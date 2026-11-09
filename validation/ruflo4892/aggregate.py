"""Full current-tree comparison, preserving every case and inherited failure."""
import hashlib
import json
from pathlib import Path
import sys
from compare_reports import compare
from compare_diagnostics import compare as compare_diagnostics
from evidence_index import verify as verify_evidence
from importlib.util import spec_from_file_location,module_from_spec
_spec=spec_from_file_location('consumer_comparison',Path(__file__).with_name('compare-consumer-locks.py'));_module=module_from_spec(_spec);_spec.loader.exec_module(_module)

P=Path(__file__).resolve().parent
PLAN=json.loads((P/'execution-plan.json').read_text())
ROOT,OUTPUT=map(Path,sys.argv[1:])
result={'state':'INCOMPLETE','profiles':{},'errors':[],'all_tests_passed':False}


def one(directory,name):
    values=list(directory.rglob(name));assert len(values)==1,('MISSING_OR_DUPLICATE_RESULT',str(directory),name,len(values))
    return json.loads(values[0].read_text())


def complete(directory):
    host=one(directory,'host-terminal.json');assert host['state']=='COMPLETE' and host['elapsed']<=PLAN['absolute_seconds']
    assert one(directory,'cleanup-result.json')['errors']==[]
    base=next(directory.rglob('evidence-index.json')).parent
    verify_evidence(base)
    # Cleanup confirmation includes actual inspect receipts, not exit0 assumptions.
    containers=list(base.glob('*-container.json'));assert containers
    for p in containers:
        state=json.loads(p.read_text())[0]['State'];assert not state['Running'] and not state['OOMKilled']
    return host


try:
    full_gates=[];all_tests=True
    for profile,definition in PLAN['profiles'].items():
        lanes={}
        for variant in ['baseline','candidate']:
            directory=ROOT/('ruflo4892-'+profile+'-'+variant);complete(directory)
            state=one(directory/'work','state.json')
            assert state['profile']==profile and state['variant']==variant
            assert one(directory/'work','frozen-install-lifecycles.json')['state']=='MATCH_REVIEWED_COMMANDS'
            assert one(directory/'work','test-terminal.json')['state']=='COMPLETE'
            lanes[variant]=(directory,state)
        before,after=[lanes[k][1] for k in ['baseline','candidate']]
        assert before['source_hashes']==after['source_hashes']
        for key in ['node','arch','availableParallelism','libraries','droppedOptionalPackages']:
            assert before['runtime_metadata'][key]==after['runtime_metadata'][key],('RUNTIME_GRAPH_DRIFT',profile,key)
        old={row['label']:row for row in before['commands'] if row['label'].startswith('official-')}
        new={row['label']:row for row in after['commands'] if row['label'].startswith('official-')}
        assert set(old)==set(new) and len(new)==len(definition['commands'])
        diagnostic_comparisons={}
        reports=[one(lanes[k][0]/'work','full-report.json') for k in ['baseline','candidate']] if definition.get('report') else None
        for label,row in new.items():
            assert row['command']==old[label]['command'] and row['cwd']==old[label]['cwd']
            assert row['exit']==0 or old[label]['exit']!=0,('NEW_COMMAND_FAILURE',profile,label)
            def logs(kind):
                folder=lanes[kind][0]/'work'
                return '\n'.join((folder/(label+'.'+extension)).read_text(errors='replace') for extension in ['stdout','stderr'])
            mode=definition['commands'][int(label.split('-')[1])]['diagnostic_mode']
            assert mode in ['structured-test-report-and-crashes','command-diagnostics']
            if mode=='structured-test-report-and-crashes':assert definition.get('report'),'NO_STRUCTURED_REPORT_FOR_TEST_COMMAND'
            diagnostic_comparisons[label]=compare_diagnostics(logs('baseline'),logs('candidate'),test_runner=mode=='structured-test-report-and-crashes',reports=reports)
        assessment={'diagnostics':diagnostic_comparisons,'official_commands':len(new),'inherited_nonzero_commands':[label for label,row in new.items() if row['exit']]}
        if definition.get('report'):
            old_report,new_report=[one(lanes[k][0]/'work','full-report.json') for k in ['baseline','candidate']]
            outcome=compare(old_report,new_report,[before['source_hashes'],after['source_hashes']],PLAN['expected_new_cases'][profile])
            assessment['full_test_comparison']=outcome;all_tests &= outcome['all_tests_passed']
            if definition.get('gating_suite'):full_gates.append(profile)
        # Exact new regression must pass in all candidate lanes; the paired
        # pristine baseline deliberately fails only the six declared cases.
        for variant in ['baseline','candidate']:
            focused=one(lanes[variant][0]/'work','focused-report.json')
            assert focused['numTotalTests']==26
            assert focused['numFailedTests']==(6 if variant=='baseline' else 0)
            assert focused['numPassedTests']==(20 if variant=='baseline' else 26)
            assert one(lanes[variant][0]/'work','early-terminal.json')['state']=='COMPLETE'
            metadata=one(lanes[variant][0]/'work','state.json')
            resolution=next(c for c in metadata['commands'] if c['label']=='actual-mcp-resolution')
            assert resolution['exit']==0
        if profile=='quality20':
            assert new['official-00']['exit']==0, 'STRICT_FULL_WORKSPACE_BUILD_FAILED'
        if profile=='shipping22':
            assessment['consumer_lock_comparison']=_module.compare(*[one(lanes[k][0]/'work','consumer-lock.json') for k in ['baseline','candidate']])
            shipped=[one(lanes[k][0]/'work','installed-proof.json') for k in ['baseline','candidate']]
            assert shipped[0]['sdk']==shipped[1]['sdk'], 'SHIPPED_SDK_DRIFT'
            for key in ['version','validator_sha256','utf16_guard']:
                assert shipped[0]['mcp'][key]==shipped[1]['mcp'][key], ('SHIPPED_MCP_DRIFT',key)
            for transport in ['stdio','http']:
                old_transport,new_transport=[s['transports'][transport] for s in shipped]
                # Only the advertised value description/maxLength may differ.
                tools=[json.loads(json.dumps(t['tool'])) for t in [old_transport,new_transport]]
                tools[1]['inputSchema']['properties']['value'].pop('maxLength')
                tools[1]['inputSchema']['properties']['value']['description']='Memory entry value'
                assert tools[0]==tools[1], ('UNRELATED_TOOL_CONTRACT_DRIFT',transport)
                assert old_transport['server']==new_transport['server'], 'SERVER_IDENTITY_DRIFT'
                assert [b['label'] for b in old_transport['boundaries']]==[b['label'] for b in new_transport['boundaries']]
                for old_boundary,new_boundary in zip(old_transport['boundaries'],new_transport['boundaries']):
                    assert old_boundary['accepted']==new_boundary['accepted']
                    if not old_boundary['accepted']:continue # Variant-specific exact refusal is checked by the driver.
                    payloads=[json.loads(b['reply']['content'][0]['text']) for b in [old_boundary,new_boundary]]
                    # bridgeHierarchicalStore returns a generated backend id.
                    # TieredMemoryStore.store returns no timestamp; retain and
                    # compare every other actual payload field without filtering.
                    for payload in payloads:
                        assert isinstance(payload['id'],(str,int)) and not isinstance(payload['id'],bool)
                        del payload['id']
                    assert payloads[0]==payloads[1], ('ACCEPTED_PAYLOAD_DRIFT',transport,old_boundary['label'])
            assessment['native_contract_comparison']='IDENTICAL_EXCEPT_REVIEWED_SCHEMA_FIELDS_AND_GENERATED_ID'
            for variant in ['baseline','candidate']:
                state=lanes[variant][1];assert state['shipping_state']=='FRESH_INSTALLED_CONSUMER_DISCONNECTED_MCP_TRANSPORT_PROOF_PASS'
                shipping=one(lanes[variant][0]/'work','installed-proof.json');assert shipping['state']=='PASS' and set(shipping['transports'])=={'stdio','http'}
                assert all(t['state']=='PASS' and t['cleanup']=='CLOSED' and len(t['boundaries'])==4 for t in shipping['transports'].values())
                assert shipping['mcp']['utf16_guard']=='PASS' and shipping['decoder_fixtures']=='PASS'
                assert one(lanes[variant][0]/'work','shipping-disconnected-proof-terminal.json')['state']=='COMPLETE'
                assessment[variant+'_shipping']='PASS'
        result['profiles'][profile]=assessment
    assert set(full_gates)=={'root20','v3-test22'},'NEW_TESTS_MISSING_FROM_BOTH_REAL_GATES'
    result.update(state='FULL_SCOPE_COMPARED',all_tests_passed=bool(all_tests),qualification='Inherited suite failures remain failures. FULL_SCOPE_COMPARED is not an all-tests-passed claim; exact results require Claude review before PR.')
except BaseException as error:
    result['errors'].append(repr(error));raise
finally:
    with OUTPUT.open('x') as output:json.dump(result,output,indent=2);output.write('\n')
