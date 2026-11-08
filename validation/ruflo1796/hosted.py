"""Single hosted x64 native-import measurement, no product tests/publication.

Reuses the retained Ruflo controller's ownership, resource, network-transition,
deadline and sealed-evidence patterns. Never imports this file to preflight it.
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
P = Path(__file__).resolve().parent
PLAN = json.loads((P / 'execution-plan.json').read_text())
assert hashlib.sha256((P / 'execution-plan.json').read_bytes()).hexdigest() == os.environ['APPROVED_PLAN_SHA256']
LANE = sys.argv[1]
assert LANE in PLAN['lanes']
assert os.environ['GITHUB_REPOSITORY'] == 'leninworld/ruflo'
assert os.environ['RUNNER_ENVIRONMENT'] == 'github-hosted'
assert os.environ['RUNNER_OS'] == 'Linux' and platform.machine() == 'x86_64'
assert os.environ['GITHUB_RUN_ATTEMPT'] == '1'
assert os.environ['GITHUB_REF'] == 'refs/heads/validate-cli-reference-imports-1796-m1'
assert os.environ['GITHUB_EVENT_NAME'] == 'push'
assert os.environ['GITHUB_SHA'] == os.environ['APPROVED_DIAGNOSTIC_COMMIT']
# The new M1 commit timestamp is one origin shared by all three jobs.
# M0 remains closed; this controller requires its own exact runtime authority.
origin_raw = subprocess.run(['git', '-C', str(P), 'show', '-s', '--format=%ct', 'HEAD'],
                            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
ORIGIN = int(origin_raw)
assert -120 <= time.time() - ORIGIN < PLAN['target_seconds']
END = ORIGIN + PLAN['target_seconds']
ROOT = Path(os.environ['RUNNER_TEMP']) / ('ruflo1796-' + os.environ['GITHUB_RUN_ID'] + '-' + LANE)
ROOT.mkdir(exist_ok=False)
E = ROOT / 'evidence'
E.mkdir()
SOURCE = Path(os.environ['GITHUB_WORKSPACE']) / 'upstream'
WORK = ROOT / 'work'
SCRATCH = ROOT / 'scratch'
OWNED = []


def capture(args, cap=20, cleanup=False):
    if not cleanup:
        assert time.time() < END
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=cap if cleanup else min(cap, max(1, END - time.time()))).stdout.strip()


def gate():
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    values = {'epoch': time.time(), 'available_memory': int(mem['MemAvailable'].split()[0]) * 1024,
              'free_disk': shutil.disk_usage(ROOT).free, 'cpus': int(capture(['nproc'])),
              'retained_peak_bytes': PLAN['retained_peak_bytes']}
    (E / 'admission.json').write_text(json.dumps(values, indent=2))
    assert values['available_memory'] >= 12 * GIB, 'HOST_MEMORY_ADMISSION'
    assert values['free_disk'] >= 16 * GIB, 'HOST_DISK_ADMISSION'
    assert values['cpus'] >= 4, 'HOST_CPU_ADMISSION'


def stop(cid):
    assert cid in OWNED
    subprocess.run(['docker', 'stop', '--time', '5', cid], capture_output=True, timeout=20, check=False)
    state = json.loads(capture(['docker', 'inspect', '--format', '{{json .State}}', cid], cleanup=True))
    with (E / 'stops.jsonl').open('a') as handle:
        handle.write(json.dumps({'container': cid, 'state': state}) + '\n')
    assert state['Running'] is False, 'OWNED_STOP_UNCONFIRMED'


def network(cid, connected):
    capture(['docker', 'network', 'connect' if connected else 'disconnect', 'bridge', cid])
    networks = json.loads(capture(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', cid]))
    assert set(networks) == ({'bridge'} if connected else set()), 'NETWORK_TRANSITION_FAILED'


def create(image, readonly=False):
    mounts = [('type=bind,src=' + str(P) + ',dst=/packet,readonly'),
              ('type=bind,src=' + str(SOURCE) + ',dst=/source,readonly'),
              ('type=bind,src=' + str(WORK) + ',dst=/work' + (',readonly' if readonly else '')),
              ('type=bind,src=' + str(SCRATCH) + ',dst=/scratch')]
    args = ['docker', 'create', '--platform', 'linux/amd64', '--read-only', '--user', '1000:1000',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', str(10 * GIB), '--memory-swap', str(10 * GIB), '--cpus', '4',
            '--pids-limit', '1024', '--network', 'none' if readonly else 'bridge',
            '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
            '--log-opt', 'compress=false',
            '--tmpfs', '/tmp:rw,exec,nosuid,nodev,mode=1777,size=1073741824',
            '--env', 'LANE=' + LANE,
            '--env', 'ATTEMPT_DEADLINE=' + str(END)]
    for mount in mounts:
        args += ['--mount', mount]
    args += [image, 'sleep', '3600']
    cid = capture(args)
    assert len(cid) == 64 and all(c in '0123456789abcdef' for c in cid)
    OWNED.append(cid)
    capture(['docker', 'start', cid])
    return cid


def phase(cid, name):
    out, err = E / (name + '.stdout'), E / (name + '.stderr')
    begin = time.monotonic()
    last_sample = begin
    problem = None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        child = subprocess.Popen(['docker', 'exec', cid, 'python3', '/packet/inside.py', name],
                                 stdout=stdout, stderr=stderr)
        try:
            while child.poll() is None:
                if time.time() >= END or time.monotonic() - begin >= PLAN['phase_caps'][name]:
                    raise TimeoutError('OWNED_PHASE_DEADLINE')
                if out.stat().st_size + err.stat().st_size > 32 * 1024 ** 2:
                    raise RuntimeError('HOST_LOG_CAP')
                if shutil.disk_usage(ROOT).free < 4 * GIB:
                    raise RuntimeError('DISK_RESERVE')
                if time.monotonic() - last_sample >= 10:
                    stats = capture(['docker', 'stats', '--no-stream', '--format', '{{json .}}', cid], cap=15)
                    with (E / 'stats.jsonl').open('a') as log:
                        log.write(json.dumps({'phase': name, 'epoch': time.time(), 'stats': json.loads(stats)}) + '\n')
                    measured = subprocess.run(['sudo', 'du', '-s', '--block-size=1', str(WORK)],
                                              capture_output=True, text=True, timeout=15)
                    if measured.returncode:
                        lines = measured.stderr.splitlines()
                        assert measured.returncode == 1 and lines and all(
                            'No such file or directory' in line for line in lines), measured.stderr
                        with (E / 'sampling-misses.jsonl').open('a') as log:
                            log.write(json.dumps({'phase': name, 'stderr': measured.stderr[:4096]}) + '\n')
                    else:
                        assert int(measured.stdout.split()[0]) <= 12 * GIB, 'WORK_DISK_CAP'
                    last_sample = time.monotonic()
                time.sleep(.25)
        except BaseException as error:
            problem = repr(error)
            stop(cid)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    peak = capture(['docker', 'exec', cid, 'cat', '/sys/fs/cgroup/memory.peak']) if problem is None else None
    receipt = {'phase': name, 'exit': child.returncode, 'stop': problem,
               'seconds': time.monotonic() - begin, 'container_memory_peak_bytes': peak,
               'disk_free': shutil.disk_usage(ROOT).free}
    (E / (name + '-receipt.json')).write_text(json.dumps(receipt, indent=2))
    assert problem is None and child.returncode == 0, receipt


def main():
    gate()
    assert capture(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']) == PLAN['source_pin']
    assert capture(['git', '-C', str(P), 'rev-parse', 'HEAD']) == os.environ['GITHUB_SHA']
    # The publisher must create an orphan root commit with only the reviewed
    # nine blobs. Its message binds the already-reviewed tree and workflow
    # hashes without making a self-referential hash inside the tree itself.
    raw_commit = capture(['git', '-C', str(P), 'cat-file', '-p', 'HEAD'])
    header = raw_commit.split('\n\n', 1)[0]
    assert not any(line.startswith('parent ') for line in header.splitlines()), 'DIAGNOSTIC_COMMIT_NOT_ORPHAN'
    (E / 'raw-commit-header.txt').write_text(header + '\n')
    message = capture(['git', '-C', str(P), 'show', '-s', '--format=%B', 'HEAD'])
    marker = '\nReviewed-Diagnostic-Tree:\n'
    assert message.count(marker) == 1
    reviewed = json.loads(message.split(marker, 1)[1])
    assert reviewed['tree_oid'] == capture(['git', '-C', str(P), 'rev-parse', 'HEAD^{tree}'])
    expected_paths = {'validation/ruflo1796/' + row['path'] for row in PLAN['packet_files']}
    expected_paths.update({'validation/ruflo1796/execution-plan.json',
                           '.github/workflows/validate-cli-reference-imports-1796-m1.yml'})
    assert set(reviewed['files']) == expected_paths and len(expected_paths) == 9
    raw = capture(['git', '-C', str(P), 'ls-tree', '-r', '--full-tree', 'HEAD'])
    actual = {}
    packet_root = Path(os.environ['GITHUB_WORKSPACE']) / 'packet'
    for line in raw.splitlines():
        fields, path = line.split('\t', 1)
        mode, kind, oid = fields.split()
        assert mode == '100644' and kind == 'blob'
        actual[path] = oid
        assert reviewed['files'][path]['git_blob_oid'] == oid
        assert hashlib.sha256((packet_root / path).read_bytes()).hexdigest() == reviewed['files'][path]['sha256']
    assert set(actual) == expected_paths, 'UNREVIEWED_BRANCH_PATHS'
    (E / 'reviewed-diagnostic-tree.json').write_text(json.dumps(reviewed, indent=2))
    assert not (SOURCE / '.git/objects/info/alternates').exists()
    names = capture(['git', '-C', str(SOURCE), 'config', '--name-only', '--list'])
    assert not any('extraheader' in key.lower() or key.lower().startswith('credential.')
                   for key in names.splitlines()), 'SOURCE_AUTH_CONFIG'
    for row in PLAN['packet_files']:
        assert hashlib.sha256((P / row['path']).read_bytes()).hexdigest() == row['sha256'], row['path']
    for name, expected in PLAN['locked_inputs'].items():
        assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == expected, name
    for root in (WORK, SCRATCH):
        root.mkdir()
    for name in ('home', 'tmp', 'cache', 'evidence'):
        (WORK / name).mkdir()
    (SCRATCH / 'evidence').mkdir()
    capture(['sudo', 'chown', '-R', '1000:1000', str(WORK), str(SCRATCH)])
    image = PLAN['lanes'][LANE]['image']
    capture(['docker', 'pull', '--platform', 'linux/amd64', image], cap=240)
    cid = create(image)
    network(cid, False)
    phase(cid, 'prepare')
    network(cid, True)
    phase(cid, 'install')
    network(cid, False)
    phase(cid, 'build')
    stop(cid)
    probe = create(image, readonly=True)
    actual_networks = json.loads(capture(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', probe]))
    assert set(actual_networks) == {'none'}, 'PROBE_NETWORK_NOT_NONE'
    phase(probe, 'probe')
    stop(probe)
    measurement = json.loads((SCRATCH / 'evidence/measurement-decision.json').read_text())
    (E / 'decision.json').write_text(json.dumps({'state': measurement['state'],
        'denial_summary': measurement['denial_summary'],
        'full_validation': False, 'product_authored': False, 'results_review_required': True}))


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt(signum)))
    try:
        main()
    finally:
        confirmations = []
        for cid in OWNED:
            try:
                stop(cid)
                confirmations.append(True)
                (E / (cid[:12] + '-container.json')).write_text(capture(['docker', 'inspect', cid], cleanup=True))
            except Exception as error:
                confirmations.append(False)
                (E / (cid[:12] + '-stop-error.txt')).write_text(str(error))
        for folder in (WORK / 'evidence', SCRATCH / 'evidence'):
            if folder.exists():
                for path in folder.iterdir():
                    if path.is_file():
                        shutil.copyfile(path, E / (folder.parent.name + '-' + path.name))
        if SCRATCH.exists():
            for path in SCRATCH.glob('*.jsonl'):
                shutil.copyfile(path, E / ('scratch-' + path.name))
        index = [{'name': path.name, 'bytes': path.stat().st_size,
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in sorted(E.iterdir()) if path.is_file()]
        (E / 'evidence-index.json').write_text(json.dumps(index, indent=2))
        assert all(confirmations), 'STOP_UNCONFIRMED; preserve evidence; no success claim'
