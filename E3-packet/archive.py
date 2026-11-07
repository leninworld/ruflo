"""Inspect/extract only fresh npm-pack outputs; no links/devices/path escapes."""
import json,sys,tarfile
from pathlib import Path,PurePosixPath
archive,destination=map(Path,sys.argv[1:]);assert not destination.exists()
with tarfile.open(archive,'r:gz') as t:
 members=t.getmembers();assert 0<len(members)<=200000
 assert sum(m.size for m in members)<=1024**3
 for m in members:
  n=PurePosixPath(m.name);assert n.parts and n.parts[0]=='package' and not n.is_absolute() and '..' not in n.parts
  assert m.isfile() or m.isdir(),'NON_REGULAR_ARCHIVE_MEMBER'
 destination.mkdir()
 for m in members:
  target=destination/PurePosixPath(m.name)
  if m.isdir():target.mkdir(parents=True,exist_ok=True)
  else:
   target.parent.mkdir(parents=True,exist_ok=True)
   with t.extractfile(m) as src,target.open('xb') as dst:
    while data:=src.read(65536):dst.write(data)
   target.chmod(m.mode&0o777)
print(json.dumps({'archive':str(archive),'members':len(members),'expanded_bytes':sum(m.size for m in members)}))
