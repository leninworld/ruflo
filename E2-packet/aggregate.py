"""Compare new E2 lanes; never consume4834 artifacts as execution results."""
import hashlib,json,re,sys
from pathlib import Path
sys.dont_write_bytecode=True
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

P=Path(__file__).resolve().parent;M,packet_digest=verify_packet(P)
from compare import compare
assert M['execution_enabled'] is True,'DRAFT_DISABLED'
root,output=map(Path,sys.argv[1:]);snapshot=json.loads((P/'source-manifest.json').read_text())
newfiles=[r['path'] for r in snapshot['files'] if '__tests__/' in r['path']]
origins=set()
result={'state':'INCOMPLETE','profiles':{},'NOT_RUN':json.loads((P/'unclosed-pricing.json').read_text())['not_run'],'errors':[]}
# Read diagnostic receipts even if the consumer caused its lane to fail.
result['cli_diagnostics']=[]
for variant in ['baseline','candidate']:
 for kind in ['native','omit']:
  path=root/('pack22-'+variant)/'evidence'/('cli-child-'+kind+'.json')
  if path.is_file():
   record=json.loads(path.read_text());result['cli_diagnostics'].append({'variant':variant,'kind':kind,'state':record.get('state'),'lastHttpStatus':record.get('lastHttpStatus'),'stderrTail':record.get('stderrTail'),'blockedNetworkSinceChild':record.get('blockedNetworkSinceChild'),'helperMismatchEvidence':record.get('helperMismatchEvidence')})
if any(row['state']=='PARK_HELPER_MANIFEST_MISMATCH' for row in result['cli_diagnostics']):result['state']='PARK_HELPER_MANIFEST_MISMATCH'
def diagnostics(t):return sorted(re.sub(r'\(\d+,\d+\)','(<line>,<column>)',x) for x in t.splitlines() if re.search(r'error TS\d+',x))
try:
 for profile in M['profiles']:
  if profile=='g0':continue
  lanes={}
  for variant in ['baseline','candidate']:
   w=root/(profile+'-'+variant);terminal=json.loads((w/'host-evidence/terminal.json').read_text());assert terminal['state']=='LANE_COMPLETE_NOT_AGGREGATE_PASS';assert terminal['elapsed_seconds']<=3420 and terminal['completed_epoch']<=terminal['origin_epoch']+3420
   origins.add(terminal['origin_epoch']);assert len(origins)==1,'WORKFLOW_ORIGIN_DRIFT'
   state=json.loads((w/'evidence/state.json').read_text());assert state['locks_before']==M['lock_hashes'];assert state['profile']==profile and state['variant']==variant
   admission=json.loads((w/'evidence/current-memory-admission.json').read_text());assert admission['admitted']
   lanes[variant]=(w,state)
  oldroot,old=lanes['baseline'];newroot,new=lanes['candidate'];assert old['locks_before']==new['locks_before']
  assert old['runtime_metadata']==new['runtime_metadata'],'INSTALLED_ENVIRONMENT_DIFF'
  a={c['label']:c for c in old['commands']};b={c['label']:c for c in new['commands']};assess={'commands':{},'reports':{}}
  for row in M['profiles'][profile]['commands']:
   label=row['label'];assert label in a and label in b
   assert a[label]['run']==b[label]['run'];assert b[label]['exit']==0 or a[label]['exit']!=0,('NEW_COMMAND_FAILURE',profile,label)
   assess['commands'][label]={'baseline_exit':a[label]['exit'],'candidate_exit':b[label]['exit']}
   if row.get('report'):
    x=json.loads((oldroot/'evidence'/(label+'-report.json')).read_text());y=json.loads((newroot/'evidence'/(label+'-report.json')).read_text());assess['reports'][label]=compare(x,y,'/work/source','/work/source',newfiles)
    baseline_collected={f['name'].removeprefix('/work/source/') for f in x['testResults']}
    baseline_directories={str(Path(f).parent) for f in baseline_collected}
    expected=[f for f in newfiles if str(Path(f).parent) in baseline_directories]
    assess['reports'][label]['expected_from_baseline_directories']=expected
    assess['reports'][label]['newly_fixed_files']=sorted({f['name'].removeprefix('/work/source/') for f in x['testResults'] if f['status']=='failed'}-{f['name'].removeprefix('/work/source/') for f in y['testResults'] if f['status']=='failed'})
    collected={f['name'].removeprefix('/work/source/') for f in y['testResults']};assert set(expected)<=collected,('NEW_TEST_FILE_NOT_COLLECTED',profile,set(expected)-collected)
   if label in ['typecheck','strict-v3-build','strict-cli-build'] and b[label]['exit']:
    def text(w,c):return (w/'evidence'/c['stdout']).read_text(errors='replace')+(w/'evidence'/c['stderr']).read_text(errors='replace')
    assert set(diagnostics(text(newroot,b[label])))<=set(diagnostics(text(oldroot,a[label]))),'NEW_TYPESCRIPT_DIAGNOSTIC'
  if profile=='pack22':
   assert old['packed_consumers_complete'] and new['packed_consumers_complete']
   for kind in ['build','extracted']:
    assert (oldroot/'evidence'/('official-helper-'+kind+'.stderr')).read_bytes()==(newroot/'evidence'/('official-helper-'+kind+'.stderr')).read_bytes(),'HELPER_DIFFERENTIAL_DRIFT'
   assess['npm_ls']={}
   for kind in ['native','omit']:
    old_exit=(oldroot/'evidence'/('npm-ls-'+kind+'.exit')).read_text().strip();new_exit=(newroot/'evidence'/('npm-ls-'+kind+'.exit')).read_text().strip()
    assess['npm_ls'][kind]={'baseline_exit':old_exit,'candidate_exit':new_exit,'all_passed':old_exit==new_exit=='0'}
    assert old_exit==new_exit,'NPM_LS_EXIT_DIFFERENTIAL'
    for suffix in ['.txt','.stderr']:
     assert (oldroot/'evidence'/('npm-ls-'+kind+suffix)).read_text()==(newroot/'evidence'/('npm-ls-'+kind+suffix)).read_text(),'NPM_LS_DIAGNOSTIC_DIFFERENTIAL'
    for w in [oldroot,newroot]:
     cli=json.loads((w/'evidence'/('cli-child-'+kind+'.json')).read_text());assert cli['state']=='REAL_CLI_MCP_START_AND_MEMORY_OBSERVED','REAL_CLI_STARTUP_OR_MEMORY_PROOF_MISSING-seecli_diagnostics'
    cli_before=json.loads((oldroot/'evidence'/('cli-child-'+kind+'.json')).read_text());cli_after=json.loads((newroot/'evidence'/('cli-child-'+kind+'.json')).read_text());assert cli_before['result']['results']==cli_after['result']['results'],'REAL_CLI_MEMORY_DIFFERENTIAL_CHANGED_UNATTRIBUTED'
    for w in [oldroot,newroot]:assert json.loads((w/'evidence'/('packed-'+kind+'.json')).read_text())['state']=='PACKED_PATH_OBSERVED'
    before=json.loads((oldroot/'evidence'/('packed-'+kind+'.json')).read_text());after=json.loads((newroot/'evidence'/('packed-'+kind+'.json')).read_text());assert before['normal']['results']==after['normal']['results'],'PACKED_SCORE_OR_ORDER_DRIFT'
    assert (oldroot/'evidence'/('init-'+kind+'.exit')).read_bytes()==(newroot/'evidence'/('init-'+kind+'.exit')).read_bytes(),'INIT_DIFFERENTIAL_CHANGED'
    assert json.loads((oldroot/'evidence'/('init-helpers-'+kind+'.json')).read_text())==json.loads((newroot/'evidence'/('init-helpers-'+kind+'.json')).read_text()),'HELPER_REFRESH_OUTCOME_CHANGED'
   for mode in ['hnsw','rabitq']:
    before=json.loads((oldroot/'evidence'/('ann-'+mode+'.json')).read_text());after=json.loads((newroot/'evidence'/('ann-'+mode+'.json')).read_text());assert before['state']==after['state']=='ACTUAL_ANN_HTTP_CHAIN';assert before['results']==after['results'],'ANN_RESULT_DIFFERENTIAL'
   fallback=json.loads((newroot/'evidence/fallback-required.json').read_text());assert fallback['success'] and fallback['numTotalTests']==fallback['numPassedTests']==3
  result['profiles'][profile]=assess
 result['state']='COMPLETE_DIFFERENTIAL_AND_PACK_PROOF_WITH_DISCLOSED_EXTERNAL_CHECKS_NOT_RUN'
except BaseException as e:result['errors'].append(repr(e));raise
finally:output.write_text(json.dumps(result,indent=2)+'\n')
