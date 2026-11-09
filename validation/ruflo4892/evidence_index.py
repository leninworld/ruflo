"""Bounded sealed-evidence decoder and realistic filesystem failure fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile


def verify(directory):
    root=Path(directory).resolve(strict=True)
    records=json.loads((root/'evidence-index.json').read_text())
    assert isinstance(records,list) and 0<len(records)<=5000,'BAD_EVIDENCE_INDEX'
    names=set();total=0
    for row in records:
        name=row['name'];assert isinstance(name,str) and name and not name.startswith('/') and '..' not in Path(name).parts,'UNSAFE_EVIDENCE_PATH'
        assert name not in names,'DUPLICATE_EVIDENCE';names.add(name)
        path=root
        for part in Path(name).parts:
            path/=part;assert not path.is_symlink(),'SYMLINK_EVIDENCE'
        assert path.is_file() and path.resolve().is_relative_to(root),'MISSING_EVIDENCE'
        assert type(row['bytes']) is int and 0<=row['bytes']<=32*1024**2,'EVIDENCE_SIZE_CAP'
        total+=row['bytes'];assert total<=256*1024**2,'TOTAL_EVIDENCE_CAP'
        assert path.stat().st_size==row['bytes'],'EVIDENCE_BYTE_MISMATCH'
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'EVIDENCE_HASH_MISMATCH'
    return {'files':len(names),'bytes':total,'sealed':True}


def fixture():
    root=Path(tempfile.mkdtemp(prefix='sealed-evidence-',dir='/work'))
    value=root/'raw.json';value.write_bytes(b'{"fixture":"decoded"}\n')
    row={'name':'raw.json','bytes':value.stat().st_size,'sha256':hashlib.sha256(value.read_bytes()).hexdigest()}
    index=root/'evidence-index.json';index.write_text(json.dumps([row]));assert verify(root)['files']==1
    rejected=[]
    for label,rows in [('path escape',[dict(row,name='../raw.json')]),('hash mismatch',[dict(row,sha256='0'*64)]),('duplicate',[row,row]),('byte mismatch',[dict(row,bytes=row['bytes']+1)])]:
        index.write_text(json.dumps(rows))
        try:verify(root)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('ACCEPTED_'+label)
    print(json.dumps({'fixture':'PASS','negative_controls':rejected,'actual_json_bytes_sha256':True}))


if __name__=='__main__':
    assert sys.argv[1:]==['fixture'];fixture()
