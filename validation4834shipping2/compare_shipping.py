"""Keep original failure final. Verify only the separate final shipping proof."""
import hashlib
import json
from pathlib import Path
import sys

root, output = map(Path,sys.argv[1:])
result={'state':'INCOMPLETE','attempt':2,'previous_run':42262920667,
        'previous_run_conclusion':'failure','variants':{},'errors':[]}


def one(folder,name):
    found=list(folder.rglob(name))
    assert len(found)==1,(name,len(found))
    return json.loads(found[0].read_text())


def canonical(admission):
    # Only own branch-built tarball content differs. Consumer graph and all
    # other package metadata/lock bytes must agree, including omitted optionals.
    lock=admission['lock']
    lock=json.loads(json.dumps(lock))
    for path,item in lock['packages'].items():
        if path=='node_modules/@claude-flow/cli':
            item.pop('integrity',None)
    return {'lock':lock,'rows':[{k:v for k,v in row.items()if k!='manifest_sha256'}
                              for row in admission['rows']], 'edges':admission['edges']}


try:
    for variant in ['baseline','candidate']:
        folder=root/('ruflo4834-shipping2-'+variant)
        host=one(folder,'host-terminal.json');terminal=one(folder,'terminal.json')
        state=one(folder,'state.json');admission=one(folder,'consumer-admission.json')
        smoke=one(folder,'shipping-result.json');cleanup=one(folder,'cleanup-result.json')
        assert host['state']==terminal['state']=='COMPLETE'
        assert admission['state']=='COMPLETE_REVIEWED_CONSUMER_GRAPH'and not admission['errors']
        assert smoke['state']=='PASS'and smoke['kind']==variant
        assert state['shipping_state']=='FRESH_OFFLINE_INSTALL_AND_REAL_API_PASS'
        assert not cleanup['errors']and all(c['exit']==0 for c in state['commands'])
        lockfiles=list(folder.rglob('consumer-lock.json'));assert len(lockfiles)==1
        assert hashlib.sha256(lockfiles[0].read_bytes()).hexdigest()==state['consumer_lock_sha256']
        result['variants'][variant]={'admission':admission,'smoke':smoke,'host':host}
    assert canonical(result['variants']['baseline']['admission'])==canonical(result['variants']['candidate']['admission']),'CONSUMER_GRAPH_DRIFT_BETWEEN_VARIANTS'
    result['state']='BOTH_FRESH_INSTALLED_PROOFS_PASS'
except BaseException as error:
    result['errors'].append(repr(error))
    raise
finally:
    output.write_text(json.dumps(result,indent=2)+'\n')
