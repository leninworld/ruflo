"""Workflow origin: seal/capacity gate, no project helper import."""
import hashlib,json,os,sys,time
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

M,digest=verify_packet(P)
assert M['execution_enabled'] is True and M['state']=='SEALED_AFTER_EXACT_PACKET_REVIEW','DRAFT_DISABLED'
assert os.getenv('GITHUB_RUN_ATTEMPT')=='1','NO_RERUNS'
assert os.getenv('GITHUB_REPOSITORY_VISIBILITY')=='public','PUBLIC_FORK_FOUR_CPU_SCOPE_REQUIRED'
assert os.getenv('RUNNER_OS')=='Linux' and os.getenv('ImageOS')=='ubuntu24','PINNED_UBUNTU24_REQUIRED'
assert Path(os.environ['GITHUB_WORKSPACE'],'.github/workflows/ruflo4876-e2.yml').read_bytes()==(P/'workflow.yml').read_bytes(),'WORKFLOW_BYTES_DIFFER'
A=json.loads((P/'approval.json').read_text());assert A['output_uid_policy']=='actual_runner_uid_gid','OUTPUT_UID_POLICY_NOT_AUTHORIZED';assert A['execution_authorized'] is True and A['packet_sha256']==digest
mem={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()};stat=os.statvfs(P);free=stat.f_bavail*stat.f_frsize
receipt={'state':'ORIGIN_CAPACITY_RECORDED','image_os':os.getenv('ImageOS'),'image_version':os.getenv('ImageVersion'),'cpu_affinity':sorted(os.sched_getaffinity(0)),'memory':mem,'free_disk_bytes':free,'uid':os.getuid(),'gid':os.getgid(),'packet_sha256':digest,'floor_memory_bytes':M['required_host_available_bytes'],'floor_disk_bytes':M['required_free_disk_bytes']}
Path('origin.json').write_text(json.dumps(receipt,indent=2)+'\n')
assert len(os.sched_getaffinity(0))>=4 and mem['MemAvailable']>=M['required_host_available_bytes'] and free>=M['required_free_disk_bytes'],'ACTUAL_ORIGIN_CAPACITY_NOT_ADMITTED_NO_SILENT_LOWERING'
origin=int(time.time());receipt.update(origin_epoch=origin,deadline_epoch=origin+3420);Path('origin.json').write_text(json.dumps(receipt,indent=2)+'\n')
with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('epoch='+str(origin)+'\n')
