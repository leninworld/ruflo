// DRAFT: metadata admission only, after source pack and before tarball acquisition.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,existsSync,readdirSync} from 'node:fs';
const read=p=>JSON.parse(readFileSync(p));
const retained=read('/inputs/consumer-retained-index.json').packages;
const lifecycle=new Map(read('/inputs/lifecycle-allowlist-for-review.json').entries.map(x=>[`${x.name}@${x.version}`,x]));
const phases=['preinstall','install','postinstall','prepare','prepublish','prepublishOnly','prepack','postpack'];
const admittedManifests=[];
function admitManifest(manifest,path){
 const scripts=Object.entries(manifest.scripts||{}).filter(([k])=>phases.includes(k));
 if(scripts.length){const approved=lifecycle.get(`${manifest.name}@${manifest.version}`);assert(approved,`unreviewed local lifecycle:${path}`);for(const[k,v]of scripts)assert.equal(approved.scripts[k],v,`unreviewed local script:${path}:${k}`);}
 admittedManifests.push({name:manifest.name,version:manifest.version,path,scripts});
}
const local=new Map();
for(const kind of ['cli','root','ruflo']){
 const manifest=read(`/work/extracted/${kind}/package/package.json`);
 admitManifest(manifest,`archive:${kind}`);local.set(manifest.name,{manifest,kind});
}
function walk(directory){if(!existsSync(directory))return;for(const entry of readdirSync(directory,{withFileTypes:true})){const path=directory+'/'+entry.name;if(entry.isDirectory())walk(path);else if(entry.name==='package.json')admitManifest(read(path),path);}}
for(const {kind}of local.values())walk(`/work/extracted/${kind}/package/node_modules`);
const evidence=[];
for(const kind of ['native','omit']){
 const lock=read(`/work/consumer-${kind}/package-lock.json`);
 assert.equal(lock.lockfileVersion,3,'unexpected consumer lock schema');
 for(const [path,p] of Object.entries(lock.packages)){
  if(!path)continue;
  assert(!p.link,`unexpected consumer link:${path}`);
  const name=path.split('node_modules/').at(-1);
  if(p.resolved?.startsWith('file:')){
   const source=local.get(name);assert(source,`unknown local archive:${name}`);
   assert.equal(p.version,source.manifest.version);
   const archive=read(`/work/evidence/${source.kind}-pack.json`)[0];
   assert.equal(p.integrity,archive.integrity,`local archive integrity:${path}`);
  }else if(p.inBundle){
   // Compare the actual current source-built staged manifest, not published metadata.
   const host=[...local.entries()].find(([host])=>path.startsWith(`node_modules/${host}/node_modules/`));
   assert(host,`unmapped bundled host:${path}`);
   const relative=path.slice(`node_modules/${host[0]}/`.length);
   assert(relative.startsWith('node_modules/')&&!relative.split('/').includes('..'));
   const manifestPath=`/work/extracted/${host[1].kind}/package/${relative}/package.json`;
   assert(existsSync(manifestPath),`unmapped bundled path:${path}`);
   const expected=read(manifestPath);assert.equal(expected.name,name);assert.equal(p.version,expected.version);admitManifest(expected,path);
   for(const field of ['dependencies','optionalDependencies','peerDependencies'])
    if(p[field])assert.deepEqual(p[field],expected[field]||{},`bundle ${field}:${path}`);
  }else{
   const expected=retained[`${name}@${p.version}`];
   assert(expected,`unpriced new registry version:${name}@${p.version}`);
   assert(expected.integrity && expected.tarball,`missing retained SRI/tarball:${name}@${p.version}`);
   assert.equal(p.integrity,expected.integrity,`registry SRI:${path}`);
   assert.equal(p.resolved,expected.tarball,`registry tarball:${path}`);
   assert.match(p.resolved,/^https:\/\/registry\.npmjs\.org\//);
   if(p.hasInstallScript){
    const reviewed=lifecycle.get(`${name}@${p.version}`);
    assert(reviewed,`lifecycle metadata missing:${name}@${p.version}`);
    assert(['preinstall','install','postinstall'].some(k=>reviewed.scripts[k])||reviewed.binding_gyp_present,
      `executable lifecycle metadata missing:${name}@${p.version}`);
    for(const stage of ['preinstall','install','postinstall'])if(expected.scripts?.[stage])
      assert.equal(reviewed.scripts[stage],expected.scripts[stage],`lifecycle mismatch:${path}:${stage}`);
   }
  }
  evidence.push({kind,path,name,version:p.version,resolved:p.resolved||null,integrity:p.integrity||null,inBundle:!!p.inBundle});
 }
}
writeFileSync('/work/evidence/consumer-admission.json',JSON.stringify({state:'GRAPH_MATCHES_RETAINED_METADATA_NOT_RUNTIME_PASS',packages:evidence,admittedManifests},null,2));
