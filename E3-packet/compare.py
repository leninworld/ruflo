"""New E1 aggregate contracts; no old attempt/test identities or fixed case count."""
import collections,json,sys
from pathlib import Path
from report_contract import inspect_report

def compare(old,new,old_root,new_root,new_files):
    a,ae,af,ar=inspect_report(old,old_root);b,be,bf,br=inspect_report(new,new_root)
    assert set(af)<=set(bf),'LOST_TEST_FILES'
    assert set(bf)-set(af)<=set(new_files),'UNEXPECTED_NEW_TEST_FILE'
    assert {x for x,s in bf.items() if s=='failed'} <= {x for x,s in af.items() if s=='failed'},'NEW_FAILING_FILE'
    assert not br or ar,'NEW_REPORT_LEVEL_FAILURE'
    assert set(be)<=set(ae),'NEW_COLLECTION_FAILURE'
    for name,message in be.items():assert message==ae[name],('CHANGED_COLLECTION_FAILURE',name)
    for key,previous in a.items():
        assert key in b,('LOST_ASSERTION',key)
        now=b[key]
        assert sum(now.values())==sum(previous.values()),('ASSERTION_MULTIPLICITY_CHANGED',key)
        assert now['passed']>=previous['passed'],('LOST_PASS',key)
        assert now['failed']<=previous['failed'],('NEW_FAILURE',key)
        assert sum(v for k,v in now.items() if k not in ['passed','failed'])<=sum(v for k,v in previous.items() if k not in ['passed','failed']),('NEW_SKIP',key)
    added={k:v for k,v in b.items() if k not in a}
    assert all(k[0] in new_files for k in added),'UNEXPECTED_NEW_ASSERTION'
    fallback='v3/@claude-flow/cli/__tests__/memory-search-sqljs-fallback-4876.test.ts'
    assert all(set(v)=={'passed'} or (k[0]==fallback and set(v)<={'pending','skipped'}) for k,v in added.items()),'NEW_CASE_NOT_PASSING'
    return {'state':'DIFFERENTIAL_NO_REGRESSION','baseline_failing_files':sorted(k for k,v in af.items() if v=='failed'),'candidate_failing_files':sorted(k for k,v in bf.items() if v=='failed'),'new_assertions':sum(sum(v.values()) for v in added.values()),'all_tests_passed':new['success'],'inherited_failures_disclosed':not new['success']}

if __name__=='__main__':
    old,new,out,oldroot,newroot,manifest=sys.argv[1:]
    files=[r['path'] for r in json.loads(Path(manifest).read_text())['files'] if '__tests__/' in r['path']]
    result=compare(json.loads(Path(old).read_text()),json.loads(Path(new).read_text()),oldroot,newroot,files)
    Path(out).write_text(json.dumps(result,indent=2)+'\n')
