"""Called only inside reviewed isolated Docker run; stages upstream release bundles.
No helper signer, release, credential, publication, or outside mount is used.
"""
import hashlib,json,os,subprocess,time,shutil
from pathlib import Path
S=Path('/work/source');E=Path('/work/evidence');P=Path('/packet');END=float(os.environ['ATTEMPT_DEADLINE'])
CLI=S/'v3/@claude-flow/cli'
def run(label,args,cwd,cap,allowed=(0,)):
 start=time.monotonic();out=E/(label+'.stdout');err=E/(label+'.stderr')
 with out.open('xb') as a,err.open('xb') as b:
  proc=subprocess.run(args,cwd=cwd,env=os.environ,stdout=a,stderr=b,timeout=min(cap,max(1,END-time.time())))
 (E/(label+'.json')).write_text(json.dumps({'exit':proc.returncode,'seconds':time.monotonic()-start,'args':args}))
 assert proc.returncode in allowed,label
 return proc.returncode
# This is the current #4940 shipping-smoke path, not prepare-publish's
# maintainer-signed release path. No helper is changed, signed or published.
stage=Path('/work/cli-stage');shutil.copytree(CLI,stage,symlinks=True,ignore=shutil.ignore_patterns('node_modules','.git'))
run('consumer-stage',['node','--input-type=module','-e',"import {stageInternalRuntimeBundles} from './scripts/stage-internal-runtime-bundles.mjs';await stageInternalRuntimeBundles('/work/cli-stage',{build:false});"],S,180)
pack=Path('/work/cli-pack');pack.mkdir();run('consumer-pack',['npm','pack','--ignore-scripts','--json','--pack-destination',str(pack)],stage,120)
(E/'consumer-staged-package.json').write_bytes((stage/'package.json').read_bytes())
tars=list(pack.glob('*.tgz'));assert len(tars)==1;tar=tars[0]
(E/'consumer-tarball.json').write_text(json.dumps({'name':tar.name,'sha256':hashlib.sha256(tar.read_bytes()).hexdigest(),'bytes':tar.stat().st_size,'shape':'upstream staged bundles; npm pack --ignore-scripts, not maintainer release'}))
outcomes=[]
for mode in ('enabled','absent'):
 consumer=Path('/work')/('consumer-'+mode);consumer.mkdir();(consumer/'package.json').write_text('{"name":"memory-batch-consumer","version":"0.0.0","private":true,"type":"module"}')
 args=['npm','install',str(tar),'--no-audit','--no-fund']
 if mode=='absent':args+=['--ignore-scripts','--omit=optional']
 run('consumer-install-'+mode,args,consumer,300)
 if mode=='absent':
  matches=[]
  for current,dirs,files in os.walk(consumer/'node_modules',followlinks=False):
   if Path(current).name=='better-sqlite3' and 'package.json' in files:matches.append(current)
  (E/'native-absence-filesystem-proof.json').write_text(json.dumps({'matches':matches,'followlinks':False}))
  assert not matches,'TRANSITIVE_BETTER_SQLITE3_PRESENT_NOT_NATIVE_ABSENCE'
 routes=['enabled'] if mode=='enabled' else ['absent-forced','absent-default']
 for route in routes:
  code=None;result=None
  try:code=run('consumer-stdio-'+route,['node',str(P/'stdio-probe.mjs'),str(consumer),route],consumer,300,allowed=(0,1))
  finally:
   proof=consumer/('stdio-proof-'+route+'.json')
   if proof.exists():
    (E/('consumer-stdio-'+route+'-proof.json')).write_bytes(proof.read_bytes());result=json.loads(proof.read_text())
   outcomes.append({'route':route,'exit':code,'success':code==0 and bool(result and result.get('success'))})
   (E/'consumer-outcomes.json').write_text(json.dumps(outcomes,indent=2))
assert len(outcomes)==3 and all(row['success'] for row in outcomes),'REAL_CONSUMER_ROUTE_FAILED'
