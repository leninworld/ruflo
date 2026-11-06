"""Draft hosted-container controller. Not approved or executed on authoring Mac."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import math
import shutil
import subprocess
import sys
import time
import threading

from check_lifecycles import approved_inventory, npm_events, pnpm_events, verify
from safe_extract import extract
from source_cache import import_source, seed, apply, verify_source

P = Path('/inputs')
W = Path('/work')
E = W / 'evidence'
S = W / 'source'
M = json.loads((P / 'manifest.json').read_text())
PLAN = json.loads((P / 'profile-plan.json').read_text())
assert PLAN['profiles'] == M['profiles'], 'PROFILE_CONFIGURATION_DRIFT'
IDENTITIES = json.loads((P / 'source-identities.json').read_text())
PROFILE, VARIANT, MODE = (os.environ[k] for k in ['PROFILE', 'VARIANT', 'PACKET_MODE'])
ORIGIN = int(os.environ['PACKET_ORIGIN'])
DEADLINE = ORIGIN + M['whole_deadline_seconds']
assert M['status'] == 'SEALED_FOR_ONE_RUN'
assert platform.machine() == 'x86_64' and os.getuid() == 1000
assert PROFILE in PLAN['profiles'] and VARIANT in ['baseline', 'candidate']
E.mkdir(exist_ok=True)
ENV = dict(os.environ, HOME='/work/home', USERPROFILE='/work/home',
    CI='true', RUNNER_OS='Linux', RUNNER_TEMP='/work/tmp',
    npm_config_cache='/cache/npm', npm_config_loglevel='info',
    npm_config_fetch_retries='0', npm_config_fetch_timeout='20000',
    PNPM_HOME='/work/bin', GIT_TERMINAL_PROMPT='0',
    GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
    DO_NOT_TRACK='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
ENV['PATH'] = '/work/bin:' + ENV['PATH']
for key in list(ENV):
    if any(word in key for word in ['API_KEY', 'AUTH_TOKEN', 'ACCESS_TOKEN', 'PASSWORD', 'SECRET']):
        ENV.pop(key)
    if key.endswith('_BASE_URL') or 'PROXY' in key.upper() or key in ['GH_TOKEN', 'GITHUB_TOKEN', 'NODE_OPTIONS', 'RUFLO_PROVIDER', 'CLAUDE_FLOW_CONFIG']:
        ENV.pop(key)
PHASE = sys.argv[1]
assert PHASE in ['prepare', 'test', 'shipping-warm', 'shipping-offline']
STATE = E / 'state.json'
state = json.loads(STATE.read_text()) if STATE.exists() else {'commands': [], 'profile': PROFILE, 'variant': VARIANT}


def save():
    STATE.write_text(json.dumps(state, indent=2) + '\n')


def run(label, command, cwd=S, seconds=120, check=True, env=None):
    assert isinstance(command, str)
    sequence = len(state['commands']) + 1
    stem = f'{sequence:03d}-' + label
    limit = min(seconds, DEADLINE - time.time() - 180,
                ORIGIN + 900 - time.time() if MODE == 'd0' else float('inf'))
    assert limit > 0, 'ORIGINAL_CLOCK_EXPIRED'
    began = time.monotonic()
    record = {'label': label, 'command': command, 'cwd': str(cwd), 'cap_seconds': limit}
    state['commands'].append(record)
    save()
    with (E / (stem + '.stdout')).open('xb') as out, (E / (stem + '.stderr')).open('xb') as err:
        p = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', command], cwd=cwd,
            env=env or ENV, stdout=out, stderr=err, timeout=limit)
    record.update(exit=p.returncode, seconds=time.monotonic() - began,
                  stdout=stem + '.stdout', stderr=stem + '.stderr',
                  child_maxrss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    save()
    assert p.returncode not in [-9, -11, 137, 139], ('CRASH_OR_OOM', label, p.returncode)
    if check:
        assert p.returncode == 0, ('COMMAND_FAILED', label, p.returncode)
    return record


def metadata(label):
    record = run(label, '''node --input-type=module -e '
import os from "node:os";
import {createRequire} from "node:module";
import {readFileSync,realpathSync,existsSync} from "node:fs";
import {join,dirname} from "node:path";
const req=createRequire(process.cwd()+"/package.json");
const libs={};
for(const n of ["vitest","@ruvector/attention","@ruvector/sona","ruvector","@claude-flow/cli-core/mcp-tools/types"]) {
try {libs[n]={realpath:realpathSync(req.resolve(n))};}catch(e){libs[n]={error:e.code};}}
const opt=[];
const lock=join(process.env.SOURCE_ROOT,"package-lock.json");
if(existsSync(lock))for(const [p,v] of Object.entries(JSON.parse(readFileSync(lock)).packages||{})) {
if(v.optional&&!existsSync(join(process.env.SOURCE_ROOT,p,"package.json")))opt.push(p);}
console.log(JSON.stringify({arch:process.arch,node:process.version,availableParallelism:os.availableParallelism(),cpus:os.cpus().length,libs,droppedOptionalPackages:opt.sort()},null,2));'
''', cwd=S / 'v3/@claude-flow/cli', env=dict(ENV, SOURCE_ROOT=str(S)))
    state[label] = json.loads((E / record['stdout']).read_text())
    assert state[label]['arch'] == 'x64' and state[label]['availableParallelism'] >= 4
    save()


def record_blocked_browser_fallback(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    assert all(r.get('state') == 'BLOCKED_GLOBAL_INSTALL' and r.get('command') == 'npm install -g agent-browser@latest' for r in rows), 'UNEXPECTED_GLOBAL_INSTALL_ATTEMPT'
    if rows:
        state.setdefault('disclosed_divergences', []).append({'blocked_global_browser_fallback': rows,
            'effect': 'No global package installed. Upstream CI would attempt this fallback. Browser behavior is not faked or claimed validated.'})
    save()


def overlay_guard():
    path = S / 'node_modules/@claude-flow/cli-core'
    assert path.is_dir() and not path.is_symlink(), 'CLI_CORE_OVERLAY_MUST_BE_REAL_DIRECTORY'
    actual = path.resolve(strict=True)
    assert actual.is_relative_to((S / 'node_modules').resolve(strict=True)), 'CLI_CORE_OVERLAY_OUTSIDE_INSTALL'
    state['root_cli_core_overlay_realpath'] = str(actual)
    save()


def gate_focused(label):
    record = run(label, './node_modules/.bin/vitest run @claude-flow/cli/__tests__/openai-configured-provider-4834.test.ts '
                 '--reporter=default --reporter=json --outputFile.json=/work/tmp/' + label + '.json',
                 S / 'v3', seconds=120, check=False)
    capture_report('/work/tmp/' + label + '.json', label + '.json')
    return record


def install():
    manager = 'npm' if PROFILE == 'root20' else 'pnpm'
    guard = E / 'install-global-guard.jsonl'
    install_env = dict(ENV, NODE_OPTIONS='--require=/inputs/install-global-guard.cjs',
                       INSTALL_GLOBAL_GUARD_LOG=str(guard))
    if manager == 'npm':
        command = 'npm ci --legacy-peer-deps'
        cwd = S
    else:
        command = 'pnpm install --frozen-lockfile --reporter=ndjson --store-dir /cache/pnpm'
        cwd = S / 'v3'
    record = run('frozen-install', command, cwd, seconds=600, env=install_env)
    logs = ''.join((E / record[k]).read_text(errors='replace') for k in ['stdout', 'stderr'])
    events = npm_events(logs) if manager == 'npm' else pnpm_events(logs)
    record_blocked_browser_fallback(guard)
    required = [('esbuild', 'postinstall')] if manager == 'npm' else [('@claude-flow/cli', 'postinstall')]
    result = verify(events, approved_inventory(P / 'lifecycle-allowlist-for-review.json'), required)
    (E / 'actual-install-lifecycles.json').write_text(json.dumps(result, indent=2) + '\n')
    state['lock_hashes_after_install'] = {
        path: hashlib.sha256((S / path).read_bytes()).hexdigest()
        for path in ['package-lock.json', 'v3/pnpm-lock.yaml']}
    assert state['lock_hashes_after_install'] == state['lock_hashes_before_install'], 'INSTALL_CHANGED_LOCK'
    verify_source(S, M, IDENTITIES, 'baseline-seeded' if VARIANT == 'baseline' else 'candidate')
    metadata('installed-runtime-metadata')


def capture_report(path, name):
    path = Path(path)
    assert path.is_file() and path.stat().st_size > 0, ('MISSING_COMPLETE_REPORT', str(path))
    report = json.loads(path.read_text())
    from compare_reports import inspect_report
    inspect_report(report, S)
    (E / name).write_bytes(path.read_bytes())


def profile_commands():
    for i, entry in enumerate(PLAN['profiles'][PROFILE]['commands']):
        command = entry['run'].replace('${{ matrix.platform }}', 'ubuntu-latest')
        if PROFILE == 'root20' and i == 1:
            overlay_guard()
        # Preserve the upstream overlay semantics in this new owned container.
        record = run(f'official-{i:02d}', command, S / entry['cwd'], seconds=1200, check=False)
        record['upstream_name'] = entry['name']
        save()
    report = PLAN['profiles'][PROFILE].get('report')
    if report:
        path = report.replace('$RUNNER_TEMP', ENV['RUNNER_TEMP'])
        if not path.startswith('/'):
            path = str(S / path)
        capture_report(path, 'full-report.json')


def focused(label):
    runner = './node_modules/.bin/vitest'
    test = 'v3/@claude-flow/cli/__tests__/openai-configured-provider-4834.test.ts' if PROFILE == 'root20' else '__tests__/openai-configured-provider-4834.test.ts'
    cwd = S if PROFILE == 'root20' else S / 'v3/@claude-flow/cli'
    record = run(label, runner + ' run ' + test +
                 ' --reporter=default --reporter=json --outputFile.json=/work/tmp/' + label + '.json',
                 cwd, seconds=120, check=False)
    capture_report('/work/tmp/' + label + '.json', label + '.json')
    return record


def d0():
    # All tiny harness fixtures ran before this profile's dependency install.
    # Root builds are required for the ratchet's real cli-core runtime overlay.
    if PROFILE == 'root20':
        for i, c in enumerate(PLAN['profiles']['root20']['commands'][:2]):
            if i == 1:
                overlay_guard()
            run('d0-root-bootstrap-' + str(i), c['run'], S / c['cwd'], seconds=180)
    else:
        run('d0-v3-bootstrap', 'pnpm --recursive --no-bail run build || true', S / 'v3', seconds=180)
        run('d0-strict-affected-build', 'pnpm run build', S / 'v3/@claude-flow/cli', seconds=120)
    sample = {'anon_peak': 0, 'max_node_procs': 0, 'errors': []}
    stopped = threading.Event()
    def sampler():
        while not stopped.is_set():
            try:
                memory = dict(line.split() for line in Path('/sys/fs/cgroup/memory.stat').read_text().splitlines())
                sample['anon_peak'] = max(sample['anon_peak'], int(memory['anon']))
                processes = Path('/sys/fs/cgroup/cgroup.procs').read_text().split()
                nodes = 0
                for pid in processes:
                    try:
                        nodes += Path('/proc/' + pid + '/comm').read_text().strip() == 'node'
                    except FileNotFoundError:
                        pass
                sample['max_node_procs'] = max(sample['max_node_procs'], nodes)
            except Exception as error:
                sample['errors'].append(repr(error))
            stopped.wait(.2)
    monitor = threading.Thread(target=sampler, daemon=True)
    monitor.start()
    try:
        # Real focused negative controls use existing baseline provider paths.
        focused('d0-baseline-regression')
        if PROFILE == 'cli22':
            gate_focused('d0-baseline-v3-gate')
        apply(S, P, M, IDENTITIES, W / 'retained-seed.test.ts')
        focused('d0-candidate-regression')
        if PROFILE == 'cli22':
            gate_focused('d0-candidate-v3-gate')
        from compare_reports import compare
        for label in ['regression'] + (['v3-gate'] if PROFILE == 'cli22' else []):
            state[label + '_comparison'] = compare(
                json.loads((E / ('d0-baseline-' + label + '.json')).read_text()),
                json.loads((E / ('d0-candidate-' + label + '.json')).read_text()),
                source_root=S, expected_new_cases=M['predicted_new_cases'])
        if PROFILE == 'cli22':
            probes = ' '.join(PLAN['D0']['native_probe_files'])
            run('d0-native-probes', './node_modules/.bin/vitest run ' + probes +
                ' --reporter=default --reporter=json --outputFile.json=/work/tmp/native.json',
                S / 'v3/@claude-flow/cli', seconds=120, check=False)
            capture_report('/work/tmp/native.json', 'native-probes.json')
            native = json.loads((E / 'native-probes.json').read_text())
            assert len(native['testResults']) == len(PLAN['D0']['native_probe_files'])
            state['native_measurement'] = {k: native.get(k) for k in ['success','numPassedTests','numFailedTests','numPendingTests','numTodoTests','numFailedTestSuites']}
            state['native_measurement']['gate'] = 'Measurement only; full baseline/candidate comparisons judge outcomes. No native-success claim if probes fail.'
    finally:
        stopped.set(); monitor.join(timeout=3)
    assert not sample['errors'] and sample['anon_peak'] > 0 and sample['max_node_procs'] > 0, 'D0_TEST_WINDOW_SAMPLER_INCOMPLETE'
    peak = int(Path('/sys/fs/cgroup/memory.peak').read_text())
    events = Path('/sys/fs/cgroup/memory.events').read_text()
    # conservative scaling across default workers, with real lifetime cgroup peak
    workers = 2 if PROFILE == 'root20' else state['installed-runtime-metadata']['availableParallelism']
    probe_workers = max(1, sample['max_node_procs'] - 1)
    estimate = sample['anon_peak'] * math.ceil(workers / probe_workers) * 1.25 + 2**30
    state['memory_admission'] = {'cgroup_peak_bytes': peak, 'memory_events': events,
        'effective_workers': workers, 'probe_workers': probe_workers, 'test_window_samples': sample,
        'conservative_scaled_bytes': estimate,
        'cap_bytes': 12 * 2**30, 'admitted': estimate <= 12 * 2**30}
    assert all(int(line.split()[1]) == 0 for line in events.splitlines() if line.split()[0] in ['oom', 'oom_kill'])
    assert estimate <= 12 * 2**30, 'D0_FULL_WORKER_MEMORY_NOT_JUSTIFIED'
    save()
    return {'D0_admitted': True, 'memory': state['memory_admission']}


def pack_shipping():
    destination = W / 'pack'
    destination.mkdir()
    run('pack-built-cli', 'pnpm pack --pack-destination /work/pack',
        S / 'v3/@claude-flow/cli', seconds=120)
    tarballs = list(destination.glob('*.tgz'))
    assert len(tarballs) == 1
    state['shipping_tarball'] = str(tarballs[0])
    state['shipping_tarball_sha256'] = hashlib.sha256(tarballs[0].read_bytes()).hexdigest()
    save()


def shipping_warm():
    consumer = W / 'shipping-warm'
    consumer.mkdir()
    (consumer / 'package.json').write_text('{"name":"shipping-fixture","version":"1.0.0","private":true,"type":"module"}')
    run('shipping-cache-warm-no-scripts', 'npm install ' + state['shipping_tarball'] +
        ' --omit=optional --no-audit --no-fund --ignore-scripts', consumer, seconds=240)
    allowed = approved_inventory(P / 'lifecycle-allowlist-for-review.json')
    actual = []
    # Inventory metadata after a script-disabled warmup, before any lifecycle runs.
    for package in (consumer / 'node_modules').rglob('package.json'):
        if package.is_symlink():
            continue
        value = json.loads(package.read_text())
        for phase in ['preinstall', 'install', 'postinstall']:
            command = value.get('scripts', {}).get(phase)
            if command:
                item = (value.get('name'), value.get('version'), phase, command)
                assert item in allowed, ('UNREVIEWED_SHIPPING_LIFECYCLE', item)
                actual.append(item)
        if (package.parent / 'binding.gyp').exists() and not value.get('scripts', {}).get('install'):
            item = (value.get('name'), value.get('version'), 'install', 'node-gyp rebuild')
            assert item in allowed, ('UNREVIEWED_SHIPPING_NATIVE_DEFAULT', item)
            actual.append(item)
    (E / 'shipping-warm-lifecycle-inventory.json').write_text(json.dumps(actual, indent=2) + '\n')
    state['consumer_lock_sha256'] = hashlib.sha256((consumer / 'package-lock.json').read_bytes()).hexdigest()
    state['shipping_state'] = 'CACHE_WARMED_SCRIPTS_DISABLED'
    save()


def shipping_offline():
    consumer = W / 'shipping-offline'
    consumer.mkdir()
    for name in ['package.json', 'package-lock.json']:
        shutil.copyfile(W / 'shipping-warm' / name, consumer / name)
    assert hashlib.sha256((consumer / 'package-lock.json').read_bytes()).hexdigest() == state['consumer_lock_sha256']
    assert hashlib.sha256(Path(state['shipping_tarball']).read_bytes()).hexdigest() == state['shipping_tarball_sha256']
    guard = E / 'shipping-global-guard.jsonl'
    record = run('shipping-fresh-offline-install',
        'npm ci --offline --omit=optional --no-audit --no-fund', consumer, seconds=180,
        env=dict(ENV, NODE_OPTIONS='--require=/inputs/install-global-guard.cjs', INSTALL_GLOBAL_GUARD_LOG=str(guard)))
    record_blocked_browser_fallback(guard)
    logs = ''.join((E / record[k]).read_text(errors='replace') for k in ['stdout', 'stderr'])
    events = npm_events(logs)
    result = verify(events, approved_inventory(P / 'lifecycle-allowlist-for-review.json'), [])
    (E / 'shipping-actual-lifecycles.json').write_text(json.dumps(result, indent=2) + '\n')
    run('installed-real-api-smoke', 'node /inputs/installed-api-smoke.mjs /work/shipping-offline ' +
        VARIANT + ' /work/evidence/shipping-result.json', consumer, seconds=150)
    state['shipping_state'] = 'FRESH_OFFLINE_INSTALL_AND_REAL_API_PASS'
    save()


try:
    if PHASE == 'prepare':
        for directory in ['home', 'tmp', 'bin']:
            (W / directory).mkdir(exist_ok=False)
        run('toolchain', 'uname -m; node --version; npm --version; python3 --version; make --version; g++ --version; git --version', W)
        if MODE == 'd0':
            for helper in ['safe_extract.py', 'source_cache.py', 'compare_reports.py', 'check_lifecycles.py']:
                run('tiny-' + helper.replace('.py', ''), 'python3 /inputs/' + helper + ' fixture', W, seconds=30)
            guard = '/work/evidence/tiny-global-guard.jsonl'
            run('tiny-global-install-rejected', '''node --require=/inputs/install-global-guard.cjs -e '
const cp=require("node:child_process");
let blocked=false;
try {cp.execSync("npm install --global frozen-fixture-never-executed");}
catch(e) {blocked=e.message.includes("Frozen validation forbids");}
if(!blocked) throw Error("guard did not reject");
const rows=require("node:fs").readFileSync(process.env.INSTALL_GLOBAL_GUARD_LOG,"utf8").trim().split("\\n");
if(rows.length!==1||JSON.parse(rows[0]).state!=="BLOCKED_GLOBAL_INSTALL")throw Error("missing real guard evidence");
console.log("PASS: rejected before a global npm subprocess");'
''', W, env=dict(ENV, INSTALL_GLOBAL_GUARD_LOG=guard), seconds=30)
        source = import_source(S, P / 'source.pack', M['source_pack'], IDENTITIES)
        state['source_import'] = source
        state['lock_hashes_before_install'] = {path: hashlib.sha256((S / path).read_bytes()).hexdigest()
            for path in ['package-lock.json', 'v3/pnpm-lock.yaml']}
        if VARIANT == 'baseline':
            seed(S, P, M, IDENTITIES)
        else:
            apply(S, P, M, IDENTITIES)
        payload = P / 'pnpm-8.15.0.tgz'
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == M['pnpm_tar_sha256']
        extract(payload, W / 'pnpm')
        launcher = W / 'bin/pnpm'
        launcher.write_text('#!/bin/sh\nexec node /work/pnpm/package/bin/pnpm.cjs "$@"\n')
        launcher.chmod(0o755)
        run('pinned-package-manager', 'test "$(pnpm --version)" = 8.15.0', W)
        install()
        save()
    elif PHASE == 'test':
        result = d0() if MODE == 'd0' else None
        if MODE != 'd0':
            profile_commands()
            if PROFILE == 'shipping22':
                assert all(r['exit'] == 0 for r in state['commands'] if r['label'].startswith('official-')), 'SHIPPING_BUILD_FAILED'
                pack_shipping()
                state['shipping_state'] = 'BUILT_AND_PACKED_OFFLINE'
                save()
                sys.exit(0)
        (E / 'terminal.json').write_text(json.dumps(dict(state='COMPLETE_WITH_FAILURES' if any(r['exit'] for r in state['commands']) else 'COMPLETE',
            profile=PROFILE, variant=VARIANT, **(result or {})), indent=2) + '\n')
    elif PHASE == 'shipping-warm':
        assert state['shipping_state'] == 'BUILT_AND_PACKED_OFFLINE'
        shipping_warm()
    else:
        assert state['shipping_state'] == 'CACHE_WARMED_SCRIPTS_DISABLED'
        shipping_offline()
        (E / 'terminal.json').write_text(json.dumps({'state': 'COMPLETE', 'profile': PROFILE, 'variant': VARIANT}) + '\n')
except BaseException as error:
    if not isinstance(error, SystemExit) or error.code:
        (E / ('failure-' + PHASE + '.json')).write_text(json.dumps({'error': repr(error), 'phase': PHASE}) + '\n')
    raise
finally:
    save()
