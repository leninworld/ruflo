"""Inspect actual unpacked consumer without executing its scripts."""
import json
from pathlib import Path
import sys

import copy

def verify(consumer, frozen, required_package="@claude-flow/cli"):
    consumer=Path(consumer)
    assert consumer.resolve().is_relative_to(Path("/work"))
    identities = {(row['name'], row['version']): row for row in frozen['identities']}
    lock = json.loads((consumer/'package-lock.json').read_text())
    actual = []
    absent_optional = []
    for name, entry in lock['packages'].items():
        if not name:
            continue
        file = consumer/name/'package.json'
        if not file.is_file():
            assert entry.get('optional') is True, ('REQUIRED_CONSUMER_PACKAGE_MISSING', name)
            absent_optional.append(name)
            continue
        metadata = json.loads(file.read_text())
        key = (metadata['name'], metadata['version'])
        # Registry dependency prepare is not an install lifecycle. Local CLI
        # postinstall and implicit binding.gyp install are still checked below.
        scripts = {phase: value for phase, value in metadata.get('scripts', {}).items()
                   if phase in ['preinstall', 'install', 'postinstall']}
        if (file.parent/'binding.gyp').is_file() and 'install' not in scripts:
            scripts['install'] = 'node-gyp rebuild'
        if not scripts:
            continue
        assert key in identities, ('CONSUMER_LIFECYCLE_VERSION_NOT_FROZEN', key)
        expected = identities[key]
        if expected['kind'] == 'registry':
            assert entry.get('integrity') in expected['integrities'], ('CONSUMER_LIFECYCLE_INTEGRITY_NOT_FROZEN', key)
        allowed = frozen['allowed_commands']
        for phase, command in scripts.items():
            assert any(row['name'] == key[0] and row['version'] == key[1]
                       and row['phase'] == phase and row['command'] == command for row in allowed), (
                           'UNREVIEWED_CONSUMER_LIFECYCLE', key, phase, command)
        actual.append({'name': key[0], 'version': key[1], 'path': name, 'scripts': scripts,
                       'integrity': entry.get('integrity'), 'kind': expected['kind']})
    assert any(row['name'] == required_package for row in actual)
    return {'state': 'PASS', 'actual': actual, 'absent_optional': absent_optional,
            'qualification': 'Optional absence is recorded, not a functionality pass. Actual durable stdio/HTTP storage still must succeed.'}


def fixture():
    consumer=Path(json.loads(Path('/work/evidence/npm-fixture-path.json').read_text())['consumer'])
    lock=json.loads((consumer/'package-lock.json').read_text())
    key='node_modules/scoped-lifecycle-fixture'
    meta=json.loads((consumer/key/'package.json').read_text())
    frozen={'identities':[{'name':meta['name'],'version':meta['version'],'kind':'registry','integrities':[lock['packages'][key]['integrity']]}],
            'allowed_commands':[{'name':meta['name'],'version':meta['version'],'phase':'postinstall','command':meta['scripts']['postinstall']}]}
    assert verify(consumer,frozen,meta['name'])['state']=='PASS'
    for label in ['version','integrity','command']:
        bad=copy.deepcopy(frozen)
        if label=='version':bad['identities'][0]['version']='99.0.0'
        elif label=='integrity':bad['identities'][0]['integrities']=['sha512-not-the-actual-tarball']
        else:bad['allowed_commands'][0]['command']='node changed-script.js'
        try:verify(consumer,bad,meta['name'])
        except AssertionError:pass
        else:raise AssertionError('ACCEPTED_UNFROZEN_CONSUMER_'+label)
    print(json.dumps({'state':'FIXTURE_PASS','actual_npm_installed_package':True,'negative_controls':3}))


if __name__=='__main__':
    if sys.argv[1:]==['fixture']:fixture()
    else:
        consumer,frozen_path,output=map(Path,sys.argv[1:])
        result=verify(consumer,json.loads(frozen_path.read_text()))
        output.write_text(json.dumps(result,indent=2)+'\n')
