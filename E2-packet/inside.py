"""New E1 container lanes. DRAFT: refuses before any project command."""
import hashlib,json,os,platform,resource,shutil,signal,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
P=Path('/inputs');W=Path('/work');S=W/'source';E=W/'evidence'
def verify_packet(root):
 root=Path(root);manifest=json.loads((root/'manifest.json').read_text());rows=[]
 for path in sorted(root.rglob('*')):
  assert not path.is_symlink(),'PACKET_SYMLINK'
  if not path.is_file() or path.relative_to(root).as_posix()=='approval.json':continue
  data=path.read_bytes()
  if path.relative_to(root).as_posix()=='manifest.json':
   normalized=dict(manifest);normalized['sealed_packet_sha256']=None
   data=json.dumps(normalized,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
  rows.append([path.relative_to(root).as_posix(),len(data),hashlib.sha256(data).hexdigest()])
 digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 assert digest==manifest['sealed_packet_sha256'],'PACKET_BYTE_SEAL_MISMATCH'
 assert hashlib.sha256((root/'consumer-retained-index.json').read_bytes()).hexdigest()==manifest['consumer_retained_index_sha256'],'CONSUMER_INDEX_CHANGED'
 return manifest,digest

M,packet_digest=verify_packet(P)
assert M['execution_enabled'] is True and M['state']=='SEALED_AFTER_EXACT_PACKET_REVIEW','DRAFT_DISABLED'
assert platform.machine()=='x86_64' and os.getuid()==int(os.environ['EXPECTED_UID']) and os.getgid()==int(os.environ['EXPECTED_GID'])
from safe_extract import extract
from source_import import import_source,git
from check_lifecycles import approved_inventory,npm_events,pnpm_events,verify
profile,variant=os.environ['PROFILE'],os.environ['VARIANT'];spec=M['profiles'][profile]
phase=sys.argv[1];assert phase in ['g0','prepare','measure','test','consumer-warm','consumer-test']
E.mkdir(exist_ok=True);statefile=E/'state.json'
state=json.loads(statefile.read_text()) if statefile.exists() else {'profile':profile,'variant':variant,'commands':[]}
ENV={'PATH':'/work/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','USERPROFILE':'/work/home','XDG_CONFIG_HOME':'/work/home/.config','XDG_CACHE_HOME':'/work/home/.cache','XDG_DATA_HOME':'/work/home/.local/share','USER':'node','LOGNAME':'node','CI':'true','NO_COLOR':'1','RUNNER_OS':'Linux','RUNNER_TEMP':'/work/tmp','TMPDIR':'/work/tmp','npm_config_cache':'/work/cache/npm','npm_config_fetch_retries':'0','npm_config_fetch_timeout':'20000','npm_config_loglevel':'info','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','DO_NOT_TRACK':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','RUFLO_DAEMON_AUTOSTART':'0','RUFLO_NO_SKILLS_SH':'1'}
if phase not in ['prepare','consumer-warm']:ENV.update(NODE_OPTIONS='--require=/inputs/network-guard.cjs',E1_NETWORK_LOG='/work/evidence/blocked-network.jsonl')
ENV['VARIANT']=variant
if phase=='consumer-test':ENV['npm_config_nodedir']='/usr/local'
ENV['RUFLO_ESBUILD_DIR']='/work/source/v3'
for d in ['home','tmp','bin','cache/npm','root-pack']:(W/d).mkdir(parents=True,exist_ok=True)
def save():statefile.write_text(json.dumps(state,indent=2)+'\n')
def run(row,cwd=None):
 n=len(state['commands']);stem=E/(f'{n:03d}-'+row['label']);t=time.monotonic();record=dict(row);state['commands'].append(record)
 origin=json.loads((W/'host-evidence/start.json').read_text())['origin_epoch']
 stop=min(time.time()+row.get('cap_seconds',300),origin+3375,float(os.environ['E2_PHASE_DEADLINE']));child=None;failure=None
 record.update(start_monotonic=t,deadline_epoch=stop,stdout=stem.with_suffix('.stdout').name,stderr=stem.with_suffix('.stderr').name);save()
 try:
  assert time.time()<stop,'CONTAINER_LANE_DEADLINE'
  with stem.with_suffix('.stdout').open('xb') as out,stem.with_suffix('.stderr').open('xb') as err:
   child=subprocess.Popen(['bash','-e','-o','pipefail','-c',row['run']],cwd=cwd or S/row.get('cwd','.'),env=ENV,stdout=out,stderr=err,start_new_session=True)
   while child.poll() is None:
    assert time.time()<stop,'CONTAINER_COMMAND_DEADLINE'
    time.sleep(0.2)
   assert child.returncode not in [-9,-11,137,139],('CRASH_OR_OOM',row['label'])
   if not row.get('differential'):assert child.returncode==0,('COMMAND_FAILED',row['label'],child.returncode)
 except BaseException as error:
  failure=repr(error);raise
 finally:
  if child is not None and child.poll() is None:
   try:os.killpg(child.pid,signal.SIGTERM)
   except ProcessLookupError:pass
   try:child.wait(timeout=3)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)
  record.update(exit=child.returncode if child else None,seconds=time.monotonic()-t,failure=failure,child_maxrss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
  # Preserve raw reports even if partial/invalid or the command failed/timed out.
  try:
   if row.get('report'):
    raw=row['report'].replace('$RUNNER_TEMP','/work/tmp');path=Path(raw) if raw.startswith('/') else S/raw
    record['report_present']=path.is_file()
    if path.is_file():
     target=E/(row['label']+'-report.json')
     if path!=target:shutil.copyfile(path,target)
     record['preserved_report']=target.name
  except BaseException as report_error:
   record['report_preservation_error']=repr(report_error)
  finally:save()

 if row.get('report'):
  try:
   assert record['report_present'],('MISSING_REPORT',row['label'])
   data=json.loads((E/record['preserved_report']).read_text());assert data.get('testResults'),'EMPTY_REPORT'
  except BaseException as error:
   record['report_validation_error']=repr(error);save();raise
 return record
def command(label,code,cap=120):return dict(label=label,run=code,cap_seconds=cap)
def memory():return {n:(Path('/sys/fs/cgroup')/n).read_text() for n in ['memory.current','memory.peak','memory.max','memory.swap.max','memory.events','cpu.max']}
if phase=='g0':
 assert not S.exists(),'FRESH_SOURCE_REQUIRED'
 metadata=M['source_pack'];identities=json.loads((P/'source-identities.json').read_text())
 state['source_import']=import_source(S,P/'source.pack',metadata,identities)
 # The baseline helper bytes are saved before any candidate overlay or build.
 shutil.copytree(S/'v3/@claude-flow/cli/.claude/helpers',W/'base-helpers')
 if variant=='candidate':
  snapshot=json.loads((P/'source-manifest.json').read_text())
  for row in snapshot['files']:
   payload=P/'candidate-files'/row['path'];assert hashlib.sha256(payload.read_bytes()).hexdigest()==row['sha256']
   target=S/row['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload.read_bytes())
 state['locks_before']={n:hashlib.sha256((S/n).read_bytes()).hexdigest() for n in ['package-lock.json','v3/pnpm-lock.yaml']}
 run(command('pnpm-extraction-fixture','python3 /inputs/safe_extract.py fixture'),W)
 run(command('helper-real-positive-and-negatives','node /inputs/helper-proof.mjs g0 /work/source/v3/@claude-flow/cli /work/base-helpers'),W)
 run(command('report-negative-fixtures','python3 /inputs/g0.py'),W)
 run(command('guard-negative',"node --require=/inputs/network-guard.cjs -e \"require('assert').throws(()=>require('net').connect({host:'example.invalid',port:443}),/offline network guard/)\""),W)
 run(command('offline-native-compile','bash /inputs/g0-native.sh'),W)
 run(command('pre-network-lifecycle-admission','python3 /inputs/lifecycle-admission.py'),W)
 # No dependency install or model code has run before this point.
 state['g0_memory']=memory();state['g0_complete']=True;save()
elif phase=='prepare':
 assert state['g0_complete'];assert M.get('lifecycle_allowlist_sha256'),'LIFECYCLE_CLOSURE_MISSING'
 allow=P/'lifecycle-allowlist-for-review.json';assert hashlib.sha256(allow.read_bytes()).hexdigest()==M['lifecycle_allowlist_sha256']
 assert hashlib.sha256((P/'pnpm-8.15.0.tgz').read_bytes()).hexdigest()==M['pnpm_archive_sha256'],'PNPM_ARCHIVE_CHANGED'
 extract(P/'pnpm-8.15.0.tgz',W/'pnpm')
 (W/'bin/pnpm').write_text('#!/bin/sh\nexec node /work/pnpm/package/bin/pnpm.cjs "$@"\n');(W/'bin/pnpm').chmod(0o755)
 for row in spec['prepare']:
  record=run(row);text=(E/record['stdout']).read_text(errors='replace')+'\n'+(E/record['stderr']).read_text(errors='replace')
  if 'install' in row['label'] or row['label']=='native-root-binding':
   events=pnpm_events(text) if 'pnpm install' in row['run'] else npm_events(text)
   # Ignore-scripts phases correctly have zero executions; all other phases
   # must record actual lifecycle events matching pre-reviewed commands.
   if '--ignore-scripts' not in row['run']:
    result=verify(events,approved_inventory(allow),[]);state.setdefault('lifecycles',[]).append(result)
 metadata=run(command('actual-runtime-metadata','node /inputs/runtime-metadata.mjs /work/source'))
 state['runtime_metadata']=json.loads((E/metadata['stdout']).read_text());assert state['runtime_metadata']['arch']=='x64' and state['runtime_metadata']['availableParallelism']>=4
 state['prepared_memory']=memory();state['prepare_complete']=True;save()
elif phase=='measure':
 assert state['prepare_complete'];assert M.get('measurement_commands'),'MEASUREMENT_PROFILE_NOT_REVIEWED'
 for row in M['measurement_commands'][profile]:
  if row.get('candidate_only') and variant!='candidate':continue
  run(row)
 observed=memory();peak=int(observed['memory.peak']);events={x.split()[0]:int(x.split()[1]) for x in observed['memory.events'].splitlines()}
 assert not events.get('oom') and not events.get('oom_kill'),'MEASUREMENT_OOM'
 # Reviewer must seal actual host headroom/effective-worker measurement rule.
 assert M.get('current_admission_rule'),'CURRENT_ADMISSION_RULE_UNCLOSED'
 rule=M['current_admission_rule'];assert peak<=rule['maximum_measured_peak_bytes']<12*2**30,'CURRENT_PEAK_TOO_HIGH'
 (E/'current-memory-admission.json').write_text(json.dumps({'admitted':True,'measurement':observed,'rule':rule})+'\n')
elif phase=='test':
 assert json.loads((E/'current-memory-admission.json').read_text())['admitted']
 for row in spec['commands']:run(row)
 after={n:hashlib.sha256((S/n).read_bytes()).hexdigest() for n in state['locks_before']};assert after==state['locks_before'],'LOCK_MUTATION'
 state['memory_final']=memory();state['terminal']='COMPLETE_WITH_RECORDED_DIFFERENTIALS';save()
elif phase=='consumer-warm':
 assert profile=='pack22';run(command('consumer-lock-acquisition','bash /inputs/consumer-warm.sh',600))
 # Consumer identity/script inventory must be compared BEFORE scriptful install.
 assert M.get('consumer_admission_command'),'CONSUMER_GRAPH_ADMISSION_UNCLOSED'
 run(command('consumer-admission',M['consumer_admission_command'],120))
 # No warm node_modules trees: at most4admitted npm cache-add processes.
 cache_deadline=min(time.time()+300,float(os.environ['E2_PHASE_DEADLINE']))
 run(command('cache-admitted-artifacts','python3 /inputs/cache-acquire.py '+str(cache_deadline),300))
elif phase=='consumer-test':
 assert profile=='pack22'
 for kind in ['native','omit']:
  record=run(command('consumer-'+kind,'bash /inputs/consumer.sh '+kind,600))
  events=npm_events((E/record['stdout']).read_text(errors='replace')+'\n'+(E/record['stderr']).read_text(errors='replace'))
  state.setdefault('consumer_lifecycles',[]).append(verify(events,approved_inventory(P/'lifecycle-allowlist-for-review.json'),[]))
 state['packed_consumers_complete']=True;save()
