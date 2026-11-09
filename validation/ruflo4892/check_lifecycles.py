"""Observe real npm info/pnpm NDJSON lifecycle starts, never infer from requiresBuild."""
import json
from pathlib import Path
import re
import sys

PHASES = {'preinstall', 'install', 'postinstall', 'prepare'}


def approved_inventory(path):
    data = json.loads(Path(path).read_text())
    rows = data['entries']
    allowed = set()
    for row in rows:
        for phase, command in row['scripts'].items():
            allowed.add((row['name'], row['version'], phase, command))
        if row.get('binding_gyp_present') and 'install' not in row['scripts']:
            allowed.add((row['name'], row['version'], 'install', 'node-gyp rebuild'))
    return allowed


def npm_events(text):
    result = []
    for line in text.splitlines():
        match = re.match(r'^npm info run (\S+) (preinstall|install|postinstall|prepare) (\S+) (.+)$', line)
        if not match:
            continue
        identity, phase, path, command = match.groups()
        if path.startswith('{'):
            continue  # Completion event, not a script start.
        name, version = identity.rsplit('@', 1)
        result.append({'name': name, 'version': version, 'phase': phase,
                       'command': command, 'path': path, 'origin': 'npm info run'})
    return result


def pnpm_events(text, source_root='/work/source'):
    result = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('name') != 'pnpm:lifecycle' or not isinstance(event.get('script'), str):
            continue
        phase = event.get('stage')
        assert phase in PHASES, 'UNKNOWN_LIFECYCLE_STAGE'
        cwd = event.get('wd')
        assert isinstance(cwd, str) and cwd.startswith(source_root.rstrip('/') + '/'), 'UNEXPECTED_LIFECYCLE_CWD'
        manifest = json.loads((Path(cwd) / 'package.json').read_text())
        result.append({'name': manifest['name'], 'version': manifest['version'],
                       'phase': phase, 'command': event['script'], 'path': cwd,
                       'origin': 'pnpm:lifecycle script'})
    return result


def verify(events, allowed, required):
    assert events, 'NO_ACTUAL_LIFECYCLE_START_EVENTS'
    unknown = [e for e in events if (e['name'], e['version'], e['phase'], e['command']) not in allowed]
    observed = {(e['name'], e['phase']) for e in events}
    assert not unknown, ('UNREVIEWED_EXECUTED_LIFECYCLE', unknown)
    assert set(required) <= observed, ('EXPECTED_LIFECYCLE_NOT_OBSERVED', sorted(set(required) - observed))
    return {'state': 'MATCH_REVIEWED_COMMANDS', 'actual_events': events,
            'actual_count': len(events), 'unknown': unknown,
            'note': 'Only actual observed execution is recorded. requiresBuild and prepare metadata do not prove execution.'}


def fixture():
    assert approved_inventory(Path(__file__).resolve().parent / 'lifecycle-allowlist-for-review.json'), 'REAL_ALLOWLIST_EMPTY'
    # Fixture format mirrors actual npm output; completion lines do not become starts.
    text = '\n'.join([
        'npm info run esbuild@0.27.2 postinstall node_modules/esbuild node install.js',
        'npm info run esbuild@0.27.2 postinstall { code: 0, signal: null }',
        'npm info run @example/scoped@1.0.0 install node_modules/@example/scoped node build.js',
    ])
    events = npm_events(text)
    assert len(events) == 2
    allowed = {('esbuild', '0.27.2', 'postinstall', 'node install.js'),
               ('@example/scoped', '1.0.0', 'install', 'node build.js')}
    assert verify(events, allowed, [('esbuild', 'postinstall')])['actual_count'] == 2
    for label, bad, allow, require in [
        ('unknown command', [dict(events[0], command='node changed.js')], allowed, []),
        ('missing expected command', events[:1], allowed, [('@example/scoped', 'install')]),
        ('empty observation', [], allowed, []),
    ]:
        try:
            verify(bad, allow, require)
        except AssertionError:
            pass
        else:
            raise AssertionError('FIXTURE_ACCEPTED_' + label)
    # PNPM parser fixture uses a real temporary manifest in the approved container.
    fixture_root = '/work/fixtures/lifecycle'
    fixture_dir = Path(fixture_root) / 'package'
    fixture_dir.mkdir(parents=True, exist_ok=False)
    (fixture_dir / 'package.json').write_text('{"name":"fixture","version":"1.0.0"}')
    data = json.dumps({'name': 'pnpm:lifecycle', 'stage': 'postinstall',
                       'script': 'node install.js', 'wd': str(fixture_dir)})
    pnpm = pnpm_events(data, source_root=fixture_root)
    assert verify(pnpm, {('fixture', '1.0.0', 'postinstall', 'node install.js')}, [('fixture', 'postinstall')])['actual_count'] == 1
    print(json.dumps({'fixture': 'PASS', 'npm_scoped_name': True, 'completion_not_start': True,
                      'pnpm_actual_manifest': True, 'negative_controls': 3}))


if __name__ == '__main__':
    if sys.argv[1:] == ['fixture']:
        fixture()
    else:
        assert len(sys.argv) == 6, 'usage: check_lifecycles.py MANAGER LOG INVENTORY GLOBAL_GUARD_LOG OUTPUT'
        manager, logfile, inventory, guard, output = sys.argv[1:]
        assert manager in ['npm', 'pnpm']
        assert not Path(guard).exists() or not Path(guard).read_text().strip(), 'GLOBAL_FALLBACK_WAS_ATTEMPTED'
        logs = Path(logfile).read_text(errors='replace')
        events = npm_events(logs) if manager == 'npm' else pnpm_events(logs)
        required = [('esbuild', 'postinstall')] if manager == 'npm' else [('@claude-flow/cli', 'postinstall')]
        result = verify(events, approved_inventory(inventory), required)
        with Path(output).open('x') as f:
            json.dump(result, f, indent=2)
            f.write('\n')
