"""Import the sealed public Git pack inside approved hosted Docker.

Only new owned /work paths are accepted. No fetch, host cache copy, hooks,
submodules, deletions, LFS filters, or user configuration are involved.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
           GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_LFS_SKIP_SMUDGE='1')


def owned(path):
    path = Path(path)
    assert path.is_absolute() and str(path).startswith('/work/'), 'NOT_OWNED_HOSTED_PATH'
    assert '..' not in path.parts and not path.is_symlink(), 'UNSAFE_HOSTED_PATH'
    return path


def git(root, *args, input_file=None):
    argv = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=', '-C', str(root), *args]
    value = subprocess.run(argv, env=ENV, stdin=input_file,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    assert value.returncode == 0, (argv, value.returncode, value.stderr.decode(errors='replace')[:4000])
    return value.stdout


def blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def import_source(destination, pack, metadata, identities):
    destination = owned(destination)
    assert not destination.exists(), 'DO_NOT_REUSE_OR_OVERWRITE_SOURCE'
    assert re.fullmatch('[0-9a-f]{40}', metadata['base'])
    assert re.fullmatch('[0-9a-f]{40}', metadata['root_tree'])
    assert metadata['shallow'] == [metadata['base']], 'EXPECTED_CURRENT_DEPTH_ONE_ROOT'
    pack = Path(pack)
    assert pack.stat().st_size == metadata['bytes'], 'PACK_SIZE_MISMATCH'
    assert hashlib.sha256(pack.read_bytes()).hexdigest() == metadata['sha256'], 'PACK_HASH_MISMATCH'
    destination.mkdir(parents=True, exist_ok=False)
    template = owned(destination.parent / (destination.name + '-empty-template'))
    template.mkdir(exist_ok=False)
    git(destination, 'init', '--quiet', '--template=' + str(template))
    with pack.open('rb') as stream:
        git(destination, 'index-pack', '--stdin', input_file=stream)
    (destination / '.git/shallow').write_text(metadata['base'] + '\n')
    git(destination, 'update-ref', 'refs/heads/validated-source', metadata['base'])
    git(destination, 'checkout', '--quiet', '--detach', metadata['base'])
    git(destination, 'remote', 'add', 'origin', 'https://github.com/ruvnet/ruflo.git')
    git(destination, 'update-ref', 'refs/remotes/origin/main', metadata['base'])
    assert git(destination, 'rev-parse', 'HEAD').decode().strip() == metadata['base']
    assert git(destination, 'rev-parse', 'HEAD^{tree}').decode().strip() == metadata['root_tree']
    git(destination, 'fsck', '--connectivity-only', '--no-dangling')
    assert not git(destination, 'status', '--porcelain', '--untracked-files=all').strip()
    for row in identities:
        assert blob(destination / row['path']) == row['git_blob'], row['path']
    return {'state': 'VERIFIED_PUBLIC_SOURCE', 'base': metadata['base'],
            'root_tree': metadata['root_tree'], 'files_verified': len(identities),
            'source_pack_sha256': metadata['sha256'], 'network_fetch': False}


def verify_source(source, manifest, identities, kind):
    source = owned(source)
    assert kind in ['baseline', 'baseline-seeded', 'candidate']
    assert git(source, 'rev-parse', 'HEAD').decode().strip() == manifest['base']
    assert git(source, 'rev-parse', 'HEAD^{tree}').decode().strip() == manifest['root_tree']
    changed = {row['path']: row for row in manifest['files']}
    for row in identities:
        name = row['path']
        if kind == 'candidate' and name in changed:
            continue
        assert blob(source / name) == row['git_blob'], ('PINNED_INPUT_CHANGED', name)
    for name, row in changed.items():
        file = source / name
        if kind == 'candidate' or (kind == 'baseline-seeded' and name == manifest['new_test_path']):
            assert hashlib.sha256(file.read_bytes()).hexdigest() == row['sha256'], ('CANDIDATE_HASH_MISMATCH', name)
        elif row['before_blob'] is None:
            assert not file.exists(), ('BASELINE_HAS_NEW_SOURCE', name)
        else:
            assert blob(file) == row['before_blob'], ('BASELINE_INPUT_CHANGED', name)
    expected = {name for name, row in changed.items() if row['before_blob'] is not None} if kind == 'candidate' else set()
    actual = set(git(source, 'diff', '--name-only', '-z').decode().split('\0')) - {''}
    assert actual == expected, ('UNEXPECTED_TRACKED_SOURCE_DELTA', sorted(actual - expected), sorted(expected - actual))
    git(source, 'diff', '--check')
    return {'state': 'SOURCE_IDENTITIES_MATCH', 'kind': kind, 'base': manifest['base'],
            'tracked_delta': sorted(actual),
            'git_status': git(source, 'status', '--porcelain', '--untracked-files=all').decode()}


def seed(source, packet, manifest, identities):
    verify_source(source, manifest, identities, 'baseline')
    path = owned(Path(source) / manifest['new_test_path'])
    assert not path.exists()
    payload = (Path(packet) / 'candidate-files' / manifest['new_test_path']).read_bytes()
    expected = next(r['sha256'] for r in manifest['files'] if r['path'] == manifest['new_test_path'])
    assert hashlib.sha256(payload).hexdigest() == expected
    with path.open('xb') as f:
        f.write(payload)
    return verify_source(source, manifest, identities, 'baseline-seeded')


def apply(source, packet, manifest, identities, preserved_seed=None):
    source = owned(source)
    if preserved_seed is not None:
        verify_source(source, manifest, identities, 'baseline-seeded')
        target = owned(preserved_seed)
        assert not target.exists()
        (source / manifest['new_test_path']).rename(target)
    verify_source(source, manifest, identities, 'baseline')
    patch = Path(packet) / 'I1.patch'
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == manifest['patch_sha256']
    git(source, 'apply', '--check', str(patch))
    git(source, 'apply', str(patch))
    return verify_source(source, manifest, identities, 'candidate')


def fixture():
    root = owned('/work/fixtures/source-cache')
    root.mkdir(parents=True, exist_ok=False)
    origin = root / 'origin'; origin.mkdir()
    template = root / 'empty-template'; template.mkdir()
    git(origin, 'init', '--quiet', '--template=' + str(template))
    (origin / 'value.txt').write_text('base\n')
    git(origin, 'add', 'value.txt')
    git(origin, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    base = git(origin, 'rev-parse', 'HEAD').decode().strip()
    tree = git(origin, 'rev-parse', 'HEAD^{tree}').decode().strip()
    pack = root / 'fixture.pack'
    with pack.open('wb') as output:
        result = subprocess.run(['git', '-C', str(origin), 'pack-objects', '--stdout', '--revs'],
                                input=(base + '\n').encode(), stdout=output, stderr=subprocess.PIPE,
                                env=ENV, timeout=30)
    assert result.returncode == 0
    metadata = {'base': base, 'root_tree': tree, 'bytes': pack.stat().st_size,
                'sha256': hashlib.sha256(pack.read_bytes()).hexdigest(), 'shallow': [base]}
    identities = [{'path': 'value.txt', 'git_blob': blob(origin / 'value.txt')}]
    assert import_source(root / 'copy', pack, metadata, identities)['state'] == 'VERIFIED_PUBLIC_SOURCE'
    assert (root / 'copy/value.txt').read_text() == 'base\n'
    wrong = dict(metadata, sha256='0' * 64)
    try:
        import_source(root / 'wrong-hash', pack, wrong, identities)
    except AssertionError:
        pass
    else:
        raise AssertionError('ACCEPTED_WRONG_PACK_HASH')
    try:
        import_source(root / 'copy', pack, metadata, identities)
    except AssertionError:
        pass
    else:
        raise AssertionError('OVERWROTE_EXISTING_CHECKOUT')
    print(json.dumps({'fixture': 'PASS', 'actual_git_pack_import_checkout': True,
                      'wrong_hash_rejected': True, 'collision_rejected': True}))


if __name__ == '__main__':
    assert sys.argv[1:] == ['fixture'], 'Only the fixture CLI exists; controllers import the source operations.'
    fixture()
