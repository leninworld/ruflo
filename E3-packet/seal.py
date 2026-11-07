"""Canonical complete packet byte seal; standard-library-only bootstrap."""
import hashlib,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
def verify_packet(root):
 root=Path(root);manifest=json.loads((root/'manifest.json').read_text());rows=[]
 for path in sorted(root.rglob('*')):
  assert not path.is_symlink(),'PACKET_SYMLINK'
  if not path.is_file() or path.relative_to(root).as_posix()=='approval.json':continue
  data=path.read_bytes()
  if path.relative_to(root).as_posix()=='manifest.json':
   normalized=dict(manifest);normalized['sealed_packet_sha256']=None
   data=json.dumps(normalized,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
  rows.append([path.relative_to(root).as_posix(),len(data),hashlib.sha256(data).hexdigest()])
 digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 assert digest==manifest['sealed_packet_sha256'],'PACKET_BYTE_SEAL_MISMATCH'
 assert hashlib.sha256((root/'consumer-retained-index.json').read_bytes()).hexdigest()==manifest['consumer_retained_index_sha256'],'CONSUMER_INDEX_CHANGED'
 return manifest,digest

if __name__=='__main__':
 m,d=verify_packet(Path(__file__).resolve().parent);print(d)
