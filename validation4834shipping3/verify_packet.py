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
    original = set(json.loads((P / 'original-diagnostic-tree.json').read_text()))
    expected = original | {'validation4834shipping3/' + name for name in seal['files']} | {'validation4834shipping3/packet-seal.json','.github/workflows/ruflo4834shipping3.yml'}
    assert actual == expected, 'DIAGNOSTIC_TREE_SCOPE_MISMATCH'
    header = subprocess.check_output(['git','cat-file','-p','HEAD'],text=True).split('\n\n',1)[0]
    parents = [line.split()[1] for line in header.splitlines() if line.startswith('parent ')]
    assert parents == ['46e1fefda3a1135f63419701e6701cf3c07c0bea'], 'UNEXPECTED_PARENT'
    assert os.environ['GITHUB_REF']=='refs/heads/validate-openai-config-4834-shipping3-20261106'
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

assert hashlib.sha256((P/'workflow.yml').read_bytes()).hexdigest()==hashlib.sha256((P.parent/'.github/workflows/ruflo4834shipping3.yml').read_bytes()).hexdigest(),'WORKFLOW_BYTE_DRIFT'
