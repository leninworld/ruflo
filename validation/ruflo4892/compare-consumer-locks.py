"""Paired real consumer locks; only the tested CLI tarball integrity may differ."""
import copy
import json
from pathlib import Path
import sys

def compare(before, after, package='node_modules/@claude-flow/cli'):
    values = [copy.deepcopy(value) for value in [before, after]]
    integrities = []
    for value in values:
        assert value['lockfileVersion'] == 3
        row = value['packages'][package]
        integrity = row.pop('integrity')
        assert isinstance(integrity, str) and integrity.startswith('sha512-')
        integrities.append(integrity)
    assert values[0] == values[1], 'CONSUMER_GRAPH_DRIFT_BEYOND_CLI_INTEGRITY'
    return {'state': 'PASS', 'allowed_difference': package+'.integrity', 'integrities': integrities}

def fixture():
    # Actual npm-generated registry-shaped lock from the preceding real offline lifecycle fixture.
    before = json.loads(Path('/work/evidence/npm-fixture-package-lock.json').read_text())
    after = copy.deepcopy(before)
    key = 'node_modules/scoped-lifecycle-fixture'
    after['packages'][key]['integrity'] = 'sha512-synthetic-other-fixture-tarball'
    assert compare(before, after, key)['state'] == 'PASS'
    for field, value in [('version', '99.0.0'), ('resolved', 'https://example.invalid/changed.tgz')]:
        bad = copy.deepcopy(after); bad['packages'][key][field] = value
        try: compare(before, bad, key)
        except AssertionError: pass
        else: raise AssertionError('CONSUMER_COMPARATOR_ACCEPTED_'+field)
    print(json.dumps({'state': 'FIXTURE_PASS', 'actual_npm_lock': True, 'negative_controls': 2}))

if __name__ == '__main__':
    if sys.argv[1:] == ['fixture']:
        fixture()
    else:
        before, after, output = map(Path, sys.argv[1:])
        result = compare(json.loads(before.read_text()), json.loads(after.read_text()))
        output.write_text(json.dumps(result, indent=2)+'\n')
