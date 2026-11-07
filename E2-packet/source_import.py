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

