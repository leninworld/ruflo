"""Full feature validation in approved hosted Docker only. Never import on Mac."""
import hashlib
import gzip
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time
from check_lifecycles import approved_inventory, npm_events, pnpm_events, verify as verify_lifecycles
from source_input import prepare, verify as verify_source, sha
from safe_extract import extract

P = Path('/packet'); W = Path('/work'); S = W / 'source'; E = W / 'evidence'
PLAN = json.loads((P / 'execution-plan.json').read_text())
PROFILE = os.environ['PROFILE']; VARIANT = os.environ['VARIANT']; PHASE = sys.argv[1]
END = float(os.environ['ATTEMPT_DEADLINE'])
assert PROFILE in PLAN['profiles']
assert VARIANT in ['baseline', 'candidate'] and os.getuid() == 1000
ENV = {'PATH':'/work/bin:/usr/local/bin:/usr/bin:/bin', 'HOME':'/work/home', 'USERPROFILE':'/work/home',
 'TMPDIR':'/work/tmp','CI':'true','RUNNER_OS':'Linux','RUNNER_TEMP':'/work/tmp',
 'XDG_CONFIG_HOME':'/work/home/config','XDG_CACHE_HOME':'/work/home/cache',
 'RUFLO_DAEMON_AUTOSTART':'0','RUFLO_NO_SKILLS_SH':'1','DO_NOT_TRACK':'1',
 'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','GIT_CONFIG_NOSYSTEM':'1',
 'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0',
 'npm_config_cache':'/work/cache/npm','npm_config_loglevel':'info',
 'npm_config_fetch_retries':'0','npm_config_fetch_timeout':'20000',
 'npm_config_nodedir':'/usr/local'}
STATE = E / 'state.json'
state = json.loads(STATE.read_text()) if STATE.exists() else {'profile':PROFILE,'variant':VARIANT,'commands':[]}

def verify(source,plan,variant):
    return verify_source(source,plan,variant)

def save(): STATE.write_text(json.dumps(state,indent=2)+'\n')

def run(label, args, cwd=S, cap=120, expected=0, extra=None):
    assert time.time() < END - 180
    start = time.monotonic()
    out, err = E / (label + '.stdout'), E / (label + '.stderr')
    stopped = None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        child = subprocess.Popen(args, cwd=cwd, env=dict(ENV, **(extra or {})),
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while child.poll() is None:
                if time.time() >= END - 180 or time.monotonic() - start > cap:
                    raise TimeoutError('OWNED_COMMAND_DEADLINE')
                if out.stat().st_size + err.stat().st_size > 32 * 1024 ** 2:
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



def execute(label, command, cwd=S, cap=120, expected=0, extra=None):
    result = run(label, ['bash','-e','-o','pipefail','-c',command], cwd, cap, expected, extra)
    state['commands'].append(dict(result, command=command, cwd=str(cwd))); save()
    return result


def lifecycle(label, manager):
    logs = (E/(label+'.stdout')).read_text(errors='replace')+(E/(label+'.stderr')).read_text(errors='replace')
    actual = npm_events(logs) if manager == 'npm' else pnpm_events(logs)
    required = [('esbuild','postinstall')] if manager == 'npm' and label == 'frozen-install' else [('@claude-flow/cli','postinstall')] if manager == 'pnpm' else []
    result = verify_lifecycles(actual, approved_inventory(P/'lifecycle-allowlist-for-review.json'), required)
    (E/(label+'-lifecycles.json')).write_text(json.dumps(result,indent=2)+'\n')
    log = E/(label+'-global-denials.jsonl')
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert all(r['state']=='BLOCKED_GLOBAL_INSTALL' and r['command'] in ['npm install -g agent-browser@^0.41.0','npm install -g agent-browser@latest'] for r in rows)
    state.setdefault('disclosed_browser_global_fallbacks',{})[label] = rows;save()


def install_guard(label):
    return {'NODE_OPTIONS':'--require=/packet/install-global-guard.cjs',
            'INSTALL_GLOBAL_GUARD_LOG':str(E/(label+'-global-denials.jsonl'))}


def report(path, filename):
    from compare_reports import inspect_report
    value = Path(path); assert value.is_file() and value.stat().st_size <= 32*1024**2
    inspect_report(json.loads(value.read_text()), str(S))
    shutil.copyfile(value,E/filename)


def decoded_fixture(row):
    assert row['compressed_bytes']<=2*1024**2 and row['raw_bytes']<=32*1024**2
    source=P/row['compressed_name']
    assert source.stat().st_size==row['compressed_bytes'] and sha(source)==row['compressed_sha256']
    with gzip.open(source,'rb') as stream:data=stream.read(row['raw_bytes']+1)
    assert len(data)==row['raw_bytes'] and hashlib.sha256(data).hexdigest()==row['raw_sha256']
    return data


def source_hashes():
    return {name:sha(S/name) for name in PLAN['ui_source_files']}


try:
    if PHASE == 'prepare':
        assert os.uname().machine == 'x86_64'
        execute('toolchain','node --version; npm --version; python3 --version; make --version; g++ --version; git --version; test "$(node -p process.arch)" = x64',W,30)
        execute('image-node-headers','node /packet/check-node-headers.mjs',W,15)
        for helper in ['safe_extract.py','source_input.py','check_lifecycles.py','evidence_index.py']:
            execute('fixture-'+helper[:-3], 'python3 /packet/'+helper+' fixture',W,45)
        fixtures=W/'fixture-reports';fixtures.mkdir()
        for row in PLAN['fixture_archives']:
            assert '/' not in row['name'] and '/' not in row['compressed_name']
            data=decoded_fixture(row)
            for key in ['compressed_sha256','raw_sha256']:
                bad=dict(row);bad[key]='0'*64
                try:decoded_fixture(bad)
                except AssertionError:pass
                else:raise AssertionError('FIXTURE_ACCEPTED_BAD_'+key)
            with (fixtures/row['name']).open('xb') as output:output.write(data)
        execute('fixture-real-retained-reports','python3 /packet/compare_reports.py retained-fixtures /work/fixture-reports/retained-root-baseline.json /work/fixture-reports/retained-root-candidate.json /packet/retained-fixture-plan.json /work/evidence/comparator-fixtures.json',W,45)
        execute('fixture-actual-npm-info-format','python3 /packet/npm-lifecycle-fixture.py',W,60)
        execute('fixture-global-fallback-guard','node /packet/global-guard-fixture.cjs',W,15)
        execute('fixture-consumer-lock-comparator','python3 /packet/compare-consumer-locks.py fixture',W,15)
        execute('fixture-consumer-frozen-closure','python3 /packet/consumer-closure.py fixture',W,15)
        execute('fixture-retained-command-diagnostics','python3 /packet/compare_diagnostics.py fixture',W,30)
        prepare(S,'/source',P,PLAN,VARIANT)
        state['source_hashes']=source_hashes(); save()
        payload=P/'pnpm-8.15.0.tgz';assert sha(payload)==PLAN['pnpm_sha256']
        extract(payload,W/'pnpm')
        (W/'bin').mkdir(exist_ok=True)
        wrapper=W/'bin/pnpm';wrapper.write_text('#!/bin/sh\nexec /usr/local/bin/node /work/pnpm/package/bin/pnpm.cjs "$@"\n');wrapper.chmod(0o755)
        execute('pnpm-version','test "$(pnpm --version)" = 8.15.0',W,15)
    elif PHASE == 'install':
        if PROFILE=='root20': execute('frozen-install','npm ci --legacy-peer-deps',S,600,extra=install_guard('frozen-install')); manager='npm'
        else: execute('frozen-install','pnpm install --frozen-lockfile --reporter=ndjson --store-dir /work/cache/pnpm',S/'v3',600,extra=install_guard('frozen-install')); manager='pnpm'
        lifecycle('frozen-install',manager);verify(S,PLAN,VARIANT)
        execute('runtime-metadata','node /packet/runtime-metadata.mjs',S/'v3/@claude-flow/cli',15)
        state['runtime_metadata']=json.loads((E/'runtime-metadata.stdout').read_text());save()
        assert state['runtime_metadata']['arch']=='x64' and state['runtime_metadata']['availableParallelism']>=4
    elif PHASE == 'early':
        execute('early-security-build','npm run build --prefix v3/@claude-flow/security',S,120)
        # Native shim/core and real ToolRegistry; no fake validators or transport.
        if PROFILE=='root20':
            execute('early-cli-core-build','npm run build --prefix v3/@claude-flow/cli-core',S,120)
            execute('early-root-core-overlay','python3 /packet/overlay-cli-core.py',S,30)
        else:
            execute('early-cli-core-build','pnpm run build',S/'v3/@claude-flow/cli-core',120)
        execute('early-mcp-build','npm run build --prefix v3/@claude-flow/mcp',S,120)
        execute('early-lint','npm run lint',S,120)
        execute('actual-mcp-resolution','node /packet/mcp-resolution.mjs',S/'v3/@claude-flow/cli',30)
        # The full baseline remains pristine. Only this owned, new test is
        # temporarily injected to demonstrate the six pre-fix failures.
        test=PLAN['candidate_files'][1];target=S/test['path']
        if VARIANT=='baseline':
            assert not target.exists();shutil.copyfile(P/'candidate-files'/test['path'],target)
        try:
            focused=PLAN['focused_runners'][PROFILE]
            execute('focused-runner-identity','node /packet/focused-runner-identity.mjs '+PROFILE,S,20)
            execute('early-affected-behavior',focused['command'],S/focused['cwd'],120,expected=0 if VARIANT=='candidate' else 1)
            execute('verify-focused-regressions','python3 /packet/verify-focused.py /work/evidence/focused-report.json '+VARIANT,W,15)
        finally:
            if VARIANT=='baseline':
                assert sha(target)==test['sha256'];target.unlink()
        verify(S,PLAN,VARIANT)
    elif PHASE == 'test':
        profile=PLAN['profiles'][PROFILE]
        for number,entry in enumerate(profile['commands']):
            if PROFILE=='root20' and number==1:
                target=S/'node_modules/@claude-flow/cli-core';assert target.is_dir() and not target.is_symlink()
            execute('official-'+str(number).zfill(2),entry['run'].replace('${{ matrix.platform }}','ubuntu-latest'),S/entry['cwd'],1200,expected=(0,1,2))
        if profile.get('report'):
            path=profile['report'].replace('$RUNNER_TEMP',ENV['RUNNER_TEMP']); report(path if path.startswith('/') else S/path,'full-report.json')
        verify(S,PLAN,VARIANT);assert source_hashes()==state['source_hashes']
        if PROFILE=='shipping22':
            assert all(c['exit']==0 for c in state['commands'] if c['label'].startswith('official-'))
            (W/'pack').mkdir();execute('pack-built-cli','pnpm pack --pack-destination /work/pack',S/'v3/@claude-flow/cli',180)
            values=list((W/'pack').glob('*.tgz'));assert len(values)==1
            state['shipping_tarball']=str(values[0]);state['shipping_tarball_sha256']=sha(values[0]);save()
    elif PHASE == 'shipping-warm':
        consumer=W/'shipping-warm';consumer.mkdir();(consumer/'package.json').write_text('{"name":"schema-installed-proof","private":true,"type":"module"}')
        assert sha(state['shipping_tarball'])==state['shipping_tarball_sha256']
        execute('consumer-warm-no-scripts','npm install '+state['shipping_tarball']+' --no-audit --no-fund --ignore-scripts',consumer,300)
        execute('verify-consumer-closure','python3 /packet/consumer-closure.py /work/shipping-warm /packet/frozen-lifecycle-identities.json /work/evidence/consumer-closure.json',consumer,30)
        state['consumer_lock_sha256']=sha(consumer/'package-lock.json');shutil.copyfile(consumer/'package-lock.json',E/'consumer-lock.json');save()
        execute('consumer-warm-allowlisted-lifecycles','npm rebuild --foreground-scripts --loglevel=info',consumer,420,extra=install_guard('consumer-warm-allowlisted-lifecycles'))
        lifecycle('consumer-warm-allowlisted-lifecycles','npm')
    elif PHASE == 'shipping-disconnected-proof':
        consumer=W/'shipping-warm'
        assert sha(consumer/'package-lock.json')==state['consumer_lock_sha256']
        execute('installed-mcp-proof','node /packet/installed-mcp-proof.mjs /work/shipping-warm /work/evidence/installed-proof.json '+VARIANT,consumer,330)
        state['shipping_state']='FRESH_INSTALLED_CONSUMER_DISCONNECTED_MCP_TRANSPORT_PROOF_PASS';save()
    else: raise ValueError(PHASE)
    if S.exists():
        verify(S,PLAN,VARIANT)
        if 'source_hashes' in state:assert source_hashes()==state['source_hashes'], 'BASELINE_OR_CANDIDATE_UI_SOURCE_CHANGED'
    (E/(PHASE+'-terminal.json')).write_text(json.dumps({'state':'COMPLETE','profile':PROFILE,'variant':VARIANT})+'\n')
finally:
    save()
