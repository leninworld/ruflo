"""Early offline actual npm/toolchain fixture, no registry or production install."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from check_lifecycles import npm_events, verify

root=Path(tempfile.mkdtemp(prefix='npm-info-fixture-',dir='/work'))
package=root/'package';consumer=root/'consumer';package.mkdir();consumer.mkdir()
(package/'package.json').write_text(json.dumps({'name':'scoped-lifecycle-fixture','version':'1.0.0','scripts':{'postinstall':'node postinstall.cjs','prepare':'node prepare.cjs'}}))
(package/'prepare.cjs').write_text("require('fs').writeFileSync('unexpected-prepare.json','{}');")
(package/'postinstall.cjs').write_text("require('fs').writeFileSync('executed-proof.json',JSON.stringify({node:process.version,arch:process.arch}));")
(consumer/'package.json').write_text('{"name":"lifecycle-consumer","version":"1.0.0","private":true}')
env=dict(os.environ,npm_config_loglevel='info',npm_config_cache=str(root/'cache'),npm_config_update_notifier='false',npm_config_fund='false',npm_config_audit='false')
# A local tarball gives npm the same regular dependency lifecycle as real ci;
# it does not execute a top-level npm-run command with a different log format.
packed=subprocess.run(['npm','pack','--ignore-scripts','--offline','--pack-destination',str(root)],cwd=package,env=env,capture_output=True,text=True,timeout=20)
assert packed.returncode==0,packed.stderr
archives=list(root.glob('*.tgz'));assert len(archives)==1
installed=subprocess.run(['npm','install','--offline','--no-audit','--no-fund',str(archives[0])],cwd=consumer,env=env,capture_output=True,text=True,timeout=20)
assert installed.returncode==0,installed.stderr
assert not (consumer/'node_modules/scoped-lifecycle-fixture/unexpected-prepare.json').exists(),'TARBALL_DEPENDENCY_PREPARE_UNEXPECTED'
observed=npm_events(installed.stdout+'\n'+installed.stderr)
assert verify(observed,{('scoped-lifecycle-fixture','1.0.0','postinstall','node postinstall.cjs')},[('scoped-lifecycle-fixture','postinstall')])['actual_count']==1
proof=json.loads((consumer/'node_modules/scoped-lifecycle-fixture/executed-proof.json').read_text());assert proof['arch']=='x64'
print(json.dumps({'fixture':'PASS','npm_version':subprocess.run(['npm','--version'],capture_output=True,text=True,check=True,timeout=10).stdout.strip(),'proof':proof,'actual_events':observed}))

Path("/work/evidence/npm-fixture-package-lock.json").write_bytes((consumer/"package-lock.json").read_bytes())
Path("/work/evidence/npm-fixture-path.json").write_text(json.dumps({"consumer":str(consumer)}))

rebuilt=subprocess.run(['npm','rebuild','--offline','--foreground-scripts','--loglevel=info'],cwd=consumer,env=env,capture_output=True,text=True,timeout=20)
assert rebuilt.returncode==0,rebuilt.stderr
assert not (consumer/'node_modules/scoped-lifecycle-fixture/unexpected-prepare.json').exists(),'REBUILD_DEPENDENCY_PREPARE_UNEXPECTED'
rebuild_events=npm_events(rebuilt.stdout+'\n'+rebuilt.stderr)
assert verify(rebuild_events,{('scoped-lifecycle-fixture','1.0.0','postinstall','node postinstall.cjs')},[('scoped-lifecycle-fixture','postinstall')])['actual_count']==1
print(json.dumps({'rebuild_fixture':'PASS','actual_events':rebuild_events,'network':'none'}))
