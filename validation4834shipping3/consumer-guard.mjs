// Hosted Docker only. Inspect every installed manifest and lock entry before
// permitting lifecycle execution. Always save the full inventory on failure.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync,readFileSync,readdirSync,lstatSync,writeFileSync,mkdirSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname,join,resolve,relative} from 'node:path';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const read = path => JSON.parse(readFileSync(path,'utf8'));
const identity = value => value.name+'@'+value.version;
const phases=['preinstall','install','postinstall'];
const scripts = m => Object.fromEntries(phases.filter(k=>m.scripts?.[k]).map(k=>[k,m.scripts[k]]));
const sorted = value => JSON.stringify(Object.entries(value||{}).sort(([a],[b])=>a.localeCompare(b)));

function inventory(consumer) {
  const output=[];
  function visit(modules) {
    if(!existsSync(modules))return;
    assert(!lstatSync(modules).isSymbolicLink(),'SYMLINK_MODULES');
    for(const entry of readdirSync(modules).sort()) {
      if(entry.startsWith('.'))continue;
      const path=join(modules,entry);
      assert(!lstatSync(path).isSymbolicLink(),'SYMLINK_PACKAGE');
      if(entry.startsWith('@')) {
        for(const scoped of readdirSync(path).sort())add(join(path,scoped));
      } else add(path);
    }
  }
  function add(path) {
    assert(!lstatSync(path).isSymbolicLink(),'SYMLINK_SCOPED_PACKAGE');
    const manifest=join(path,'package.json');
    assert(existsSync(manifest),'PACKAGE_MANIFEST_MISSING:'+path);
    const bytes=readFileSync(manifest), metadata=JSON.parse(bytes);
    output.push({path:relative(consumer,path),metadata,manifest_sha256:hash(bytes),
                 scripts:scripts(metadata),binding_gyp:existsSync(join(path,'binding.gyp'))});
    visit(join(path,'node_modules'));
  }
  visit(join(consumer,'node_modules'));
  return output;
}

function isBuiltTarball(consumer,spec,packedTarball) {
  return typeof spec==='string' && spec.startsWith('file:') &&
    resolve(consumer,spec.slice(5))===packedTarball;
}

function audit(consumer,prediction,rows,lock,semver,packedTarball='/work/pack/fixture.tgz') {
  const errors=[],observedRegistry=new Set(),roles={},edges=[];
  const byPath=new Map(rows.map(r=>[r.path,r]));
  function demand(ok,text){if(!ok)errors.push(text);}
  for(const row of rows) {
    const m=row.metadata,id=identity(m);
    const bundle=/^node_modules\/@claude-flow\/cli\/node_modules\/@claude-flow\/(codex|mcp|plugin-agent-federation|security)$/.test(row.path);
    const role=row.path==='node_modules/@claude-flow/cli'?'root':bundle?'bundle':'registry';
    roles[row.path]=role;
    const matches=prediction.records.filter(r=>r.role===role&&identity(r.metadata)===id);
    demand(matches.length===1,'UNREVIEWED_IDENTITY:'+role+':'+row.path+':'+id);
    const expected=matches[0];
    const locked=lock.packages?.[row.path];
    demand(!!locked&&locked.version===m.version,'LOCK_MANIFEST_MISMATCH:'+row.path);
    if(!expected)continue;
    for(const field of ['dependencies','optionalDependencies','peerDependencies','peerDependenciesMeta']) {
      demand(sorted(m[field])===sorted(expected.metadata[field]),'METADATA_DRIFT:'+row.path+':'+field);
    }
    demand(sorted(row.scripts)===sorted(scripts(expected.metadata)),'SCRIPT_COMMAND_DRIFT:'+row.path);
    demand(!row.binding_gyp,'UNREVIEWED_NATIVE_INSTALL:'+row.path);
    if(role==='registry') {
      observedRegistry.add(id);
      demand(expected.approved_integrities.includes(locked?.integrity),'LOCK_SRI_DRIFT:'+row.path);
      demand(/^https:\/\/registry\.npmjs\.org\//.test(locked?.resolved||''),'NON_REGISTRY_RESOLUTION:'+row.path);
    } else if(role==='bundle') {
      demand(row.manifest_sha256===expected.manifest_sha256,'BUNDLE_CONTENT_DRIFT:'+row.path);
    } else {
      demand(isBuiltTarball(consumer,locked?.resolved,packedTarball),'ROOT_NOT_BUILT_LOCAL_TARBALL');
    }
    for(const [name,sha] of Object.entries(prediction.required_script_files[id]||{})) {
      const path=join(consumer,row.path,name);
      demand(existsSync(path)&&!lstatSync(path).isSymbolicLink()&&hash(readFileSync(path))===sha,'SCRIPT_CONTENT_DRIFT:'+row.path+':'+name);
    }
    function resolveDependency(name) {
      let parent=join(consumer,row.path);
      while(parent.startsWith(consumer)) {
        const candidate=relative(consumer,join(parent,'node_modules',name));
        if(byPath.has(candidate))return byPath.get(candidate);
        if(parent===consumer)break;
        parent=dirname(parent);
      }
    }
    for(const [name,range] of Object.entries(m.dependencies||{})) {
      if(Object.hasOwn(m.optionalDependencies||{},name))continue;
      const found=resolveDependency(name);
      demand(!!found,'MISSING_REQUIRED_DEPENDENCY:'+row.path+':'+name);
      if(found) {
        const alias=/^npm:(@[^/]+\/[^@]+|[^@]+)@(.+)$/.exec(range);
        demand(found.metadata.name===(alias?.[1]||name)&&semver.satisfies(found.metadata.version,alias?.[2]||range),'DEPENDENCY_RANGE_MISMATCH:'+row.path+':'+name);
        edges.push({parent:row.path,dependency:name,range,resolved:found.path,version:found.metadata.version});
      }
    }
    for(const [name,range] of Object.entries(m.peerDependencies||{})) {
      const found=resolveDependency(name);
      if(!found&&m.peerDependenciesMeta?.[name]?.optional)continue;
      demand(!!found&&semver.satisfies(found.metadata.version,range),'PEER_RANGE_MISMATCH:'+row.path+':'+name);
    }
  }
  const expectedRegistry=prediction.records.filter(r=>r.role==='registry').map(r=>identity(r.metadata)).sort();
  const unusedReviewed=expectedRegistry.filter(id=>!observedRegistry.has(id));
  // Every observed row is still admitted individually; prediction counts are evidence only.
  for(const [path,item] of Object.entries(lock.packages||{})) {
    if(!path||byPath.has(path))continue;
    demand(item.optional===true,'ABSENT_NONOPTIONAL_LOCK_NODE:'+path);
    // Omitted optional lock records remain evidence, not execution authority.
  }
  return {errors,edges,roles,registry_unique_versions:observedRegistry.size,
          installed_occurrences_observed:rows.length,unused_reviewed_registry_versions:unusedReviewed};
}

if(process.argv[2]==='--fixture') {
  // Tiny fail-closed controller fixture before any source/dependency install.
  assert(isBuiltTarball('/work/shipping-warm','file:../pack/fixture.tgz','/work/pack/fixture.tgz'));
  assert(isBuiltTarball('/work/shipping-warm','file:/work/pack/fixture.tgz','/work/pack/fixture.tgz'));
  assert(!isBuiltTarball('/work/shipping-warm','file:../pack/other.tgz','/work/pack/fixture.tgz'));
  assert(!isBuiltTarball('/work/shipping-warm','https://example.invalid/fixture.tgz','/work/pack/fixture.tgz'));
  const semver={satisfies:(v,r)=>v===r};
  const m={name:'fixture',version:'1.0.0',dependencies:{missing:'1.0.0'}};
  const rows=[{path:'node_modules/fixture',metadata:m,scripts:{},binding_gyp:false}];
  const p={records:[{role:'registry',metadata:m,approved_integrities:['sha512-fixture']}],required_script_files:{},expected_installed_occurrences:1};
  const lock={packages:{'node_modules/fixture':{version:'1.0.0',resolved:'https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz',integrity:'sha512-fixture'}}};
  assert(audit('/work/fixture',p,rows,lock,semver).errors.some(x=>x.startsWith('MISSING_REQUIRED_DEPENDENCY')));
  delete m.dependencies;
  assert.deepEqual(audit('/work/fixture',p,rows,lock,semver).errors,[]);
  rows[0].scripts={postinstall:'node unexpected.cjs'};
  assert(audit('/work/fixture',p,rows,lock,semver).errors.some(x=>x.startsWith('SCRIPT_COMMAND_DRIFT')));
  const fixture='/work/fixtures/consumer-guard';
  mkdirSync(join(fixture,'node_modules/fixture'),{recursive:true});
  writeFileSync(join(fixture,'node_modules/fixture/package.json'),JSON.stringify(m),{flag:'wx'});
  const collected=inventory(fixture);
  assert.equal(collected.length,1);assert.equal(collected[0].metadata.name,'fixture');
  assert.deepEqual(audit(fixture,p,collected,lock,semver).errors,[]);
  const badLock=structuredClone(lock);badLock.packages['node_modules/fixture'].integrity='sha512-unreviewed';
  assert(audit(fixture,p,collected,badLock,semver).errors.some(x=>x.startsWith('LOCK_SRI_DRIFT')));
  console.log('PASS: real fixture inventory, positive and missing-edge/unreviewed-script/SRI negative controls');
} else {
  const [consumer,predictionFile,output,packedTarball]=process.argv.slice(2);
  assert(typeof packedTarball==='string'&&packedTarball.startsWith('/work/pack/')&&resolve(packedTarball)===packedTarball);
  assert(existsSync(packedTarball)&&!lstatSync(packedTarball).isSymbolicLink());
  assert(consumer==='/work/shipping-warm'&&output==='/work/evidence/consumer-admission.json');
  const prediction=read(predictionFile),lock=read(join(consumer,'package-lock.json'));
  const result={state:'INVENTORY_IN_PROGRESS',lock,rows:[],errors:[]};
  try {
    result.rows=inventory(consumer);
    writeFileSync(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
    const semverPath=join(consumer,'node_modules/semver/package.json');
    assert.equal(read(semverPath).version,'7.7.3');
    const semver=createRequire(semverPath)(join(consumer,'node_modules/semver'));
    Object.assign(result,audit(consumer,prediction,result.rows,lock,semver,packedTarball));
    assert.equal(result.errors.length,0,JSON.stringify(result.errors));
    result.state='COMPLETE_REVIEWED_CONSUMER_GRAPH';
  } catch(error) {
    result.state='TERMINAL_ADMISSION_FAILURE';result.error=String(error);throw error;
  } finally {
    writeFileSync(output,JSON.stringify(result,null,2)+'\n');
  }
}
