"""Exact public-only payload identity gate; execute only on hosted runner."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
P = Path(__file__).resolve().parent
seal = json.loads((P / 'packet-seal.json').read_text())
assert seal['status'] == 'SEALED_FOR_ONE_RUN'
if sys.argv[1:] == ['--tree']:
    actual = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'HEAD'], text=True).splitlines())
    expected = {'validation4834/' + name for name in seal['files']} | {'validation4834/packet-seal.json', '.github/workflows/ruflo4834.yml'}
    assert actual == expected, 'DIAGNOSTIC_TREE_SCOPE_MISMATCH'
    parents = subprocess.check_output(['git', 'rev-list', '--parents', '-n', '1', 'HEAD'], text=True).split()
    assert len(parents) == 1, 'DIAGNOSTIC_COMMIT_MUST_BE_PARENTLESS'
    assert os.environ['GITHUB_REF'] == 'refs/heads/validate-openai-config-4834-20261106'
files = {str(p.relative_to(P)) for p in P.rglob('*') if p.is_file()}
assert files == set(seal['files']) | {'packet-seal.json'}, 'PACKET_SCOPE_MISMATCH'
for name, row in seal['files'].items():
    path = P / name
    assert not path.is_symlink() and path.stat().st_size == row['bytes']
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    assert h.hexdigest() == row['sha256'], ('PACKET_HASH_MISMATCH', name)
print(json.dumps({'state': 'EXACT_PACKET_MATCH', 'files': len(seal['files'])}))
