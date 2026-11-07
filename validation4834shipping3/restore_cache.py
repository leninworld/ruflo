"""Restore only sealed public package-manager cache bytes on a hosted runner."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
root, destination = map(Path, sys.argv[1:])
assert destination.is_absolute() and str(destination).startswith(os.environ['RUNNER_TEMP'] + '/')
assert not destination.exists()
payload = root / 'dependency-cache.tgz'
assert payload.stat().st_size <= 3 * 2**30, 'CACHE_HANDOFF_BYTES'
digest = hashlib.sha256()
with payload.open('rb') as f:
    for block in iter(lambda: f.read(1024 * 1024), b''):
        digest.update(block)
assert digest.hexdigest() == (root / 'dependency-cache.sha256').read_text().split()[0]
destination.mkdir()
with tarfile.open(payload, 'r:gz') as archive:
    members = archive.getmembers()
    assert len(members) <= 200000 and sum(m.size for m in members) <= 6 * 2**30
    for member in members:
        name = PurePosixPath(member.name)
        assert not name.is_absolute() and '..' not in name.parts
        assert name.parts and name.parts[0] in ['npm20', 'npm22', 'pnpm']
        assert member.isdir() or member.isfile(), 'NO_LINK_DEVICE_OR_SPECIAL_CACHE_ENTRY'
    archive.extractall(destination, members=members, filter='data')
print('VERIFIED_PUBLIC_PACKAGE_MANAGER_CACHES_ONLY')
