"""Baseline-only feasibility measurement; no candidate or source implementation."""
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
E = Path('/work/evidence')
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
    run('node-identity', ['node', '-e', 'if(process.arch!=="x64" || process.versions.node.split(".")[0]!==process.argv[1])process.exit(1);console.log(JSON.stringify(process.versions))', PLAN['lanes'][LANE]['node_major']], cap=20)
    for binary in ['npm', 'python3', 'make', 'g++', 'git']:
        run('toolchain-' + binary.replace('+', 'p'), [binary, '--version'], cap=10)
    for folder, label in [(Path('/work'), 'work'), (Path('/tmp'), 'tmp')]:
        native = folder / 'native-exec-proof'
        shutil.copyfile('/bin/true', native)
        native.chmod(0o755)
        run(label + '-native-exec', [str(native)], cwd=folder, cap=10)
    (E / 'mountinfo.txt').write_text(Path('/proc/self/mountinfo').read_text())
    # Same real command runner: success, expected failure, timeout and evidence decoding.
    run('fixture-success', ['node', '-e', 'process.stdout.write(JSON.stringify({ok:true}))'], cap=5)
    assert json.loads((E / 'fixture-success.stdout').read_text()) == {'ok': True}
    run('fixture-failure', ['node', '-e', 'process.stderr.write("expected-fixture");process.exit(7)'], cap=5, expected=7)
    assert (E / 'fixture-failure.stderr').read_text() == 'expected-fixture'
    try:
        run('fixture-timeout', ['node', '-e', 'setTimeout(()=>{},5000)'], cap=.2)
    except AssertionError:
        receipt = json.loads((E / 'fixture-timeout.json').read_text())
        assert receipt['stop'] and receipt['exit'] is not None
    else:
        raise AssertionError('TIMEOUT_FIXTURE_DID_NOT_STOP')
    try:
        json.loads('{bad evidence')
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError('EVIDENCE_DECODER_ACCEPTED_INVALID_JSON')
    (E / 'fixture-decision.json').write_text(json.dumps({'success': True, 'expected_failure': True, 'timeout': True, 'decoding': True}))
    if LANE == 'workspace22':
        archive = P / 'pnpm-8.15.0.tgz'
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == PLAN['pnpm_sha256']
        tools = Path('/work/tools'); tools.mkdir()
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
    extra = {'NODE_OPTIONS': '--require=/packet/install-global-guard.cjs', 'INSTALL_GLOBAL_GUARD_LOG': '/work/evidence/install-global-denials.jsonl'}
    try:
        if LANE == 'workspace22':
            run('pnpm-frozen', ['pnpm','install','--frozen-lockfile','--reporter=ndjson'], cwd=S/'v3', cap=840, extra=extra)
        else:
            cwd=S/'plugins/ruflo-x-gateway' if LANE=='gateway22' else S
            flags=[] if LANE=='gateway22' else ['--legacy-peer-deps']
            run('npm-ci', ['npm','ci',*flags], cwd=cwd, cap=840, extra=extra)
    except AssertionError:
        guard=E/'install-global-denials.jsonl'
        (E/'install-failure-classification.json').write_text(json.dumps({'state':'HARNESS_GUARD_FAILURE' if guard.exists() and guard.stat().st_size else 'TERMINAL_INSTALL_FAILURE_POSSIBLE_TRANSIENT_NOT_PRODUCT','no_retry':True}))
        raise
    verify_source()
elif PHASE == 'early':
    if LANE=='gateway22':
        files=sorted(x.name for x in (S/'plugins/ruflo-x-gateway/test').glob('*.test.mjs'))
        assert files==sorted(PLAN['gateway_test_files']), 'Gateway test scope changed'
        run('gateway-real-dependencies',['node','--input-type=module','-e',"import WebSocket from 'ws';import {Firestore} from '@google-cloud/firestore';if(typeof WebSocket!=='function'||typeof Firestore!=='function')throw Error('dependency import');console.log('actual ws and Firestore SDK imports')"],cwd=S/'plugins/ruflo-x-gateway',cap=30)
    else:
        base=S/'v3' if LANE=='workspace22' else S
        fixture=base/'.validation-fixture';fixture.mkdir()
        (fixture/'vitest.config.mjs').write_text("export default {test:{include:['pass.test.mjs','fail.test.mjs'],environment:'node'}}")
        (fixture/'pass.test.mjs').write_text("import {test,expect} from 'vitest';test('real success',()=>expect(41+1).toBe(42));")
        (fixture/'fail.test.mjs').write_text("import {test,expect} from 'vitest';test('real failure',()=>expect(41+1).toBe(43));")
        try:
            run('actual-vitest-controller-fixture',[str(base/'node_modules/.bin/vitest'),'run','--config',str(fixture/'vitest.config.mjs'),'--root',str(fixture),'--maxWorkers=2','--reporter=json','--outputFile.json=/work/evidence/actual-vitest-controller-fixture-report.json'],cwd=base,cap=60,expected=1)
            report=json.loads((E/'actual-vitest-controller-fixture-report.json').read_text())
            assert report['numTotalTests']==2 and report['numFailedTests']==1
            assert sorted(x['status'] for x in report['testResults'])==['failed','passed']
            assert report['success'] is False
            (E/'actual-vitest-controller-fixture-proof.json').write_text(json.dumps({'real_dependency':True,'success_and_failure_retained':True,'files':{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in fixture.iterdir()}}))
        finally:
            # Only disposable hosted-container fixture files; external disks never mounted.
            shutil.rmtree(fixture)
    verify_source()
elif PHASE in ('build','measure'):
    ENV.pop('HF_HUB_OFFLINE',None)
    ENV.pop('TRANSFORMERS_OFFLINE',None)
    for item in PLAN['lanes'][LANE]['steps'][PHASE]:
        if item.get('kind') == 'cli-core-overlay':
            target=S/'node_modules/@claude-flow/cli-core/dist'
            assert target.is_dir() and not target.is_symlink()
            shutil.rmtree(target)
            shutil.copytree(S/'v3/@claude-flow/cli-core/dist',target)
            continue
        receipt=run(item['label'],item['args'],cwd=S/item['cwd'],cap=item['cap'],
                    expected=tuple(item['expected']) if isinstance(item['expected'],list) else item['expected'])
        if item.get('copy_report'):
            shutil.copyfile(S/item['copy_report'], E/(item['label']+'-report.json'))
        if item.get('vitest_report'):
            report=json.loads((E/item['vitest_report']).read_text())
            assert report['numTotalTestSuites']>0 and report['numTotalTests']>0
            assert isinstance(report['testResults'],list) and len(report['testResults'])>0
            # Baseline failures are evidence, never converted to a green result.
            (E/(item['label']+'-outcomes.json')).write_text(json.dumps({
                'success':report.get('success'), 'exit':receipt['exit'],
                'failed_suites':report.get('numFailedTestSuites'),
                'failed_tests':report.get('numFailedTests'),
                'failures':[x for x in report['testResults'] if x.get('status')!='passed']},indent=2))
            if receipt['exit']==139:
                log=(E/(item['label']+'.stdout')).read_text()+(E/(item['label']+'.stderr')).read_text()
                assert report['success'] is True and report['numFailedTestSuites']==report['numFailedTests']==0
                assert not __import__('re').search(r'Errors +[0-9]+ errors?|Unhandled Errors|error during close',log)
    verify_source()
else:
    raise ValueError(PHASE)
