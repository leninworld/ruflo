"""Require all twelve full profiles and compare actual complete test reports."""
import json
from pathlib import Path
import sys

from compare_reports import compare

P = Path(__file__).resolve().parent
PLAN = json.loads((P / 'profile-plan.json').read_text())
M = json.loads((P / 'manifest.json').read_text())
assert PLAN['profiles'] == M['profiles']
root, output = map(Path, sys.argv[1:])
result = {'state': 'INCOMPLETE', 'profiles': {}, 'errors': [], 'regression_collected_in_gate': []}


def one(directory, filename):
    values = list(directory.rglob(filename))
    assert len(values) == 1, ('MISSING_OR_DUPLICATE_EVIDENCE', str(directory), filename, len(values))
    return json.loads(values[0].read_text())


try:
    admission = one(root / 'ruflo4834-result-d0', 'd0-admission.json')
    assert admission['admitted'] is True
    for name, profile in PLAN['profiles'].items():
        try:
            lanes = {}
            for variant in ['baseline', 'candidate']:
                directory = root / ('ruflo4834-result-' + name + '-' + variant)
                host = one(directory, 'host-terminal.json')
                assert host['state'] == 'COMPLETE'
                assert host['elapsed'] <= 3600
                terminal = one(directory, 'terminal.json')
                assert terminal['state'] in ['COMPLETE', 'COMPLETE_WITH_FAILURES']
                state = one(directory, 'state.json')
                assert state['profile'] == name and state['variant'] == variant
                assert state['lock_hashes_before_install'] == state['lock_hashes_after_install']
                actual = one(directory, 'actual-install-lifecycles.json')
                assert actual['state'] == 'MATCH_REVIEWED_COMMANDS' and actual['actual_count'] > 0
                cleanup = one(directory, 'cleanup-result.json')
                assert cleanup['errors'] == []
                lanes[variant] = {'directory': directory, 'state': state}
            before, after = (lanes[k]['state'] for k in ['baseline', 'candidate'])
            assert before['lock_hashes_before_install'] == after['lock_hashes_before_install']
            assert before['installed-runtime-metadata']['droppedOptionalPackages'] == after['installed-runtime-metadata']['droppedOptionalPackages']
            old_commands = {r['label']: r for r in before['commands'] if r['label'].startswith('official-')}
            new_commands = {r['label']: r for r in after['commands'] if r['label'].startswith('official-')}
            assert set(old_commands) == set(new_commands) and len(old_commands) == len(profile['commands'])
            for label, record in old_commands.items():
                updated = new_commands[label]
                assert record['command'] == updated['command'] and record['cwd'] == updated['cwd']
                assert updated['exit'] == 0 or record['exit'] != 0, ('NEW_COMMAND_FAILURE', name, label)
            assessment = {'official_commands': len(old_commands),
                'inherited_nonzero_commands': [k for k in new_commands if new_commands[k]['exit']]}
            if profile.get('report'):
                assessment['test_comparison'] = compare(
                    one(lanes['baseline']['directory'], 'full-report.json'),
                    one(lanes['candidate']['directory'], 'full-report.json'),
                    require_new_file=False, expected_new_cases=M['predicted_new_cases'])
                if profile.get('gating_suite') and assessment['test_comparison']['new_case_occurrences'] == M['predicted_new_cases']:
                    result['regression_collected_in_gate'].append(name)
            if name == 'shipping22':
                for kind in ['baseline', 'candidate']:
                    assert lanes[kind]['state']['shipping_state'] == 'FRESH_OFFLINE_INSTALL_AND_REAL_API_PASS'
                    shipping = one(lanes[kind]['directory'], 'shipping-result.json')
                    assert shipping['state'] == 'PASS' and shipping['kind'] == kind
                    assessment[kind + '_shipping'] = shipping
            result['profiles'][name] = assessment
        except Exception as error:
            result['profiles'][name] = {'state': 'INCOMPLETE', 'error': repr(error)}
            result['errors'].append({'profile': name, 'error': repr(error)})
    assert result['regression_collected_in_gate'], 'NEW_REGRESSIONS_NOT_COLLECTED_BY_CI_GATE'
    assert not result['errors'], 'ONE_OR_MORE_PROFILE_FAILURES'
    result['state'] = 'FULL_SCOPE_COMPARED'
except BaseException as error:
    result['errors'].append(repr(error))
    raise
finally:
    output.write_text(json.dumps(result, indent=2) + '\n')
