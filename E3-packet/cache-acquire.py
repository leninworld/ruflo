"""Bounded pool populates npm cache from admitted registry artifacts only."""
import json,os,subprocess,sys,time
from pathlib import Path
E=Path('/work/evidence');receipt=E/'cache-acquire.json'
origin=json.loads(Path('/work/host-evidence/start.json').read_text())['origin_epoch']
deadline=min(float(sys.argv[1]),time.time()+300,origin+3375)
rows=json.loads((E/'consumer-admission.json').read_text())['packages'];artifacts={}
for row in rows:
 url=row.get('resolved')
 if row.get('inBundle') or not url or not url.startswith('https://registry.npmjs.org/'):continue
 assert row.get('integrity'),'CACHE_SRI_MISSING'
 assert url not in artifacts or artifacts[url]==row['integrity'],'CACHE_SRI_CONFLICT'
 artifacts[url]=row['integrity']
pending=list(enumerate(sorted(artifacts.items())));active={};finished=[];failure=None
state={'state':'IN_PROGRESS','max_parallel':4,'cache_budget_seconds':300,'deadline_epoch':deadline,'actual_unique_registry_urls':len(artifacts),'retained_reference_unique_urls':173,'reference_limitation':'Oldobservedgraph, notcurrentnativeconsumerprice or proof; actualcoldtimeunknown','commands':finished}
def save():receipt.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 while pending or active:
  assert time.time()<deadline,'CACHE_POOL_DEADLINE'
  while pending and len(active)<4:
   index,(url,sri)=pending.pop(0);stem=E/f'cache-artifact-{index:04d}';out=stem.with_suffix('.stdout').open('xb');err=stem.with_suffix('.stderr').open('xb')
   command={'resolved':url,'integrity':sri,'argv':['npm','cache','add',url,'--ignore-scripts','--no-audit','--no-fund'],'start_epoch':time.time(),'stdout':out.name.rsplit('/',1)[-1],'stderr':err.name.rsplit('/',1)[-1]}
   try:child=subprocess.Popen(command['argv'],stdout=out,stderr=err)
   except BaseException as error:
    command.update(exit=None,failure=repr(error));finished.append(command);out.close();err.close();save();raise
   active[index]=(child,out,err,command)
  for index,(child,out,err,command) in list(active.items()):
   assert time.time()-command['start_epoch']<60,'CACHE_ARTIFACT_DEADLINE'
   if child.poll() is None:continue
   out.close();err.close();command.update(exit=child.returncode,seconds=time.time()-command['start_epoch']);finished.append(command);del active[index];save();assert child.returncode==0,('CACHE_ARTIFACT_FAILED',command['resolved'],child.returncode)
  time.sleep(.2)
 state['state']='CACHE_POPULATED_NOT_INSTALL_OR_SRI_PROOF'
except BaseException as error:failure=repr(error);state.update(state='FAILED',failure=failure);raise
finally:
 for child,out,err,command in active.values():
  if child.poll() is None:
   child.terminate()
   try:child.wait(timeout=1)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
  out.close();err.close();command.update(exit=child.returncode,seconds=time.time()-command['start_epoch'],failure=failure or 'POOL_INTERRUPTED');finished.append(command)
 state['completed_epoch']=time.time();save()
