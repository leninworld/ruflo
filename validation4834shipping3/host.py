"""One hosted-only orchestration entry. Never run on the user's Mac."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

P = Path(__file__).resolve().parent
M = json.loads((P / 'manifest.json').read_text())
ORIGIN = int(os.environ['PACKET_ORIGIN'])
DEADLINE = ORIGIN + M['whole_deadline_seconds']
LABEL = sys.argv[1]
PROFILE = os.environ.get('PROFILE', '')
VARIANT = os.environ.get('VARIANT', '')
assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'HOSTED_ONLY'
assert os.environ.get('GITHUB_REPOSITORY') == 'leninworld/ruflo', 'PERSONAL_FORK_ONLY'
assert os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'NO_RERUN_AUTHORITY'
assert M['status'] == 'SEALED_FOR_ONE_RUN', 'UNAPPROVED_DRAFT'
assert LABEL == 'full' and PROFILE == 'shipping22' and M['attempt'] == 3
if LABEL == 'full':
    assert PROFILE in M['profiles'] and VARIANT in ['baseline', 'candidate']
name_suffix = 'd0' if LABEL == 'd0' else PROFILE + '-' + VARIANT
BASE = Path(os.environ['RUNNER_TEMP']).resolve()
W = BASE / ('ruflo4834-' + name_suffix)
assert not W.exists(), 'NEVER_OVERWRITE_PREVIOUS_ATTEMPT'
W.mkdir()
E = W / 'evidence'; E.mkdir()
CACHE = BASE / 'ruflo4834-cache'
assert str(CACHE).startswith(str(BASE) + '/') and not CACHE.is_symlink()
CACHE.mkdir(exist_ok=True)
for name in ['npm20', 'npm22', 'pnpm']:
    (CACHE / name).mkdir(exist_ok=True)
sequence = 0
containers = []
initial_free = None
baseline_owned = None
observed_owned_peak = 0


def run(label, args, seconds=60, expected=(0,), cleanup=False):
    global sequence
    sequence += 1
    key = f'{sequence:04d}-' + label
    limit = seconds if cleanup else min(seconds, DEADLINE - time.time() - 90,
        ORIGIN + 900 - time.time() if LABEL == 'd0' else float('inf'))
    assert limit > 0, 'COMMON_DEADLINE'
    record = {'argv': list(map(str, args)), 'limit_seconds': limit}
    started = time.monotonic()
    try:
        with (E / (key + '.stdout')).open('xb') as out, (E / (key + '.stderr')).open('xb') as err:
            process = subprocess.run(list(map(str, args)), stdout=out, stderr=err, timeout=limit)
        record['exit'] = process.returncode
        assert process.returncode in expected, (key, process.returncode)
    except BaseException as error:
        record['error'] = repr(error)
        raise
    finally:
        record['seconds'] = time.monotonic() - started
        (E / (key + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    return (E / (key + '.stdout')).read_text(errors='replace')


def capacity(required_seconds):
    values = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    disk = os.statvfs(W).f_bavail * os.statvfs(W).f_frsize
    assert values['MemAvailable'] >= 13.5 * 2**30 and values['MemTotal'] >= 15 * 2**30, 'HOST_MEMORY_GATE'
    assert len(os.sched_getaffinity(0)) >= 4, 'HOST_CPU_GATE'
    assert disk >= M['initial_free_GiB'] * 2**30, 'INITIAL_DISK_GATE'
    assert DEADLINE - time.time() >= required_seconds, 'ORIGINAL_CLOCK_INSUFFICIENT'
    return {'memory': values, 'free_bytes': disk, 'cpu_affinity': sorted(os.sched_getaffinity(0)),
            'remaining_seconds': DEADLINE - time.time(), 'required_seconds': required_seconds}


def check_running(name):
    state = json.loads(run('inspect', ['docker', 'inspect', name], 15))[0]
    assert state['State']['Running'] and not state['State'].get('OOMKilled'), 'CONTAINER_NOT_RUNNING'
    return state


def container_phase(name, phase, phase_cap):
    # Monitor the real foreground docker-exec process. Docker CLI inspect does
    # not support exec IDs, so no detached-exec pseudo-polling is used.
    global sequence, observed_owned_peak
    sequence += 1
    key = f'{sequence:04d}-exec-' + phase
    began = time.monotonic()
    with (E / (key + '.stdout')).open('xb') as out, (E / (key + '.stderr')).open('xb') as err:
        process = subprocess.Popen(['docker', 'exec', name, 'python3', '/inputs/inside.py', phase], stdout=out, stderr=err)
        try:
            while process.poll() is None:
                check_running(name)
                assert time.monotonic() - began < phase_cap and time.time() < DEADLINE - 180, 'PHASE_OR_COMMON_DEADLINE'
                assert LABEL != 'd0' or time.time() < ORIGIN + 900, 'D0_ORIGINAL_HARD_CEILING'
                free = os.statvfs(W).f_bavail * os.statvfs(W).f_frsize
                assert free >= 3 * 2**30, 'DISK_FREE_FLOOR'
                # A runner is exclusive to this job. Filesystem free-space
                # delta includes images and bind/cache writes without racing
                # package-manager renames or walking inaccessible temp dirs.
                owned = baseline_owned + max(0, initial_free - free)
                observed_owned_peak = max(observed_owned_peak, owned)
                assert owned <= M['owned_peak_GiB'] * 2**30, 'OWNED_DISK_GROWTH'
                logs = 0
                for child_evidence in [E, *W.glob('*/evidence')]:
                    for f in child_evidence.rglob('*'):
                        try:
                            if f.is_file() and not f.is_symlink():
                                logs += f.stat().st_size
                        except FileNotFoundError:
                            pass  # Only a sampling race, not a missing result.
                assert logs <= 256 * 1024**2, 'EVIDENCE_CAP'
                run('stats', ['docker', 'stats', '--no-stream', '--format', '{{json .}}', name], 15)
                time.sleep(5)
            assert process.returncode == 0, ('PHASE_FAILED', phase, process.returncode)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            (E / (key + '.json')).write_text(json.dumps({'exit': process.returncode,
                'seconds': time.monotonic() - began, 'phase_cap': phase_cap}) + '\n')


images = {}


def execute(profile, variant, mode):
    node = M['profiles'][profile]['node']
    image = M['images'][node]['image']
    child = W / (profile + '-' + variant)
    child.mkdir()
    (child / 'evidence').mkdir()
    # Official node image has a real passwd entry for UID1000. Only this job's
    # new public work/cache directories are re-owned, never any Mac path.
    run('own-new-public-work', ['sudo', 'chown', '-R', '1000:1000', child, CACHE], 60)
    run('pull-image', ['docker', 'pull', '--platform', 'linux/amd64', image], 180)
    description = json.loads(run('image-inspect', ['docker', 'image', 'inspect', image], 15))[0]
    assert description['Architecture'] == 'amd64' and description['Os'] == 'linux'
    images[image] = description['Size']
    name = 'ruflo4834-' + os.environ['GITHUB_RUN_ID'] + '-' + mode + '-' + profile + '-' + variant
    args = ['docker', 'create', '--name', name, '--platform', 'linux/amd64', '--init',
            '--user', '1000:1000', '--memory', '12g', '--memory-swap', '12g', '--pids-limit', '1024',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--env', 'USER=node', '--env', 'LOGNAME=node', '--env', 'PACKET_ORIGIN=' + str(ORIGIN),
            '--env', 'PROFILE=' + profile, '--env', 'VARIANT=' + variant, '--env', 'PACKET_MODE=' + mode,
            '--mount', 'type=bind,src=' + str(P) + ',dst=/inputs,readonly',
            '--mount', 'type=bind,src=' + str(child) + ',dst=/work',
            '--mount', 'type=bind,src=' + str(CACHE / ('npm' + node)) + ',dst=/cache/npm',
            '--mount', 'type=bind,src=' + str(CACHE / 'pnpm') + ',dst=/cache/pnpm',
            image, 'sleep', 'infinity']
    run('create', args)
    containers.append(name)
    run('start', ['docker', 'start', name])
    state = check_running(name)
    assert state['HostConfig']['Memory'] == state['HostConfig']['MemorySwap'] == 12 * 2**30
    assert state['HostConfig']['NanoCpus'] == 0 and not state['HostConfig']['CpusetCpus'], 'NO_CPU_OR_WORKER_THROTTLING'
    container_phase(name, 'prepare', 900 if mode == 'd0' else 600)
    run('isolate-network', ['docker', 'network', 'disconnect', 'bridge', name], 15)
    state = check_running(name)
    assert not state['NetworkSettings']['Networks'], 'TEST_NETWORK_NOT_NONE'
    container_phase(name, 'test', 900 if mode == 'd0' else 1500)
    if mode == 'full' and profile == 'shipping22':
        run('shipping-cache-network', ['docker', 'network', 'connect', 'bridge', name], 15)
        container_phase(name, 'shipping-warm', 300)
        run('shipping-offline-network', ['docker', 'network', 'disconnect', 'bridge', name], 15)
        state = check_running(name)
        assert not state['NetworkSettings']['Networks'], 'SHIPPING_NETWORK_NOT_NONE'
        container_phase(name, 'shipping-offline', 360)
    run('stop-completed', ['docker', 'stop', '--time', '10', name], 25)
    final = json.loads(run('completed-state', ['docker', 'inspect', name], 15))[0]['State']
    assert not final['OOMKilled'], 'OOM_REJECTED'
    path = child / 'evidence/terminal.json'
    terminal = json.loads(path.read_text())
    assert terminal['state'] in ['COMPLETE', 'COMPLETE_WITH_FAILURES'], terminal
    return terminal


try:
    assert not list(W.glob('source*'))
    admission = capacity(1650)
    initial_free = admission['free_bytes']
    measured = run('existing-public-owned-blocks', ['du', '-sk', CACHE, os.environ['GITHUB_WORKSPACE']], 60)
    baseline_owned = sum(int(line.split()[0]) * 1024 for line in measured.splitlines())
    admission['initial_owned_blocks_bytes'] = baseline_owned
    (E / 'admission.json').write_text(json.dumps(admission, indent=2) + '\n')
    if LABEL == 'd0':
        outcomes = []
        for profile in ['root20', 'cli22']:
            outcomes.append(execute(profile, 'baseline', 'd0'))
            assert time.time() < ORIGIN + 900, 'D0_HARD_CEILING'
        assert DEADLINE - time.time() >= 2430, 'NO_FULL_RUN_TIME_REMAINING_AFTER_CACHE_HANDOFF'
        assert all(x['D0_admitted'] for x in outcomes), 'D0_MEMORY_OR_SCOPE_NOT_ADMITTED'
        (E / 'd0-admission.json').write_text(json.dumps({'admitted': True, 'outcomes': outcomes,
                                                     'origin': ORIGIN, 'elapsed': time.time() - ORIGIN}, indent=2) + '\n')
    else:
        execute(PROFILE, VARIANT, 'full')
    (E / 'host-terminal.json').write_text(json.dumps({'state': 'COMPLETE', 'origin': ORIGIN,
                                                   'elapsed': time.time() - ORIGIN,
                                                   'sampled_owned_peak_bytes': observed_owned_peak,
                                                   'disk_basis': 'preexisting cache/workspace blocks plus exclusive-runner free-space delta'}) + '\n')
finally:
    cleanup_errors = []
    for name in containers:
        # Stop only owned live processes; never rm/prune/reset any Docker object.
        for label, args in [('owned-stop', ['docker', 'stop', '--time', '10', name]),
                            ('final-inspect', ['docker', 'inspect', name]),
                            ('final-logs', ['docker', 'logs', name])]:
            try:
                run(label, args, 25, expected=(0,), cleanup=True)
            except Exception as error:
                cleanup_errors.append({'container': name, 'action': label, 'error': repr(error)})
    (E / 'cleanup-result.json').write_text(json.dumps({'errors': cleanup_errors}) + '\n')
    if not cleanup_errors:
        run('public-cache-handoff', ['sudo', 'chown', '-R', f'{os.getuid()}:{os.getgid()}', CACHE],
            60, cleanup=True)
    assert not cleanup_errors, 'OWNED_CONTAINER_CLEANUP_UNCONFIRMED'
