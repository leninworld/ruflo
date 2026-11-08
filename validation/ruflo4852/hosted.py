"""Owned GitHub-runner Docker controller. Not authorized merely by this file.

The workflow seals dispatch_epoch once. All jobs share its 50-minute target;
queue time counts, and there is no retry or deadline reset.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

GIB = 1024 ** 3
PACKET = Path(__file__).resolve().parent
PLAN = json.loads((PACKET / "execution-plan.json").read_text())
assert hashlib.sha256((PACKET / "execution-plan.json").read_bytes()).hexdigest() == os.environ["APPROVED_PLAN_SHA256"]
LANE = sys.argv[1]
assert LANE in PLAN["lanes"]
assert os.environ["GITHUB_REPOSITORY"] == "leninworld/ruflo"
assert os.environ["RUNNER_OS"] == "Linux" and platform.machine() == "x86_64"
assert os.environ["RUNNER_ENVIRONMENT"] == "github-hosted"
assert os.environ["GITHUB_SHA"] == os.environ["APPROVED_DIAGNOSTIC_COMMIT"]
assert os.environ["GITHUB_RUN_ID"].isdigit()
assert os.environ["GITHUB_RUN_ATTEMPT"] == "1", "No unapproved GitHub rerun"
assert os.environ["ATTEMPT_ID"] == "ruflo4852-i3-e1"
assert os.environ["GITHUB_REF"] == "refs/heads/validate-native-read-matches-4852-i3"
assert os.environ["GITHUB_EVENT_NAME"] == "push"
ORIGIN = int(subprocess.run(["git", "-C", str(PACKET.parent.parent), "log", "-1", "--format=%ct", "HEAD"], capture_output=True, text=True, check=True, timeout=10).stdout)
assert subprocess.run(["git", "-C", str(PACKET.parent.parent), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10).stdout.strip() == os.environ["GITHUB_SHA"]
assert -120 <= time.time() - ORIGIN < 3000
DEADLINE = min(ORIGIN + 3000, time.time() + PLAN["lanes"][LANE]["lane_window_seconds"])
ROOT = Path(os.environ["RUNNER_TEMP"]) / ("ruflo4852-" + os.environ["GITHUB_RUN_ID"] + "-" + LANE)
ROOT.mkdir(exist_ok=False)
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir()
SOURCE = Path(os.environ["GITHUB_WORKSPACE"]) / "upstream"
OWNED = []


def capture(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True,
                            timeout=min(timeout, max(1, DEADLINE - time.time())), check=True)
    return result.stdout.strip()


def available_memory():
    rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(rows["MemAvailable"].split()[0]) * 1024


def gate():
    values = {"epoch": time.time(), "nproc": int(capture(["nproc"])),
              "MemAvailable": available_memory(), "disk_free": shutil.disk_usage(ROOT).free,
              "ImageOS": os.environ.get("ImageOS"), "ImageVersion": os.environ.get("ImageVersion")}
    with (EVIDENCE / "admission.jsonl").open("a") as out:
        out.write(json.dumps(values) + "\n")
    assert values["epoch"] < DEADLINE, "OWNED_TARGET_REACHED"
    assert values["nproc"] >= 4
    assert values["MemAvailable"] >= 12 * GIB, "HOST_MEMORY_ADMISSION"
    assert values["disk_free"] >= 16 * GIB, "HOST_DISK_ADMISSION"


def verify_inputs():
    assert capture(["git", "-C", str(SOURCE), "rev-parse", "HEAD"]) == PLAN["source_base"]
    assert (SOURCE / ".git").is_dir()
    assert not (SOURCE / ".git/objects/info/alternates").exists()
    config_names = capture(["git", "-C", str(SOURCE), "config", "--name-only", "--list"])
    assert not any("extraheader" in key.lower() or key.lower().startswith("credential.")
                   for key in config_names.splitlines()), "SOURCE_GIT_AUTH_CONFIG"
    for row in PLAN["packet_files"]:
        path = PACKET / row["path"]
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], row["path"]
    for name, expected in PLAN["locked_inputs"].items():
        assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == expected, name


def stop(container):
    # Only IDs returned by this invocation's docker create reach this function.
    if container not in OWNED:
        raise RuntimeError("unowned container")
    confirmed = False
    try:
        subprocess.run(["docker", "stop", "--time", "5", container], capture_output=True, timeout=15, check=False)
        result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", container],
                                capture_output=True, text=True, timeout=20, check=False)
        confirmed = result.returncode == 0 and json.loads(result.stdout).get("Running") is False
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    with (EVIDENCE / "stop-receipts.jsonl").open("a") as out:
        out.write(json.dumps({"epoch": time.time(), "container": container, "stopped_confirmed": confirmed}) + "\n")
    return confirmed


def phase(container, variant, name):
    out = EVIDENCE / (variant + "-" + name + ".stdout")
    err = EVIDENCE / (variant + "-" + name + ".stderr")
    begin = time.monotonic()
    failure = None
    last_disk_check = begin
    with out.open("xb") as stdout, err.open("xb") as stderr:
        proc = subprocess.Popen(["docker", "exec", container, "python3", "/packet/inside.py", name],
                                stdout=stdout, stderr=stderr)
        try:
            while proc.poll() is None:
                if time.time() >= DEADLINE or time.monotonic() - begin > PLAN["lanes"][LANE]["phase_caps"][name]:
                    raise TimeoutError("OWNED_PHASE_DEADLINE")
                if out.stat().st_size + err.stat().st_size > 32 * 1024 * 1024:
                    raise RuntimeError("OWNED_PHASE_LOG_CAP")
                if shutil.disk_usage(ROOT).free < 4 * GIB:
                    raise RuntimeError("OWNED_DISK_RESERVE")
                if time.monotonic() - last_disk_check >= 5:
                    try:
                        measured = subprocess.run(["sudo", "du", "-s", "--block-size=1", "--",
                                                   str(ROOT / (variant + "-work"))],
                                                  capture_output=True, text=True, timeout=10)
                        if measured.returncode == 0:
                            assert int(measured.stdout.split()[0]) <= 12 * GIB, "OWNED_WORK_CAP"
                        else:
                            lines = measured.stderr.splitlines()
                            assert measured.returncode == 1 and lines and all("No such file or directory" in l for l in lines), measured.stderr
                            with (EVIDENCE / "sampling-misses.jsonl").open("a") as out:
                                out.write(json.dumps({"epoch": time.time(), "stderr": measured.stderr[:4096]}) + "\n")
                    except subprocess.TimeoutExpired:
                        with (EVIDENCE / "sampling-misses.jsonl").open("a") as out:
                            out.write(json.dumps({"operation": "du", "error": "timeout"}) + "\n")
                    try:
                        stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
                                               capture_output=True, text=True, check=False, timeout=15)
                        if stats.returncode != 0:
                            raise ValueError("stats exit " + str(stats.returncode) + ": " + stats.stderr[:2048])
                        value = json.loads(stats.stdout)
                        with (EVIDENCE / (variant + "-stats.jsonl")).open("a") as out:
                            out.write(json.dumps({"epoch": time.time(), "observed": value}) + "\n")
                    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                        with (EVIDENCE / "sampling-misses.jsonl").open("a") as out:
                            out.write(json.dumps({"epoch": time.time(), "operation": "docker stats", "error": str(exc)[:4096]}) + "\n")
                    last_disk_check = time.monotonic()
                time.sleep(.5)
        except BaseException as exc:
            failure = repr(exc)
            stop(container)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    receipt = {"variant": variant, "phase": name, "exit": proc.returncode,
               "stop": failure,
               "seconds": time.monotonic() - begin,
               "stdout_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
               "stderr_sha256": hashlib.sha256(err.read_bytes()).hexdigest()}
    (EVIDENCE / (variant + "-" + name + "-receipt.json")).write_text(json.dumps(receipt, indent=2))
    assert failure is None and proc.returncode == 0, receipt


def main():
    gate()
    verify_inputs()
    image = PLAN["lanes"][LANE]["image"]
    capture(["docker", "pull", "--platform", "linux/amd64", image], timeout=240)
    for variant in ("baseline", "candidate"):
        gate()
        work = ROOT / (variant + "-work")
        work.mkdir()
        for sub in ("home", "tmp", "evidence"):
            (work / sub).mkdir()
        # Fresh, isolated hosted-runner directory only; never a Mac/external path.
        subprocess.run(["sudo", "chown", "-R", "1000:1000", str(work)], check=True, timeout=20)
        args = ["docker", "create", "--platform", "linux/amd64", "--read-only",
                "--user", "1000:1000", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--network", "bridge",
                "--memory", str(10 * GIB), "--memory-swap", str(10 * GIB),
                "--cpus", "4", "--pids-limit", "1024",
                "--log-driver", "local", "--log-opt", "max-size=1m",
                "--log-opt", "max-file=1", "--log-opt", "compress=false",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=67108864",
                "--mount", "type=bind,src=" + str(PACKET) + ",dst=/packet,readonly",
                "--mount", "type=bind,src=" + str(SOURCE) + ",dst=/source,readonly",
                "--mount", "type=bind,src=" + str(EVIDENCE) + ",dst=/baseline,readonly",
                "--mount", "type=bind,src=" + str(work) + ",dst=/work",
                "--env", "LANE=" + LANE, "--env", "VARIANT=" + variant,
                "--env", "ATTEMPT_DEADLINE=" + str(DEADLINE),
                image, "sleep", "3600"]
        container = capture(args)
        assert len(container) == 64 and all(c in "0123456789abcdef" for c in container)
        OWNED.append(container)
        capture(["docker", "start", container])
        try:
            phase(container, variant, "fixture")
            capture(["docker", "network", "disconnect", "bridge", container])
            networks = json.loads(capture(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container]))
            assert networks == {}, "NETWORK_FIXTURE_DISCONNECT"
            capture(["docker", "network", "connect", "bridge", container])
            networks = json.loads(capture(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container]))
            assert set(networks) == {"bridge"}, "NETWORK_FIXTURE_RECONNECT"
            for name in ("prepare", "install"):
                phase(container, variant, name)
            # Start connected, disconnect once; never try to attach a `none` container.
            capture(["docker", "network", "disconnect", "bridge", container])
            phase(container, variant, "offline")
            # Registry-backed package audits only, after all offline product tests.
            if LANE == "smokes22":
                capture(["docker", "network", "connect", "bridge", container])
                phase(container, variant, "registry")
            if variant == "candidate":
                phase(container, variant, "compare")
        finally:
            stopped = stop(container)
            try:
                inspected = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, timeout=20)
                (EVIDENCE / (variant + "-container.json")).write_text(inspected.stdout)
            finally:
                for path in (work / "evidence").glob("*"):
                    if path.is_file():
                        shutil.copyfile(path, EVIDENCE / (variant + "-" + path.name))
            # Evidence is sealed first. Reuse disk by removing only this fresh,
            # stopped job's disposable source/dependencies, never Docker data.
            assert stopped, "STOP_UNCONFIRMED_PRESERVE_WORK"
            subprocess.run(["sudo", "rm", "-rf", "--", str(work)], check=True, timeout=90)
    print(json.dumps({"lane": LANE, "state": "TERMINAL_REVIEW_RESULTS_REQUIRED",
                      "evidence": str(EVIDENCE), "project_pass_claim": False}))


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Owned runner interrupted: " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    finally:
        confirmations = []
        for owned in OWNED:
            confirmations.append(stop(owned))
        index = []
        for path in sorted(EVIDENCE.iterdir()):
            if path.is_file() and path.name != "evidence-index.json":
                row = {"name": path.name, "bytes": path.stat().st_size,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                index.append(row)
                print(json.dumps({"sealed_evidence": row}), flush=True)
        (EVIDENCE / "evidence-index.json").write_text(json.dumps(index, indent=2))
        assert all(confirmations), "Owned stop not confirmed; evidence retained, no success claim"
