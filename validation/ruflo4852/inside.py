"""Single-purpose Ruflo4852 Docker phases; invoked only by the owned controller."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time

P = Path("/packet")
W = Path("/work")
S = W / "source"
E = W / "evidence"
PLAN = json.loads((P / "execution-plan.json").read_text())
LANE, VARIANT, PHASE = os.environ["LANE"], os.environ["VARIANT"], sys.argv[1]
END = float(os.environ["ATTEMPT_DEADLINE"])
ENV = {"PATH": "/work/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/work/home",
       "USERPROFILE": "/work/home", "TMPDIR": "/work/tmp", "CI": "true", "NO_COLOR": "1",
       "RUNNER_OS": "Linux", "RUNNER_TEMP": "/work/evidence", "GITHUB_WORKSPACE": str(S),
       "GITHUB_BASE_REF": "main", "npm_config_cache": "/work/cache/npm",
       "npm_config_fetch_retries": "0", "npm_config_fetch_timeout": "20000",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
       "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
       "GIT_CONFIG_KEY_1": "safe.directory", "GIT_CONFIG_VALUE_1": str(S),
       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "DO_NOT_TRACK": "1",
       "RUFLO_DAEMON_AUTOSTART": "0", "RUFLO_NO_SKILLS_SH": "1"}


def run(label, command, cwd=S, cap=120, allowed=(0,)):
    assert label and all(c.isalnum() or c in "-_" for c in label)
    assert time.time() < END
    output, error = E / (label + ".stdout"), E / (label + ".stderr")
    start = time.monotonic()
    with output.open("xb") as out, error.open("xb") as err:
        proc = subprocess.Popen(["bash", "-e", "-o", "pipefail", "-c", command],
                                cwd=cwd, env=ENV, stdout=out, stderr=err, start_new_session=True)
        stop = None
        try:
            while proc.poll() is None:
                if time.time() >= END or time.monotonic() - start > cap:
                    raise TimeoutError("COMMAND_CAP")
                if output.stat().st_size + error.stat().st_size > 32 * 1024 * 1024:
                    raise RuntimeError("COMMAND_LOG_CAP")
                time.sleep(.1)
        except BaseException as exc:
            stop = repr(exc)
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
        receipt = {"label": label, "command": command, "cwd": str(cwd), "pid": proc.pid, "exit": proc.returncode,
                   "stop": stop, "seconds": time.monotonic() - start,
                   "stdout_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                   "stderr_sha256": hashlib.sha256(error.read_bytes()).hexdigest()}
    (E / (label + ".json")).write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
    acceptable = (0 <= proc.returncode < 128) if allowed is None else proc.returncode in allowed
    assert stop is None and acceptable, receipt
    return receipt


def source_identity():
    rows = []
    try:
        absent = not list(S.glob("vitest.config.*"))
        rows.append({"root_vitest_config_absent": absent})
        assert absent, "Do not synthesize a root Vitest config/alias"
        for name, expected in PLAN["locked_inputs"].items():
            actual = hashlib.sha256((S / name).read_bytes()).hexdigest()
            rows.append({"path": name, "expected": expected, "actual": actual})
            assert actual == expected, name
        for row in PLAN["candidate_files"]:
            path = S / row["path"]
            if VARIANT == "candidate" or row["path"].endswith("auto-memory-read-matches-4852.test.ts"):
                expected = row["sha256"]
            else:
                expected = row["baseline_sha256"]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append({"path": row["path"], "expected": expected, "actual": actual})
            assert actual == expected, row["path"]
        manifest = S / PLAN["manifest_path"]
        actual = hashlib.sha256(manifest.read_bytes()).hexdigest()
        expected = hashlib.sha256((Path("/source") / PLAN["manifest_path"]).read_bytes()).hexdigest()
        rows.append({"path": PLAN["manifest_path"], "expected": expected, "actual": actual})
        assert actual == expected
    finally:
        (E / ("source-identity-" + PHASE + ".json")).write_text(json.dumps(rows, indent=2))


def fixture():
    assert os.getuid() == 1000, "Non-root POSIX permission tests must run, not skip"
    assert os.uname().machine == "x86_64"
    run("fixture-decoder", "python3 /packet/compare_reports_fixture.py", cwd=W, cap=30)
    probe = W / "native-exec-proof"
    shutil.copyfile("/bin/true", probe)
    probe.chmod(0o755)
    run("fixture-native-work-exec", str(probe), cwd=W, cap=10)
    major = "20" if LANE == "root20" else "22"
    run("fixture-toolchain", "node --version; npm --version; python3 --version; make --version; g++ --version; git --version; "
        "test \"$(node -p process.arch)\" = x64; test \"$(node -p 'process.versions.node.split(\".\")[0]')\" = " + major,
        cwd=W, cap=20)
    run("fixture-child-success", "node -e 'process.exit(0)'", cwd=W, cap=5)
    run("fixture-child-failure", "node -e 'process.stderr.write(\"synthetic failure\\n\");process.exit(7)'",
        cwd=W, cap=5, allowed=(7,))
    try:
        run("fixture-child-timeout", "node -e 'setInterval(()=>{},1000)'", cwd=W, cap=.2)
    except AssertionError:
        receipt = json.loads((E / "fixture-child-timeout.json").read_text())
        assert receipt["stop"] and receipt["exit"] is not None
    else:
        raise AssertionError("timeout fixture did not stop and retain evidence")
    (E / "mountinfo.txt").write_text(Path("/proc/self/mountinfo").read_text())
    archive = P / "pnpm-8.15.0.tgz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PLAN["pnpm_sha256"]
    tools = W / "tools"
    tools.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert members and len(members) == 684
        for member in members:
            path = Path(member.name)
            assert not path.is_absolute() and ".." not in path.parts
            assert member.isfile() or member.isdir(), member.name
        tar.extractall(tools)
    (W / "bin").mkdir()
    wrapper = W / "bin/pnpm"
    wrapper.write_text('#!/bin/sh\nexec /usr/local/bin/node /work/tools/package/bin/pnpm.cjs "$@"\n')
    wrapper.chmod(0o755)
    run("fixture-pinned-pnpm", 'test "$(pnpm --version)" = 8.15.0', cwd=W, cap=20)


def prepare():
    assert not S.exists()
    shutil.copytree("/source", S, symlinks=True)
    for row in PLAN["candidate_files"]:
        if VARIANT == "candidate" or row["path"].endswith("auto-memory-read-matches-4852.test.ts"):
            target = S / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(P / "candidate-files" / row["path"], target)
    source_identity()
    run("source-pin", "test \"$(git rev-parse HEAD)\" = " + PLAN["source_base"] +
        "\ngit update-ref refs/remotes/origin/main " + PLAN["source_base"], cap=20)
    # The affected backend uses only Node builtins. Exercise the actual helper
    # before the large dependency install, then retain the full Vitest suites.
    project = W / "early-backend-project"
    store = project / ".claude-flow/data/auto-memory-store.json"
    store.parent.mkdir(parents=True)
    records = [{"id": "first", "key": "same", "namespace": "fixture", "content": "first"},
               {"id": "second", "key": "same", "namespace": "fixture", "content": "second"}]
    store.write_text(json.dumps(records))
    ENV["CLAUDE_PROJECT_DIR"] = str(project)
    before = (store.read_bytes(), store.stat().st_mtime_ns)
    try:
        for label, helper in [("root", ".claude/helpers/auto-memory-hook.mjs"),
                              ("package", "v3/@claude-flow/cli/.claude/helpers/auto-memory-hook.mjs")]:
            run("preinstall-matches-" + label, "node " + helper + " read-matches fixture same", cap=15)
            text = (E / ("preinstall-matches-" + label + ".stdout")).read_text()
            if VARIANT == "candidate":
                result = json.loads(text)
                assert result["matchCount"] == 2 and result["entries"] == records
            else:
                assert text.startswith("Usage: auto-memory-hook.mjs ")
            # Check the reviewed candidate bytes before even the baseline's
            # expensive install/full suite. This is the real Node/JSON backend,
            # not a replacement backend or a shipped unsigned helper route.
            run("preinstall-packet-candidate-" + label,
                "node /packet/candidate-files/" + helper + " read-matches fixture same", cap=15)
            actual = json.loads((E / ("preinstall-packet-candidate-" + label + ".stdout")).read_text())
            assert actual["matchCount"] == 2 and actual["entries"] == records
            run("preinstall-ambiguity-" + label, "node " + helper + " read fixture same", cap=15, allowed=(1,))
            refusal = json.loads((E / ("preinstall-ambiguity-" + label + ".stdout")).read_text())
            assert refusal["error"]["code"] == "AMBIGUOUS_KEY"
            assert before == (store.read_bytes(), store.stat().st_mtime_ns)
            assert sorted(p.name for p in store.parent.iterdir()) == [store.name]
    finally:
        ENV.pop("CLAUDE_PROJECT_DIR", None)


def install():
    if LANE in ("root20", "smokes22"):
        run("install-root", "npm ci --legacy-peer-deps --no-audit --no-fund", cap=300)
    if LANE != "root20":
        run("install-workspace", "pnpm install --frozen-lockfile --reporter=ndjson", cwd=S / "v3", cap=300)
    source_identity()
    run("source-delta-after-install", "git diff --name-status; git ls-files --others --exclude-standard", cap=30)


def vitest(label, files, report, full=False):
    command = "./node_modules/.bin/vitest run " + " ".join(files)
    command += " --reporter=default --reporter=json --outputFile.json=" + str(E / report)
    run(label, command, cwd=S / "v3/@claude-flow/cli", cap=360 if full else 90, allowed=(0, 1))
    assert (E / report).is_file(), "No reusable stale report is permitted"


def early():
    run("syntax-root-helper", "node --check .claude/helpers/auto-memory-hook.mjs", cap=20)
    run("syntax-package-helper", "node --check v3/@claude-flow/cli/.claude/helpers/auto-memory-hook.mjs", cap=20)
    if LANE == "cli22":
        files = ["__tests__/auto-memory-read-matches-4852.test.ts", "__tests__/auto-memory-persistence-4685.test.ts",
                 "__tests__/auto-memory-hooks-4058.test.ts", "__tests__/helper-refresh.test.ts",
                 "__tests__/doctor-helper-integrity-3565.test.ts"]
        vitest("early-real-helper-behavior", files, "early-report.json")
        from compare_reports import decode, load
        report = decode(load(E / "early-report.json"), str(S))
        new = "v3/@claude-flow/cli/__tests__/auto-memory-read-matches-4852.test.ts"
        observed = {i: s for i, s in report["tests"].items() if i[0] == new}
        assert len(observed) == 34 and all(s not in ("pending", "skipped", "todo") for s in observed.values())
        assert not report["file_errors"]
        for identity, status in report["tests"].items():
            assert status == "passed" or (VARIANT == "baseline" and identity[0] == new), identity
        if VARIANT == "baseline":
            assert sum(s == "failed" for s in observed.values()) == 33


def official_steps(group, registry=False):
    for step in PLAN["lanes"][LANE][group]:
        run(step["label"], step["command"], cwd=S / step["cwd"], cap=step["cap_seconds"],
            allowed=None if step.get("compare_exit") else tuple(step.get("allowed_exit_codes", [0])))


def offline():
    early()
    official_steps("prerequisite")
    if LANE == "cli22":
        run("early-cli-type-build", "pnpm run build", cwd=S / "v3/@claude-flow/cli", cap=120)
    official_steps("offline")
    if LANE == "cli22":
        vitest("full-cli", [], "full-report.json", full=True)
        run("exact-helper-sync", "node scripts/check-helper-sync.mjs", cap=120,
            allowed=(0,) if VARIANT == "baseline" else (1,))
        vitest("manifest-assertions", ["__tests__/helper-signing.test.ts", "--maxWorkers=1"], "manifest-report.json")
    elif LANE == "root20":
        shutil.copyfile(S / ".test-results/vitest.json", E / "full-report.json")
    elif LANE == "workspace22":
        shutil.copyfile(E / "vitest-report.json", E / "full-report.json")
    elif LANE == "smokes22":
        run("strict-cli-build-for-pack", "pnpm run build", cwd=S / "v3/@claude-flow/cli", cap=120)
        run("real-cli-pack", "npm pack --ignore-scripts --json --pack-destination /work/evidence",
            cwd=S / "v3/@claude-flow/cli", cap=120)
        manifests = json.loads((E / "real-cli-pack.stdout").read_text())
        assert isinstance(manifests, list) and len(manifests) == 1
        filename = manifests[0]["filename"]
        assert Path(filename).name == filename and filename.endswith(".tgz")
        with tarfile.open(E / filename) as packed:
            for member_name, source_path in (
                ("package/.claude/helpers/auto-memory-hook.mjs", "v3/@claude-flow/cli/.claude/helpers/auto-memory-hook.mjs"),
                ("package/.claude/helpers/helpers.manifest.json", PLAN["manifest_path"]),
            ):
                member = packed.getmember(member_name)
                assert member.isfile()
                assert packed.extractfile(member).read() == (S / source_path).read_bytes()
            helper = packed.extractfile("package/.claude/helpers/auto-memory-hook.mjs").read()
        extracted = W / "packed/.claude/helpers/auto-memory-hook.mjs"
        extracted.parent.mkdir(parents=True)
        extracted.write_bytes(helper)
        project = W / "packed-project"
        store = project / ".claude-flow/data/auto-memory-store.json"
        store.parent.mkdir(parents=True)
        entries = [{"id": "one", "namespace": "fixture", "key": "same", "content": "one"},
                   {"id": "two", "namespace": "fixture", "key": "same", "content": "two"}]
        store.write_text(json.dumps(entries))
        before = (store.read_bytes(), store.stat().st_mtime_ns)
        previous = ENV.get("CLAUDE_PROJECT_DIR")
        ENV["CLAUDE_PROJECT_DIR"] = str(project)
        try:
            run("packed-real-helper", "node /work/packed/.claude/helpers/auto-memory-hook.mjs read-matches fixture same", cap=20)
        finally:
            if previous is None:
                ENV.pop("CLAUDE_PROJECT_DIR", None)
            else:
                ENV["CLAUDE_PROJECT_DIR"] = previous
        output = (E / "packed-real-helper.stdout").read_text()
        if VARIANT == "candidate":
            value = json.loads(output)
            assert value["matchCount"] == 2 and value["entries"] == entries
        else:
            assert output.startswith("Usage: auto-memory-hook.mjs ")
        assert before == (store.read_bytes(), store.stat().st_mtime_ns)
    source_identity()
    run("source-delta-after-offline", "git diff --name-status; git ls-files --others --exclude-standard", cap=30)
    for name in ("memory.peak", "memory.events", "pids.peak"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            (E / name).write_text(path.read_text())


def registry():
    assert LANE == "smokes22"
    official_steps("registry", registry=True)
    source_identity()
    run("source-delta-after-registry", "git diff --name-status; git ls-files --others --exclude-standard", cap=30)


def compare():
    from compare_reports import (assert_exact_helper_guard, unhandled_log_identities,
                                 compare as compare_decoded, decode, load)
    assert VARIANT == "candidate"
    result = {"lane": LANE, "broad_comparison": None, "expected_signature_failure": None}
    result["command_exits"] = []
    for group in ("prerequisite", "offline", "registry"):
        for step in PLAN["lanes"][LANE][group]:
            label = step["label"]
            before = json.loads((Path("/baseline") / ("baseline-" + label + ".json")).read_text())
            after = json.loads((E / (label + ".json")).read_text())
            assert before["stop"] is None and after["stop"] is None
            result["command_exits"].append({"label": label, "baseline": before["exit"], "candidate": after["exit"]})
            assert not (before["exit"] == 0 and after["exit"] != 0), result["command_exits"][-1]
    if LANE == "cli22":
        assert_exact_helper_guard((Path("/baseline/baseline-exact-helper-sync.stdout")).read_text(),
                                  (Path("/baseline/baseline-exact-helper-sync.stderr")).read_text(), False)
        assert_exact_helper_guard((E / "exact-helper-sync.stdout").read_text(),
                                  (E / "exact-helper-sync.stderr").read_text(), True)
        baseline_manifest = decode(load(Path("/baseline/baseline-manifest-report.json")), str(S))
        candidate_manifest = decode(load(E / "manifest-report.json"), str(S))
        assert not baseline_manifest["failures"] and not baseline_manifest["file_errors"]
        assert len(candidate_manifest["failures"]) == 1 and not candidate_manifest["file_errors"]
        expected = next(iter(candidate_manifest["failures"]))
        assert expected[0] == "v3/@claude-flow/cli/__tests__/helper-signing.test.ts"
        assert expected[1].endswith("auto-memory-hook.mjs integrity")
        result["expected_signature_failure"] = expected
    # Reporter counters alone do not identify an unhandled runtime error.
    before_logs, after_logs = set(), set()
    for log in list(E.glob("*.stdout")) + list(E.glob("*.stderr")):
        after_logs.update(unhandled_log_identities(log.read_text(errors="replace"), str(S)))
    for log in list(Path("/baseline").glob("baseline-*.stdout")) + list(Path("/baseline").glob("baseline-*.stderr")):
        before_logs.update(unhandled_log_identities(log.read_text(errors="replace"), str(S)))
    result["unhandled_logs"] = {"baseline": sorted(before_logs), "candidate": sorted(after_logs),
                                "candidate_only": sorted(after_logs - before_logs)}
    assert not (after_logs - before_logs), result["unhandled_logs"]
    if LANE in ("root20", "cli22", "workspace22"):
        baseline = decode(load(Path("/baseline/baseline-full-report.json")), str(S))
        candidate = decode(load(E / "full-report.json"), str(S))
        expected = {i for i in candidate["failures"]
                    if i[0] == "v3/@claude-flow/cli/__tests__/helper-signing.test.ts"
                    and i[1].endswith("auto-memory-hook.mjs integrity")}
        # Only the explicitly reviewed signed-helper assertion is excepted;
        # the dedicated CLI lane proves its exact cause independently.
        assert len(expected) <= 1
        for identity in expected:
            assert baseline["tests"].get(identity) == "passed"
            candidate["failures"].remove(identity)
            baseline["tests"].pop(identity)
            candidate["tests"].pop(identity)
        result["broad_signature_exception"] = sorted(expected)
        strict = set()
        for identity in baseline["tests"]:
            if identity[0] == "<unhandled>":
                continue
            path = S / identity[0]
            assert path.resolve().is_relative_to(S.resolve()), "Report test path escapes source through symlink"
            if path.is_file() and "auto-memory-hook.mjs" in path.read_text(errors="replace"):
                strict.add(identity[0])
        new = "v3/@claude-flow/cli/__tests__/auto-memory-read-matches-4852.test.ts"
        strict.discard(new)
        collected = any(identity[0] == new for identity in baseline["tests"])
        result["new_test_collected_by_suite"] = collected
        assert collected or LANE != "cli22", "Full CLI must collect new regressions"
        result["broad_comparison"] = compare_decoded(baseline, candidate, strict_files=sorted(strict),
                                                     candidate_strict_files=[new] if collected else [])
    (E / "comparison.json").write_text(json.dumps(result, indent=2))
    if result["broad_comparison"] is not None:
        assert result["broad_comparison"]["accepted"], result


assert PHASE in ("fixture", "prepare", "install", "offline", "registry", "compare")
globals()[PHASE]()
