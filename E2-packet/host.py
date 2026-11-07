"""E2 proposed hosted controller; disabled until exact review/authorization."""
import argparse,hashlib,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
sys.dont_write_bytecode=True
P=Path(__file__).resolve().parent
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
assert M['execution_enabled'] is True and M['state']=='SEALED_AFTER_EXACT_PACKET_REVIEW','E2_DRAFT_DISABLED'
A=json.loads((P/'approval.json').read_text());assert A['output_uid_policy']=='actual_runner_uid_gid','OUTPUT_UID_POLICY_NOT_AUTHORIZED';assert A['execution_authorized'] is True and A['packet_sha256']==packet_digest,'APPROVAL_MISMATCH'
assert not json.loads((P/'unclosed-pricing.json').read_text())['blockers'],'UNAPPROVED_PACKET'
parser=argparse.ArgumentParser();parser.add_argument('profile');parser.add_argument('variant');parser.add_argument('output');parser.add_argument('--origin',type=float,required=True);args=parser.parse_args()
profile,variant=args.profile,args.variant
assert profile in M['profiles'] and profile!='g0' and variant in ['baseline','candidate']
origin=args.origin;deadline=origin+3420;assert 0<origin<=time.time()+5,'INVALID_SHARED_ORIGIN'
W=Path(args.output).resolve();assert not W.exists(),'NEVER_REUSE_ATTEMPT'
assert not any(str(W).startswith(x) for x in ['/Volumes/','/media/','/mnt/']),'EXTERNAL_DISK_FORBIDDEN'
W.parent.mkdir(parents=True,exist_ok=True);W.mkdir();E=W/'host-evidence';E.mkdir();uid,gid=os.getuid(),os.getgid();attempt=str(uuid.uuid4());started=time.time()
(E/'start.json').write_text(json.dumps({'id':attempt,'profile':profile,'variant':variant,'origin_epoch':origin,'deadline_epoch':deadline,'lane_start_epoch':started,'uid':uid,'gid':gid,'runner_image':os.getenv('ImageOS'),'runner_image_version':os.getenv('ImageVersion'),'packet_sha256':packet_digest})+'\n')
if started>=deadline-120:
 (E/'terminal.json').write_text(json.dumps({'state':'NOT_STARTED_DEADLINE','origin_epoch':origin,'deadline_epoch':deadline,'observed_epoch':started})+'\n');sys.exit(2)
seq=0;container=None;phase_deadline=None;network_used=0;last_rx=0
free_at_admission=os.statvfs(W).f_bavail*os.statvfs(W).f_frsize
def kill_group(child):
 if child is None or child.poll() is not None:return
 try:os.killpg(child.pid,signal.SIGTERM)
 except ProcessLookupError:return
 try:child.wait(timeout=2)
 except subprocess.TimeoutExpired:
  try:os.killpg(child.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  child.wait(timeout=2)
def host_rx():
 return sum(int(p.read_text()) for p in Path('/sys/class/net').glob('*/statistics/rx_bytes') if p.parts[-3]!='lo' and not p.parts[-3].startswith(('docker','veth','br-')))
def run(label,argv,cap=30,cleanup=False):
 global seq
 seq+=1;stem=E/(f'{seq:04d}-'+label);begin=time.time();stop=min(begin+cap,deadline-5,phase_deadline or deadline) if not cleanup else min(begin+cap,deadline)
 child=None;failure=None;record={'argv':list(map(str,argv)),'start_epoch':begin,'deadline_epoch':stop}
 try:
  assert begin<stop,'SHARED_DEADLINE'
  with stem.with_suffix('.stdout').open('xb') as out,stem.with_suffix('.stderr').open('xb') as err:
   rx=host_rx() if label=='pull-image' else None
   child=subprocess.Popen(list(map(str,argv)),stdout=out,stderr=err,start_new_session=True)
   while child.poll() is None:
    assert time.time()<stop,'HOST_COMMAND_DEADLINE'
    if rx is not None:
     received=max(0,host_rx()-rx);(E/'image-rx.json').write_text(json.dumps({'rx_bytes':received,'cap':M['image_network_byte_cap'],'host_wide':True})+'\n');assert received<=M['image_network_byte_cap'],'IMAGE_RX_CAP'
    time.sleep(.2)
   if rx is not None:assert max(0,host_rx()-rx)<=M['image_network_byte_cap'],'IMAGE_RX_CAP'
   assert child.returncode==0,(label,child.returncode)
  return stem.with_suffix('.stdout').read_text()
 except BaseException as error:failure=repr(error);raise
 finally:
  kill_group(child);record.update(exit=child.returncode if child else None,failure=failure,seconds=time.time()-begin,stdout=stem.with_suffix('.stdout').name,stderr=stem.with_suffix('.stderr').name);stem.with_suffix('.json').write_text(json.dumps(record)+'\n')
def inspect():return json.loads(run('inspect',['docker','inspect',container]))[0]
def rx_counter(reset=False):
 global network_used,last_rx
 rx=int(run('container-rx',['docker','exec',container,'sh','-c',"for p in /sys/class/net/*/statistics/rx_bytes; do case \"$p\" in */lo/*) continue;; esac; cat \"$p\"; done | awk '{n+=$1} END {print n+0}'"]).strip())
 if not reset:network_used+=max(0,rx-last_rx)
 last_rx=rx;(E/'container-rx.json').write_text(json.dumps({'rx_bytes':network_used,'cap':M['network_byte_cap'],'sample_interval_seconds':3})+'\n');assert network_used<=M['network_byte_cap'],'CONTAINER_RX_CAP'
def phase(name,cap):
 global seq,phase_deadline
 seq+=1;stem=E/(f'{seq:04d}-phase-'+name);begin=time.time();phase_deadline=min(begin+cap,deadline-45);child=None;failure=None
 try:
  assert begin<phase_deadline,'SHARED_PHASE_DEADLINE'
  with stem.with_suffix('.stdout').open('xb') as out,stem.with_suffix('.stderr').open('xb') as err:
   child=subprocess.Popen(['docker','exec','--env','E2_PHASE_DEADLINE='+str(phase_deadline),container,'python3','/inputs/inside.py',name],stdout=out,stderr=err,start_new_session=True)
   while child.poll() is None:
    assert time.time()<phase_deadline,'HOST_PHASE_DEADLINE'
    d=inspect();assert d['State']['Running'] and not d['State']['OOMKilled'],'CONTAINER_FAILURE'
    stat=os.statvfs(W);free=stat.f_bavail*stat.f_frsize;delta=max(0,free_at_admission-free)
    (E/'disk-delta.json').write_text(json.dumps({'free_at_admission':free_at_admission,'free_now':free,'sampled_delta_bytes':delta,'cap':M['owned_byte_cap'],'exclusive_vm_required':True})+'\n')
    assert delta<=M['owned_byte_cap'] and free>=3*2**30,'DISK_ADMISSION_BREACH'
    run('stats',['docker','stats','--no-stream','--format','{{json .}}',container])
    if name in ['prepare','consumer-warm']:rx_counter()
    time.sleep(min(3,max(0,phase_deadline-time.time())))
   assert child.returncode==0,(name,child.returncode)
   if name in ['prepare','consumer-warm']:rx_counter()
 except BaseException as error:failure=repr(error);raise
 finally:
  kill_group(child);stem.with_suffix('.json').write_text(json.dumps({'exit':child.returncode if child else None,'failure':failure,'seconds':time.time()-begin,'deadline_epoch':phase_deadline})+'\n');phase_deadline=None
try:
 assert Path('/proc/meminfo').exists() and uid!=0,'HOST_LINUX_NONROOT_REQUIRED'
 mem={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()}
 (E/'admission.json').write_text(json.dumps({'memory':mem,'cpus':sorted(os.sched_getaffinity(0)),'free_bytes':free_at_admission,'uid':uid,'gid':gid})+'\n')
 assert mem['MemAvailable']>=M['required_host_available_bytes'] and len(os.sched_getaffinity(0))>=4 and free_at_admission>=M['required_free_disk_bytes'],'ACTUAL_HOST_NOT_ADMITTED'
 image=M['images'][M['profiles'][profile]['node']];assert A['public_image_pull_authorized'] is True
 run('pull-image',['docker','pull','--platform','linux/amd64',image],180)
 image_info=json.loads(run('image-inspect',['docker','image','inspect',image]))[0];assert image_info['Architecture']=='amd64' and image_info['Os']=='linux' and image in image_info['RepoDigests']
 container='ruflo4876-e2-'+attempt
 run('create',['docker','create','--name',container,'--platform','linux/amd64','--network','none','--init','--user',f'{uid}:{gid}','--memory','12g','--memory-swap','12g','--pids-limit','1024','--cap-drop','ALL','--security-opt','no-new-privileges','--env','PROFILE='+profile,'--env','VARIANT='+variant,'--env','EXPECTED_UID='+str(uid),'--env','EXPECTED_GID='+str(gid),'--mount',f'type=bind,src={P},dst=/inputs,readonly','--mount',f'type=bind,src={W},dst=/work',image,'sleep','infinity'])
 run('start',['docker','start',container]);d=inspect();assert d['HostConfig']['Memory']==d['HostConfig']['MemorySwap']==12*2**30
 phase('g0',180);assert A['dependency_network_authorized'] is True
 run('connect',['docker','network','connect','bridge',container]);rx_counter(True);phase('prepare',900);run('disconnect',['docker','network','disconnect','bridge',container]);assert not inspect()['NetworkSettings']['Networks']
 phase('measure',900)
 mem={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()};(E/'post-measure-memory.json').write_text(json.dumps(mem)+'\n');assert mem['MemAvailable']>=M['current_admission_rule']['minimum_actual_host_headroom_bytes']
 phase('test',1800)
 if profile=='pack22':
  run('consumer-connect',['docker','network','connect','bridge',container]);rx_counter(True);phase('consumer-warm',600);run('consumer-disconnect',['docker','network','disconnect','bridge',container]);assert not inspect()['NetworkSettings']['Networks'];phase('consumer-test',1200)
 (E/'terminal.json').write_text(json.dumps({'state':'LANE_COMPLETE_NOT_AGGREGATE_PASS','origin_epoch':origin,'deadline_epoch':deadline,'completed_epoch':time.time(),'elapsed_seconds':time.time()-origin,'profile':profile,'variant':variant})+'\n')
except BaseException as error:
 (E/'terminal.json').write_text(json.dumps({'state':'FAILED','failure':repr(error),'origin_epoch':origin,'deadline_epoch':deadline,'observed_epoch':time.time(),'profile':profile,'variant':variant})+'\n');raise
finally:
 if container:
  for label,argv in [('stop-owned',['docker','stop','--time','5',container]),('final-state',['docker','inspect',container])]:
   try:run(label,argv,10,cleanup=True)
   except BaseException as error:(E/(label+'-error.json')).write_text(json.dumps({'failure':repr(error)})+'\n')
