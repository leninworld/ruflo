"""Offline G0 inventory admission before network and lifecycle execution."""
import hashlib,json,re
from pathlib import Path
P=Path('/inputs');S=Path('/work/source');E=Path('/work/evidence')
M=json.loads((P/'manifest.json').read_text());path=P/'lifecycle-allowlist-for-review.json'
assert hashlib.sha256(path.read_bytes()).hexdigest()==M['lifecycle_allowlist_sha256']
rows=json.loads(path.read_text())['entries'];allowed={(r['name'],r['version']):r for r in rows}
def applies(values,actual):
 return not values or (actual not in [x[1:] for x in values if x.startswith('!')] and (not any(not x.startswith('!') for x in values) or actual in values))
def admit(name,version):
 assert (name,version) in allowed,('UNREVIEWED_PRE_NETWORK_LIFECYCLE_METADATA',name,version)
 return allowed[name,version]
report={'root':[],'pnpm':[],'workspaces':[],'known_root_execution_provenance':'Identical root lock a64678df executed in retained4834 root lanes; no newpayload/runtime claim'}
root=json.loads((S/'package-lock.json').read_text())
assert hashlib.sha256((S/'package-lock.json').read_bytes()).hexdigest()==M['lock_hashes']['package-lock.json']
for name,p in root['packages'].items():
 if not p.get('hasInstallScript'):continue
 applicable=applies(p.get('os',[]),'linux') and applies(p.get('cpu',[]),'x64')
 if applicable:admit(name.split('node_modules/')[-1],p['version'])
 report['root'].append({'path':name,'version':p['version'],'applicable':applicable,'os':p.get('os',[]),'cpu':p.get('cpu',[])})
raw=(S/'v3/pnpm-lock.yaml').read_text();assert hashlib.sha256(raw.encode()).hexdigest()==M['lock_hashes']['v3/pnpm-lock.yaml']
expected=json.loads((P/'pnpm-build-obligations.json').read_text());parsed=[]
for hit in re.finditer(r'^  (/[^\n]+):\n((?:.*\n)*?)(?=^  /|\Z)',raw,re.M):
 if '    requiresBuild: true' not in hit[2]:continue
 key=hit[1].strip("'").split('(')[0][1:];name,version=key.rsplit('@',1)
 def arr(field):
  m=re.search(r'^    '+field+r': \[([^\]]*)\]',hit[2],re.M);return [x.strip(" '\"") for x in m[1].split(',')] if m else []
 row={'key':hit[1],'name':name,'version':version,'os':arr('os'),'cpu':arr('cpu')};parsed.append(row)
 applicable=applies(row['os'],'linux') and applies(row['cpu'],'x64');blocked=name in expected['neverBuiltDependencies']
 if applicable and not blocked:admit(name,version)
 report['pnpm'].append(dict(row,applicable=applicable,neverBuilt=blocked))
assert parsed==expected['rows'],'PNPM_BUILD_OBLIGATIONS_CHANGED'
phases=['preinstall','install','postinstall','prepare','prepublish','prepublishOnly','prepack','postpack']
for row in json.loads((P/'workspace-lifecycle-inputs.json').read_text()):
 file=S/row['path'];assert hashlib.sha256(file.read_bytes()).hexdigest()==row['manifest_sha256'];p=json.loads(file.read_text());scripts={k:v for k,v in p.get('scripts',{}).items() if k in phases};assert scripts==row['scripts']
 if scripts:
  entry=admit(p['name'],p['version'])
  for phase,command in scripts.items():assert entry['scripts'].get(phase)==command,('WORKSPACE_SCRIPT_CHANGED',row['path'],phase)
 report['workspaces'].append(row)
(E/'pre-network-lifecycle-admission.json').write_text(json.dumps(report,indent=2)+'\n')
