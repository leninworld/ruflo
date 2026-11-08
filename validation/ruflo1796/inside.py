"""One read-only import feasibility packet; no candidate or project test run."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import time

P = Path('/packet')
PLAN = json.loads((P / 'execution-plan.json').read_text())
LANE = os.environ['LANE']
PHASE = sys.argv[1]
END = float(os.environ['ATTEMPT_DEADLINE'])
S = Path('/work/source')
E = Path('/work/evidence') if PHASE != 'probe' else Path('/scratch/evidence')
ENV = {'PATH': '/work/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': '/work/home',
       'XDG_CONFIG_HOME': '/work/home/config', 'XDG_CACHE_HOME': '/work/home/cache',
       'TMPDIR': '/work/tmp', 'CI': 'true', 'NO_COLOR': '1',
       'RUFLO_DAEMON_AUTOSTART': '0', 'RUFLO_NO_SKILLS_SH': '1',
       'npm_config_cache': '/work/cache/npm', 'npm_config_fetch_retries': '0',
       'npm_config_fetch_timeout': '20000', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
       'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'DO_NOT_TRACK': '1'}


def run(label, args, cwd=S, cap=120, expected=0, extra=None):
    assert time.time() < END
    start = time.monotonic()
    out, err = E / (label + '.stdout'), E / (label + '.stderr')
    stopped = None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        child = subprocess.Popen(args, cwd=cwd, env=dict(ENV, **(extra or {})),
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while child.poll() is None:
                if time.time() >= END or time.monotonic() - start > cap:
                    raise TimeoutError('OWNED_COMMAND_DEADLINE')
                if out.stat().st_size + err.stat().st_size > 16 * 1024 ** 2:
                    raise RuntimeError('OWNED_COMMAND_LOG_CAP')
                time.sleep(.1)
        except BaseException as error:
            stopped = repr(error)
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=3)
    receipt = {'label': label, 'args': args, 'exit': child.returncode, 'stop': stopped,
               'seconds': time.monotonic() - start,
               'phase_children_peak_rss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
               'disk_free': shutil.disk_usage('/work').free,
               'stdout_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
               'stderr_sha256': hashlib.sha256(err.read_bytes()).hexdigest()}
    (E / (label + '.json')).write_text(json.dumps(receipt, indent=2))
    allowed = expected if isinstance(expected, tuple) else (expected,)
    assert stopped is None and child.returncode in allowed, receipt
    return receipt


def verify_source():
    for name, expected in PLAN['locked_inputs'].items():
        assert hashlib.sha256((S / name).read_bytes()).hexdigest() == expected, name


if PHASE == 'prepare':
    assert os.getuid() == 1000 and os.uname().machine == 'x86_64'
    assert not S.exists()
    shutil.copytree('/source', S, symlinks=True)
    verify_source()
    run('toolchain', ['bash', '-e', '-c',
        'node --version; npm --version; python3 --version; make --version; g++ --version; '
        'git --version; test "$(node -p process.arch)" = x64; '
        'test "$(node -p \'process.versions.node.split(".")[0]\')" = ' + PLAN['lanes'][LANE]['node_major']], cap=20)
    native = Path('/work/native-exec-proof')
    shutil.copyfile('/bin/true', native)
    native.chmod(0o755)
    run('work-mount-native-exec', [str(native)], cwd=Path('/work'), cap=10)
    tmp_native = Path('/tmp/native-exec-proof')
    shutil.copyfile('/bin/true', tmp_native)
    tmp_native.chmod(0o755)
    run('tmp-mount-write-and-native-exec', [str(tmp_native)], cwd=Path('/tmp'), cap=10)
    (E / 'mountinfo.txt').write_text(Path('/proc/self/mountinfo').read_text())
    run('probe-syntax', ['node', '--check', '/packet/probe.mjs'], cap=10)
    run('probe-fixture-syntax', ['node', '--check', '/packet/probe-fixture.mjs'], cap=10)
    run('guard-syntax', ['node', '--check', '/packet/import-guard.cjs'], cap=10)
    run('probe-fixtures', ['node', '/packet/probe-fixture.mjs', str(S), '/work/probe-fixtures'], cap=100)
    # The guard must reject an actual JS network attempt and retain its reason.
    run('offline-guard-fixture', ['node', '--require=/packet/import-guard.cjs', '-e',
        'try { require("node:net").connect(9,"127.0.0.1");process.exit(2) } '
        'catch(e) { if(e.code!=="IMPORT_SIDE_EFFECT_DENIED")throw e; }'],
        cwd=Path('/scratch'), cap=10,
        extra={'IMPORT_DENIAL_LOG': '/scratch/guard-fixture-denials.jsonl'})
    assert json.loads(Path('/scratch/guard-fixture-denials.jsonl').read_text())['operation']
    if LANE == 'workspace22':
        archive = P / 'pnpm-8.15.0.tgz'
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == PLAN['pnpm_sha256']
        tools = Path('/work/tools')
        tools.mkdir()
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            assert len(members) == 684
            for member in members:
                path = Path(member.name)
                assert not path.is_absolute() and '..' not in path.parts
                assert member.isfile() or member.isdir()
            tar.extractall(tools)
        Path('/work/bin').mkdir()
        wrapper = Path('/work/bin/pnpm')
        wrapper.write_text('#!/bin/sh\nexec /usr/local/bin/node /work/tools/package/bin/pnpm.cjs "$@"\n')
        wrapper.chmod(0o755)
        run('pnpm-identity', ['pnpm', '--version'], cap=15)
        assert (E / 'pnpm-identity.stdout').read_text().strip() == '8.15.0'
elif PHASE == 'install':
    guard = {'NODE_OPTIONS': '--require=/packet/install-global-guard.cjs',
             'INSTALL_GLOBAL_GUARD_LOG': '/work/evidence/install-global-denials.jsonl'}
    if LANE == 'root20':
        run('npm-ci', ['npm', 'ci', '--legacy-peer-deps'], cap=600, extra=guard)
    else:
        run('pnpm-frozen', ['pnpm', 'install', '--frozen-lockfile', '--reporter=ndjson'],
            cwd=S / 'v3', cap=600, extra=guard)
    verify_source()
elif PHASE == 'build':
    if LANE == 'root20':
        run('root-security-build', ['npm', 'run', 'build', '--workspace=@claude-flow/security'], cap=120)
        run('root-cli-core-build', ['npm', 'run', 'build', '--prefix', 'v3/@claude-flow/cli-core'], cap=120)
        target = S / 'node_modules/@claude-flow/cli-core/dist'
        assert target.is_dir() and not target.is_symlink()
        # Disposable hosted source copy only; same CI overlay, not Mac data.
        shutil.rmtree(target)
        shutil.copytree(S / 'v3/@claude-flow/cli-core/dist', target)
        # Exact root build script is fail-tolerant upstream. Retain its full
        # output and let the import measurement record missing dist modules.
        run('root-cli-build-ts', ['npm', 'run', 'build:ts'], cap=240)
    else:
        run('workspace-cli-dependency-build', ['pnpm', '--filter', '@claude-flow/cli...', 'run', 'build'],
            cwd=S / 'v3', cap=480)
    verify_source()
elif PHASE == 'probe':
    ENV.update(HOME='/scratch/home', XDG_CONFIG_HOME='/scratch/home/config',
               XDG_CACHE_HOME='/scratch/home/cache', TMPDIR='/scratch/tmp')
    for name in ('home', 'tmp'):
        Path('/scratch', name).mkdir()
    home_before = sorted(str(x.relative_to('/scratch/home')) for x in Path('/scratch/home').rglob('*'))
    try:
        receipt = run('all-command-imports', ['node', '--require=/packet/import-guard.cjs', '/packet/probe.mjs',
            str(S), '/scratch/evidence/command-imports.json'], cwd=Path('/scratch'), cap=90,
            extra={'IMPORT_DENIAL_LOG': '/scratch/import-denials.jsonl'}, expected=(0, 2))
        report = json.loads((E / 'command-imports.json').read_text())
        assert report['harnessError'] is False and isinstance(report['modules'], list) and report['modules']
        assert report['complete'] == (receipt['exit'] == 0)
        (E / 'measurement-decision.json').write_text(json.dumps({
            'state': 'MEASURED_WITH_FINDINGS' if receipt['exit'] == 2 else 'MEASURED_WITHOUT_IMPORT_FINDINGS',
            'full_validation': False, 'product_authoring': 'BLOCKED_PENDING_RESULTS_REVIEW',
            'root_lane_limit': 'Built-output import approximates root dependency resolution; it does not reproduce vitest TypeScript transforms.'}))
    finally:
        home_after = sorted(str(x.relative_to('/scratch/home')) for x in Path('/scratch/home').rglob('*'))
        (E / 'scratch-home-delta.json').write_text(json.dumps({'before': home_before, 'after': home_after}))
        assert not Path('/scratch/import-denials.jsonl').exists(), 'IMPORT_SIDE_EFFECT_ATTEMPT'
    verify_source()
else:
    raise ValueError(PHASE)
