"""Decode native Linux Vitest reports; compare test identities, never just counts.

Run only as part of the reviewed hosted Docker packet. Fixtures include retained
real Vitest records; a successful parser fixture is not a project-test pass.
"""
import collections
import json
import re
from pathlib import Path, PurePosixPath


class ReportError(ValueError):
    pass


def unhandled_log_identities(text, root):
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    identities = set()
    for line in text.splitlines():
        if re.search(r"^\s*(?:Unhandled Errors|Errors\s+[1-9][0-9]*\s+errors?|error during close)", line):
            identities.add(line.strip().replace(root, "<SOURCE>"))
    return identities


def default_failure_lines(text):
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return re.findall(r"(?m)^\s*FAIL\s+(.+)$", text)


def assert_exact_helper_guard(stdout, stderr, candidate):
    marker = "[helper-sync] Checking the committed packaged helpers.manifest.json signature and helper bytes"
    if marker not in stdout:
        raise ReportError("exact guard did not reach signature tests after native hook tests")
    failures = default_failure_lines(stdout + "\n" + stderr)
    if candidate:
        if not (len(failures) == 1 and "__tests__/helper-signing.test.ts" in failures[0]
                and failures[0].endswith("auto-memory-hook.mjs integrity")):
            raise ReportError("exact guard failed for an unapproved cause: " + repr(failures)[:2048])
    elif failures:
        raise ReportError("baseline exact guard has test failures")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load(path):
    path = Path(path)
    if not 0 < path.stat().st_size <= 128 * 1024 * 1024:
        raise ReportError("missing/empty/oversized report")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def integer(report, key):
    value = report.get(key)
    if type(value) is not int or value < 0:
        raise ReportError("invalid report counter " + key)
    return value


def relative_name(name, root):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ReportError("invalid native Linux test filename")
    path = PurePosixPath(name)
    if ".." in path.parts:
        raise ReportError("test filename traverses directories")
    if path.is_absolute():
        try:
            path = path.relative_to(PurePosixPath(root))
        except ValueError as exc:
            raise ReportError("test filename outside source root") from exc
    if str(path) in ("", "."):
        raise ReportError("empty relative test filename")
    return str(path)


def decode(report, root):
    if not isinstance(report, dict) or type(report.get("success")) is not bool:
        raise ReportError("invalid report envelope")
    files = report.get("testResults")
    if not isinstance(files, list) or not files:
        raise ReportError("report contains no test files")
    tests, failures, diagnostics, file_errors = {}, set(), {}, []
    for key in ("numRuntimeErrorTestSuites", "numUnhandledErrors"):
        if key in report and integer(report, key):
            failures.add(("<unhandled>", key, integer(report, key)))
    errors = report.get("unhandledErrors", [])
    if not isinstance(errors, list):
        raise ReportError("invalid unhandledErrors")
    for error in errors:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise ReportError("unhandled error lacks message identity")
        failures.add(("<unhandled>", "message", error["message"].replace(root, "<SOURCE>")))
    occurrences = collections.Counter()
    for file in files:
        if not isinstance(file, dict):
            raise ReportError("invalid test-file record")
        name = relative_name(file.get("name"), root)
        assertions = file.get("assertionResults")
        if not isinstance(assertions, list):
            raise ReportError("missing assertionResults")
        file_failed = False
        for test in assertions:
            if not isinstance(test, dict):
                raise ReportError("invalid test record")
            full_name, status = test.get("fullName"), test.get("status")
            if not isinstance(full_name, str) or not full_name:
                raise ReportError("test has no fullName")
            if status not in ("passed", "failed", "pending", "todo", "skipped"):
                raise ReportError("unknown test status")
            # Retain separate instances when upstream parameter names repeat.
            occurrences[(name, full_name)] += 1
            identity = (name, full_name, occurrences[(name, full_name)])
            tests[identity] = status
            if status == "failed":
                file_failed = True
                failures.add(identity)
                messages = test.get("failureMessages", [])
                if not isinstance(messages, list) or any(not isinstance(m, str) for m in messages):
                    raise ReportError("invalid failureMessages")
                diagnostics[identity] = "\n".join(messages)[:4096]
        if file.get("status") not in ("passed", "failed", "pending", "skipped"):
            raise ReportError("unknown test-file status")
        message = file.get("message", "")
        if not isinstance(message, str):
            raise ReportError("invalid suite diagnostic")
        if file.get("status") == "failed" and (message or not file_failed):
            if not message:
                raise ReportError("failed suite lacks identifiable diagnostic")
            message = message.replace(root, "<SOURCE>")
            identity = (name, "<suite-error>", message)
            failures.add(identity)
            file_errors.append(identity)
    if len(tests) != integer(report, "numTotalTests"):
        raise ReportError("test count differs from actual assertion records")
    failed_tests = sum(status == "failed" for status in tests.values())
    if failed_tests != integer(report, "numFailedTests"):
        raise ReportError("failed-test count differs from assertion records")
    if report["success"] and failures:
        raise ReportError("report claims success despite failures")
    if not report["success"] and not failures:
        raise ReportError("failed report without an identified failure")
    return {"tests": tests, "failures": failures, "file_errors": file_errors,
            "diagnostics": diagnostics, "reported_success": report["success"]}


def compare(baseline, candidate, strict_files=(), candidate_strict_files=()):
    # A crash is retained as a named file/message identity. Any missing report,
    # process timeout or unsupported abnormal exit must be rejected by caller.
    added = candidate["failures"] - baseline["failures"]
    missing_tests = set(baseline["tests"]) - set(candidate["tests"])
    degraded = {identity for identity, status in baseline["tests"].items()
                if status == "passed" and candidate["tests"].get(identity) != "passed"}
    strict_errors = []
    for file in strict_files:
        for label, report in (("baseline", baseline), ("candidate", candidate)):
            selected = {i: s for i, s in report["tests"].items() if i[0] == file}
            if not selected or any(s != "passed" for s in selected.values()):
                strict_errors.append((label, file, "missing, skipped or failing strict tests"))
            if any(i[0] == file for i in report["file_errors"]):
                strict_errors.append((label, file, "strict suite error"))
    # New regressions are deliberately overlaid onto baseline for fail-before
    # proof. A3 requires all of them to pass on candidate, not on old code.
    for file in candidate_strict_files:
        selected = {i: s for i, s in candidate["tests"].items() if i[0] == file}
        if not selected or any(s != "passed" for s in selected.values()):
            strict_errors.append(("candidate", file, "missing, skipped or failing new tests"))
        if any(i[0] == file for i in candidate["file_errors"]):
            strict_errors.append(("candidate", file, "new suite error"))
    return {"accepted": not (added or missing_tests or degraded or strict_errors),
            "candidate_only_failures": sorted(added, key=repr),
            "missing_candidate_tests": sorted(missing_tests, key=repr),
            "passed_tests_degraded": sorted(degraded, key=repr),
            "strict_errors": strict_errors,
            "baseline_failure_count": len(baseline["failures"]),
            "candidate_failure_count": len(candidate["failures"])}
