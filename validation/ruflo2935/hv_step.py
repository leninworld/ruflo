#!/usr/bin/env python3
"""Stdlib-only step runner and evidence summarizer for ruflo #2935."""

import argparse
import datetime as dt
import json
import ntpath
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hv_common import atomic_json, load_json, sha256_file

PIN = "87f612cef121b50c2e31e2d3f88e46d88963a377"
TREE = "bed85d39e668d3d919b23fd948e5276c77feaba9"
STREAM_CAP = 16 * 1024 * 1024
CMD_META = frozenset('%!^&|<>()"\r\n')


class LaunchConstructionError(RuntimeError):
    def __init__(self, message, metadata):
        super().__init__(message)
        self.metadata = metadata


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_pipe(pipe, path, state, key, cap, over_cap):
    total = 0
    with open(path, "wb") as output:
        while True:
            block = pipe.read(65536)
            if not block:
                break
            room = max(0, cap - total)
            if room:
                output.write(block[:room])
            total += len(block)
            if total > cap:
                over_cap.set()
    state[key] = total


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def build_vitest_guard(guard, targets, evidence, cwd):
    """Build the identical Bash guard path for smoke and counted phases."""
    guard = os.path.abspath(guard)
    evidence = os.path.abspath(evidence)
    env = os.environ.copy()
    env["RUNNER_TEMP"] = evidence
    source_hash = sha256_file(guard)
    effective_guard = guard
    if targets:
        needle = b"./node_modules/.bin/vitest run "
        with open(guard, "rb") as handle:
            original = handle.read()
        count = original.count(needle)
        if count != 1:
            raise RuntimeError("guard must contain exactly one Vitest command; found {}".format(count))
        quoted_targets = " ".join(shlex.quote(target) for target in targets).encode("utf-8")
        derived = original.replace(needle, needle + quoted_targets + b" ", 1)
        effective_guard = os.path.join(evidence, "smoke-guard.sh")
        with open(effective_guard, "wb") as handle:
            handle.write(derived)
    metadata = {
        "reviewed_guard": guard,
        "reviewed_guard_sha256": source_hash,
        "effective_guard": effective_guard,
        "effective_guard_sha256": sha256_file(effective_guard),
        "guard_cwd": os.path.abspath(cwd),
    }
    argv = ["bash", "--noprofile", "--norc", "-eo", "pipefail", effective_guard]
    return argv, env, os.path.abspath(cwd), metadata


def build_launch_command(argv, os_name=None, environ=None, which=shutil.which,
                         isfile=os.path.isfile):
    """Purely construct the platform launch object and its evidence metadata."""
    if not argv:
        raise RuntimeError("executable not found: empty argv")
    platform_name = os.name if os_name is None else os_name
    environment = os.environ if environ is None else environ
    requested = argv[0]
    resolved = which(requested)
    launch_argv = list(argv)
    interpreter = None
    effective = launch_argv
    metadata = {
        "requested_executable": requested,
        "resolved_executable": resolved,
        "interpreter": interpreter,
        "argv": launch_argv,
        "effective_command_line": effective,
    }
    if platform_name == "nt":
        if resolved is None:
            raise LaunchConstructionError(
                "executable not found: {}".format(requested), metadata)
        launch_argv[0] = resolved
        effective = launch_argv
        metadata.update(argv=launch_argv, effective_command_line=effective)
        if ntpath.splitext(resolved)[1].lower() in (".cmd", ".bat"):
            for argument in launch_argv:
                if not isinstance(argument, str):
                    raise LaunchConstructionError(
                        "unsafe command argument: non-string", metadata)
                if any(character in CMD_META for character in argument):
                    raise LaunchConstructionError(
                        "unsafe command argument contains cmd metacharacter", metadata)

            def existing_absolute(path):
                if not path:
                    return None
                absolute = ntpath.abspath(path)
                return absolute if ntpath.isabs(absolute) and isfile(absolute) else None

            interpreter = existing_absolute(environment.get("COMSPEC"))
            if interpreter is None:
                interpreter = existing_absolute(which("cmd.exe"))
            if interpreter is None:
                raise LaunchConstructionError(
                    "command interpreter not found", metadata)
            effective = ('"{}" /d /s /c "'.format(interpreter)
                         + subprocess.list2cmdline(launch_argv) + '"')
            metadata.update(interpreter=interpreter,
                            effective_command_line=effective)
    return metadata


def cmd_verify(args):
    evidence = os.path.abspath(args.evidence)
    os.makedirs(evidence, exist_ok=True)
    record = {"side": args.side, "repo": os.path.abspath(args.repo), "started_at": utc_now()}
    atomic_json(os.path.join(evidence, "verify-request.json"), record)
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.repo, text=True).strip()
        record["head"] = head
        if head != PIN:
            raise RuntimeError("upstream HEAD does not equal pinned commit")
        if args.side == "cand":
            subprocess.run(["git", "apply", "--index", os.path.abspath(args.patch)],
                           cwd=args.repo, check=True)
            tree = subprocess.check_output(["git", "write-tree"], cwd=args.repo, text=True).strip()
            record["tree"] = tree
            if tree != TREE:
                raise RuntimeError("candidate tree mismatch")
        record["status"] = "PASS"
        record["finished_at"] = utc_now()
        atomic_json(os.path.join(evidence, "phase.json"), record)
        return 0
    except Exception as error:
        record.update(status="HARNESS_ERROR", error=repr(error), traceback=traceback.format_exc(), finished_at=utc_now())
        atomic_json(os.path.join(evidence, "phase.json"), record)
        return 1


def cmd_run(args, os_name=None, environ=None, which=shutil.which,
            isfile=os.path.isfile):
    evidence = os.path.abspath(args.evidence)
    os.makedirs(evidence, exist_ok=True)
    requested_argv = list(args.argv)
    if requested_argv and requested_argv[0] == "--":
        requested_argv.pop(0)
    argv = list(requested_argv)
    env = os.environ.copy()
    launch_cwd = os.path.abspath(args.cwd)
    request = {"name": args.name, "requested_argv": requested_argv, "argv": argv,
               "cwd": os.path.abspath(args.cwd),
               "started_at": utc_now(), "timeout_s": args.timeout_s,
               "allow_nonzero": args.allow_nonzero, "stream_cap": args.byte_cap,
               "requested_executable": argv[0] if argv else None,
               "resolved_executable": None, "interpreter": None,
               "effective_command_line": argv}
    stdout_path = os.path.join(evidence, "stdout.log")
    stderr_path = os.path.join(evidence, "stderr.log")
    started = time.monotonic()
    phase = {}
    proc = None
    state = {}
    try:
        if argv and argv[0] == "@vitest":
            if len(argv) < 2:
                raise RuntimeError("@vitest requires guard path")
            argv, env, launch_cwd, guard_metadata = build_vitest_guard(
                argv[1], argv[2:], evidence, args.cwd)
            request.update(guard_metadata)
        launch = build_launch_command(argv, os_name=os_name, environ=environ,
                                      which=which, isfile=isfile)
        argv = launch["argv"]
        effective_command_line = launch["effective_command_line"]
        request.update(launch)
        atomic_json(os.path.join(evidence, "request.json"), request)
        phase = dict(request)
        platform_name = os.name if os_name is None else os_name
        kwargs = {"cwd": launch_cwd, "env": env, "stdin": subprocess.DEVNULL,
                  "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "shell": False}
        if platform_name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(effective_command_line, **kwargs)
        over_cap = threading.Event()
        threads = [threading.Thread(target=_read_pipe, args=(proc.stdout, stdout_path, state, "stdout", args.byte_cap, over_cap), daemon=True),
                   threading.Thread(target=_read_pipe, args=(proc.stderr, stderr_path, state, "stderr", args.byte_cap, over_cap), daemon=True)]
        for thread in threads:
            thread.start()
        timed_out = False
        deadline = time.monotonic() + args.timeout_s
        while proc.poll() is None:
            if over_cap.is_set():
                _kill_tree(proc)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_tree(proc)
                break
            time.sleep(0.05)
        if proc.poll() is None:
            proc.wait(timeout=15)
        for thread in threads:
            thread.join(timeout=15)
        capped = state.get("stdout", 0) > args.byte_cap or state.get("stderr", 0) > args.byte_cap
        phase.update(exit_code=proc.returncode, timed_out=timed_out, capped=capped,
                     stdout_bytes=state.get("stdout", 0), stderr_bytes=state.get("stderr", 0))
        if timed_out:
            phase["status"] = "TIMEOUT"
        elif capped:
            phase["status"] = "HARNESS_ERROR"
            phase["error"] = "stream byte cap exceeded"
        elif proc.returncode == 0:
            phase["status"] = "PASS"
        elif args.allow_nonzero:
            phase["status"] = "NONZERO_RECORDED"
        else:
            phase["status"] = "HARNESS_ERROR"
        rc = 0 if phase["status"] in ("PASS", "NONZERO_RECORDED") else 1
    except Exception as error:
        if isinstance(error, LaunchConstructionError):
            request.update(error.metadata)
        request["argv"] = argv
        if request.get("requested_executable") is None and argv:
            request["requested_executable"] = argv[0]
        atomic_json(os.path.join(evidence, "request.json"), request)
        if not phase:
            phase = dict(request)
        if proc is not None:
            _kill_tree(proc)
        for path in (stdout_path, stderr_path):
            if not os.path.exists(path):
                open(path, "ab").close()
        error_text = str(error) if isinstance(error, LaunchConstructionError) else repr(error)
        phase.update(status="HARNESS_ERROR", error=error_text, traceback=traceback.format_exc())
        rc = 1
    phase["elapsed_s"] = round(time.monotonic() - started, 3)
    phase["finished_at"] = utc_now()
    atomic_json(os.path.join(evidence, "phase.json"), phase)
    return rc


def _phase_status(directory):
    path = os.path.join(directory, "phase.json")
    if not os.path.exists(path):
        return {"status": "MISSING", "path": os.path.basename(directory)}
    try:
        data = load_json(path)
        status = data.get("status")
        if status not in ("PASS", "NONZERO_RECORDED", "TIMEOUT", "HARNESS_ERROR"):
            status = "HARNESS_ERROR"
        return {"status": status, "path": os.path.basename(directory), "phase": data}
    except Exception as error:
        return {"status": "HARNESS_ERROR", "path": os.path.basename(directory), "error": repr(error)}


def cmd_summarize(args):
    root = os.path.abspath(args.evidence)
    os.makedirs(root, exist_ok=True)
    summary = {"schema": "ruflo2935-summary/1", "job": args.job, "generated_at": utc_now(), "phases": {}}
    try:
        expected = {
            "linux-full-base": ("source", "install", "mcp-policy", "build", "flywheel", "replay", "smoke", "counted"),
            "linux-full-cand": ("source", "install", "mcp-policy", "build", "flywheel", "replay", "smoke", "counted", "focused"),
            "node20-build-typecheck-base": ("source", "install", "build", "typecheck"),
            "node20-build-typecheck-cand": ("source", "install", "build", "typecheck", "pack"),
            "macos-cand": ("source", "install", "build", "focused", "probe", "candidate-probe"),
            "windows-cand": ("source", "install", "build", "focused", "candidate-probe"),
        }.get(args.job, ())
        names = set(expected)
        names.update(name for name in os.listdir(root)
                     if os.path.isdir(os.path.join(root, name)) and not os.path.islink(os.path.join(root, name)))
        for name in sorted(names):
            path = os.path.join(root, name)
            summary["phases"][name] = _phase_status(path)
        bad = [row["status"] for row in summary["phases"].values()
               if row["status"] in ("MISSING", "TIMEOUT", "HARNESS_ERROR")]
        summary["job_outcome"] = "HARNESS_ERROR" if bad else "COMPLETE"
        if args.require_smoke:
            smoke = summary["phases"].get("smoke", {"status": "MISSING"})
            try:
                report = load_json(os.path.join(root, "smoke", "vitest-report.json"))
                if smoke["status"] != "PASS" or not isinstance(report.get("numTotalTests"), int) or report["numTotalTests"] <= 0:
                    raise ValueError("smoke did not pass with numTotalTests > 0")
                summary["smoke_gate"] = "PASS"
            except Exception as error:
                summary["smoke_gate"] = "HARNESS_SMOKE_FAILED"
                summary["smoke_error"] = repr(error)
                summary["job_outcome"] = "HARNESS_SMOKE_FAILED"
    except Exception as error:
        summary["job_outcome"] = "HARNESS_ERROR"
        summary["exception"] = repr(error)
        summary["traceback"] = traceback.format_exc()
    try:
        atomic_json(os.path.join(root, "summary.json"), summary)
        rows = []
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(current, d)))
            for name in sorted(files):
                path = os.path.join(current, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                if rel == "sha256.txt" or os.path.islink(path):
                    continue
                rows.append(sha256_file(path) + "  " + rel)
        with open(os.path.join(root, "sha256.txt"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(rows) + "\n")
    except Exception as error:
        summary["job_outcome"] = "HARNESS_ERROR"
        summary["index_exception"] = repr(error)
        try:
            atomic_json(os.path.join(root, "summary.json"), summary)
        except Exception:
            pass
    return 1 if args.require_smoke and summary.get("smoke_gate") != "PASS" else 0


def parser():
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-source")
    verify.add_argument("--side", choices=("base", "cand"), required=True)
    verify.add_argument("--repo", required=True)
    verify.add_argument("--patch")
    verify.add_argument("--evidence", required=True)
    verify.set_defaults(func=cmd_verify)
    run = sub.add_parser("run-phase")
    run.add_argument("--name", required=True)
    run.add_argument("--cwd", required=True)
    run.add_argument("--timeout-s", type=float, required=True)
    run.add_argument("--evidence", required=True)
    run.add_argument("--allow-nonzero", action="store_true")
    run.add_argument("--byte-cap", type=int, default=STREAM_CAP)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)
    summary = sub.add_parser("summarize")
    summary.add_argument("--evidence", required=True)
    summary.add_argument("--job", required=True)
    summary.add_argument("--require-smoke", action="store_true")
    summary.set_defaults(func=cmd_summarize)
    return top


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
