"""Fixtures for the actual report decoder; execute inside approved Docker."""
import copy
import json
from pathlib import Path
import tempfile

from compare_reports import (ReportError, assert_exact_helper_guard, unhandled_log_identities,
                             compare, decode, default_failure_lines, load)


def envelope(files):
    assertions = [test for file in files for test in file["assertionResults"]]
    failures = sum(test["status"] == "failed" for test in assertions)
    suite_errors = any(file["status"] == "failed" and not file["assertionResults"] for file in files)
    return {"success": not (failures or suite_errors), "testResults": files,
            "numTotalTests": len(assertions), "numFailedTests": failures}


def must_reject(operation):
    try:
        operation()
    except ReportError:
        return
    raise AssertionError("invalid evidence was accepted")


def main():
    assert not unhandled_log_identities("passed: the test describes Unhandled Errors decoding\n  Errors 0 errors\n", "/work/source")
    for text in ("\x1b[31mUnhandled Errors\x1b[0m\n", "  Errors 1 error\n", "error during close\n"):
        assert unhandled_log_identities(text, "/work/source")
    raw = load(Path(__file__).with_name("real-vitest-records.json"))
    assert len(default_failure_lines("\n".join(raw["actual_default_failure_lines"]))) == 2
    marker = "[helper-sync] Checking the committed packaged helpers.manifest.json signature and helper bytes"
    expected = "\x1b[41m FAIL \x1b[0m __tests__/helper-signing.test.ts > shipped helpers manifest > auto-memory-hook.mjs integrity\n"
    assert_exact_helper_guard(marker, "", False)
    assert_exact_helper_guard(marker, expected, True)
    for stdout, stderr in (("native hook tests failed", expected), (marker, ""),
                           (marker, expected + " FAIL __tests__/other.test.ts > unexpected failure\n")):
        must_reject(lambda: assert_exact_helper_guard(stdout, stderr, True))
    original = envelope(raw["files"])
    baseline = decode(original, "/work/source")
    assert baseline["failures"], "fixture must exercise a real failing record"
    assert compare(baseline, decode(copy.deepcopy(original), "/work/source"))["accepted"]

    fixed = copy.deepcopy(original)
    for file in fixed["testResults"]:
        file["status"], file["message"] = "passed", ""
        for test in file["assertionResults"]:
            test["status"], test["failureMessages"] = "passed", []
    fixed["numFailedTests"], fixed["success"] = 0, True
    assert compare(baseline, decode(fixed, "/work/source"))["accepted"]

    changed = copy.deepcopy(original)
    for file in changed["testResults"]:
        for test in file["assertionResults"]:
            if test["status"] == "failed":
                test["fullName"] += " different regression despite identical failure count"
    assert not compare(baseline, decode(changed, "/work/source"))["accepted"]

    skipped = copy.deepcopy(fixed)
    skipped["testResults"][0]["assertionResults"][0]["status"] = "pending"
    assert not compare(decode(fixed, "/work/source"), decode(skipped, "/work/source"))["accepted"]

    missing = copy.deepcopy(fixed)
    missing["testResults"].pop()
    missing["numTotalTests"] = sum(len(f["assertionResults"]) for f in missing["testResults"])
    assert not compare(decode(fixed, "/work/source"), decode(missing, "/work/source"))["accepted"]

    strict = next(iter(baseline["failures"]))[0]
    assert not compare(baseline, baseline, strict_files=[strict])["accepted"]
    assert not compare(baseline, baseline, candidate_strict_files=[strict])["accepted"]
    assert compare(baseline, decode(fixed, "/work/source"), candidate_strict_files=[strict])["accepted"]
    assert not compare(baseline, baseline, strict_files=["missing.test.ts"])["accepted"]

    for mutate in (
        lambda r: r.update(numFailedTests=0),
        lambda r: r.update(numTotalTests=True),
        lambda r: r.update(success=True),
        lambda r: r.update(testResults=[]),
        lambda r: r["testResults"][0].update(name="/elsewhere/escape.test.ts"),
        lambda r: r["testResults"][0].update(name="../escape.test.ts"),
        lambda r: r["testResults"][0]["assertionResults"][0].update(status="unknown"),
    ):
        invalid = copy.deepcopy(original)
        mutate(invalid)
        must_reject(lambda: decode(invalid, "/work/source"))

    suite = envelope([{"name": "/work/source/suite.test.ts", "status": "failed",
                       "message": "Cannot import dependency at /work/source/dependency.ts",
                       "assertionResults": []}])
    same_suite = decode(suite, "/work/source")
    assert compare(same_suite, same_suite)["accepted"]
    crashed = copy.deepcopy(suite)
    crashed["testResults"][0]["message"] = "A new suite crash"
    assert not compare(same_suite, decode(crashed, "/work/source"))["accepted"]
    suite["testResults"][0]["message"] = ""
    must_reject(lambda: decode(suite, "/work/source"))

    unhandled = copy.deepcopy(fixed)
    unhandled.update(success=False, numUnhandledErrors=1,
                     unhandledErrors=[{"message": "Unhandled failure at /work/source/dependency.ts"}])
    known = decode(unhandled, "/work/source")
    assert compare(known, decode(copy.deepcopy(unhandled), "/work/source"))["accepted"]
    assert not compare(decode(fixed, "/work/source"), known)["accepted"]
    assert not compare(known, decode(dict(unhandled, numUnhandledErrors=2), "/work/source"))["accepted"]
    old_log = unhandled_log_identities("  Errors 1 error at /work/source/a.ts\n", "/work/source")
    assert not (unhandled_log_identities("  Errors 1 error at /work/source/a.ts\n", "/work/source") - old_log)
    assert unhandled_log_identities("  Errors 1 error at /work/source/b.ts\n", "/work/source") - old_log

    with tempfile.TemporaryDirectory(prefix="ruflo-report-fixture-") as directory:
        path = Path(directory) / "report.json"
        path.write_text('{"success":true,"success":false}')
        must_reject(lambda: load(path))
        path.write_text("")
        must_reject(lambda: load(path))
        path.write_text("{not JSON")
        try:
            load(path)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("malformed JSON accepted")
    print(json.dumps({"fixture": "Vitest identity decoder", "status": "PASS",
                      "actual_record_source": raw["provenance"],
                      "project_tests_executed": False}))


if __name__ == "__main__":
    main()
