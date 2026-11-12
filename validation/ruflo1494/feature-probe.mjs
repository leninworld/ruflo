// Candidate-only, actual compiled handlers, genuine embeddings, no replacements.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
import {spawnSync} from 'node:child_process';
const project=process.argv[2],mode=process.argv[3]??'write';
mkdirSync(project,{recursive:true});process.chdir(project);
const base='/work/source/v3/@claude-flow/cli/dist/src';
const memory=await import(pathToFileURL(base+'/memory/memory-initializer.js'));
const bridge=await import(pathToFileURL(base+'/memory/memory-bridge.js'));
const {memoryTools}=await import(pathToFileURL(base+'/mcp-tools/memory-tools.js'));
const report={success:false,mode,strict:process.env.RUFLO_REQUIRE_REAL_EMBEDDINGS==='1',forceSqlJs:process.env.CLAUDE_FLOW_DISABLE_BRIDGE==='1',transport:'compiled real handler; installed stdio is a separate gate'};
const save=()=>writeFileSync(project+'/feature-'+mode+'.json',JSON.stringify(report,null,2));
const call=(name,input)=>memoryTools.find(x=>x.name===name).handler(input,{transport:'stdio'});
const special={0:['Astronomers use a telescope to observe distant galaxies and planets in the night sky.','Studying faraway star systems with optical instruments after dark'],623:['Marine biologists study colorful coral reefs and the tropical fish living in the ocean.','Scientists researching undersea habitats teeming with aquatic creatures'],1246:['A violinist plays a classical concerto with a symphony orchestra in a concert hall.','Musicians performing string solos before audiences at an auditorium']};
const distractors=['A computer network firewall inspects incoming packets and rejects unauthorized remote connections.','Bread dough requires flour and yeast and rises before baking in a hot oven.','The national election chooses parliamentary representatives through secret ballots.','An electric car battery stores energy and recharges at a roadside charging station.','Mountain hikers carry camping equipment and navigate rocky trails with a map.','The hospital surgeon repairs a broken bone during an orthopedic operation.'];
async function rankings(namespace='batch1494'){
 for(const [index,[target,query]] of Object.entries(special)){
  const tokens=s=>new Set(s.toLowerCase().match(/[a-z0-9]+/g)??[]),t=tokens(target);
  assert.equal([...tokens(query)].filter(x=>t.has(x)).length,0,'word-disjoint query');
  const result=await call('memory_search',{query,namespace,limit:3,threshold:0,smart:false});
  (report.searches??=[]).push({index,result});save();
  assert.equal(result.results?.[0]?.key,'bulk-'+index,'late/early/middle semantic ranking');
 }
}
async function main(){
 if(mode==='read'){
  if(report.forceSqlJs){
   const {readFileMaybeEncrypted}=await import(pathToFileURL(base+'/fs-secure.js'));
   const {default:initSqlJs}=await import(pathToFileURL('/work/source/v3/@claude-flow/cli/node_modules/sql.js/dist/sql-wasm.js'));
   const SQL=await initSqlJs(),db=new SQL.Database(readFileMaybeEncrypted(memory.getMemoryRoot()+'/memory.db',null));
   try {
    const rows=db.exec("SELECT id,key,embedding,embedding_dimensions FROM memory_entries WHERE namespace='batch1494'")[0]?.values??[];
    assert.equal(rows.length,1247);assert.equal(new Set(rows.map(row=>row[0])).size,1247);
    for(const row of rows){const vector=JSON.parse(row[2]);assert.equal(row[3],384);assert.equal(vector.length,384);assert.ok(vector.every(Number.isFinite));}
    report.durableRows=rows.length;report.durableRealVectorShapes=rows.length;report.durabilityRoute='raw sql.js image, bridge forced off';
    const options=db.exec("SELECT tags,metadata,provenance_type FROM memory_entries WHERE namespace='options1494' AND key='options'")[0]?.values?.[0];
    assert.deepEqual(JSON.parse(options[0]),['evidence']);assert.deepEqual(JSON.parse(options[1]),{count:2});assert.equal(options[2],'tool_result');report.durableOptions=options;
   } finally {db.close();}
  }else{
   // Reopen through the bridge's canonical path after the writer process
   // closes. Do not assume a filename or a successful native WAL checkpoint.
   const all=await bridge.bridgeGetAllEmbeddings({dimensions:384,limit:10000});assert.ok(Array.isArray(all));
   const rows=all.filter(row=>row.namespace==='batch1494');assert.equal(rows.length,1247);assert.equal(new Set(rows.map(row=>row.id)).size,1247);
   for(const row of rows){assert.equal(row.embedding.length,384);assert.ok(row.embedding.every(Number.isFinite));}
   report.durableRows=rows.length;report.durableRealVectorShapes=rows.length;report.durabilityRoute='canonical bridge export in a fresh process';
  }
  for(const i of [0,623,1246])assert.equal((await call('memory_retrieve',{namespace:'batch1494',key:'bulk-'+i})).found,true);
  await rankings();return;
 }
 const a=await memory.generateEmbedding('A dog is chasing a ball across the grass.'),b=await memory.generateEmbedding('A puppy runs through a field to catch a toy.'),c=await memory.generateEmbedding('The central bank increased interest rates to reduce inflation.');
 const cosine=(x,y)=>x.reduce((n,v,i)=>n+v*y[i],0)/Math.sqrt(x.reduce((n,v)=>n+v*v,0)*y.reduce((n,v)=>n+v*v,0));
 report.semantic={paraphrase:cosine(a.embedding,b.embedding),unrelated:cosine(a.embedding,c.embedding),backend:a.backend,dimensions:a.dimensions};save();
 for(const row of [a,b,c])assert.ok(row.backend==='onnx'&&row.dimensions===384&&row.embedding.every(Number.isFinite));
 assert.ok(report.semantic.paraphrase>report.semantic.unrelated+0.15);
 report.bridgeEnabled=await bridge.isBridgeAvailable();assert.equal(report.bridgeEnabled,!report.forceSqlJs);
 const entries=Array.from({length:1247},(_,i)=>({key:'bulk-'+i,namespace:'batch1494',value:special[i]?.[0]??distractors[i%distractors.length]+' Section '+i}));
 assert.ok(Buffer.byteLength(JSON.stringify({entries}))<4*1024*1024);
 const start=performance.now();report.batch=await call('memory_batch',{entries});report.seconds=(performance.now()-start)/1000;save();
 assert.equal(report.batch.success,true);assert.equal(report.batch.stored,1247);assert.equal(report.batch.embedded,1247);assert.equal(new Set(report.batch.results.map(x=>x.id)).size,1247);
 assert.ok(report.seconds<=25,'full1247<=25s');await rankings();
 // Whole-batch validation must reject before writing any valid prefix.
 const invalid=await call('memory_batch',{entries:[{key:'must-not-exist',value:'valid prefix',namespace:'invalid1494'},{key:'bad',value:'bad',namespace:'bad;namespace'}]});
 assert.equal(invalid.success,false);assert.equal((await call('memory_retrieve',{key:'must-not-exist',namespace:'invalid1494'})).found,false);
 const duplicate=await call('memory_batch',{entries:[{key:'dup',value:'one'},{key:'dup',value:'two'}]});assert.equal(duplicate.success,false);
 // Canonical option storage, strict conflicts, omitted-provenance upsert and TTL.
 const optionEntries=[{key:'options',value:'original',namespace:'options1494',tags:['evidence'],metadata:{count:2},provenance_type:'tool_result'},{key:'short',value:'expired record',namespace:'options1494',ttl:0.01},{key:'scoped',value:'agent-scoped record',scope:'project',agentId:'batch-agent'}];report.options=await call('memory_batch',{entries:optionEntries});assert.equal(report.options.success,true);
 report.singleOptions=[];for(const row of optionEntries){const single=await call('memory_store',{...row,key:'single-'+row.key});assert.equal(single.success,true);report.singleOptions.push(single);}
 for(const row of optionEntries){const args={namespace:row.namespace,scope:row.scope,agentId:row.agentId};const batched=await call('memory_retrieve',{...args,key:row.key}),single=await call('memory_retrieve',{...args,key:'single-'+row.key});assert.equal(batched.found,single.found);if(batched.found){assert.deepEqual(batched.value,single.value);assert.deepEqual(batched.tags,single.tags);}}
 report.conflict=await call('memory_batch',{entries:[{key:'options',value:'replacement',namespace:'options1494',upsert:false},{key:'other',value:'partial success',namespace:'options1494'}]});assert.equal(report.conflict.success,false);assert.equal(report.conflict.results[0].stored,false);assert.equal(report.conflict.results[1].stored,true);
 report.readOptions=await call('memory_retrieve',{key:'options',namespace:'options1494'});assert.equal(report.readOptions.found,true);assert.ok(JSON.stringify(report.readOptions).includes('original'));assert.ok(JSON.stringify(report.readOptions).includes('evidence'));
 report.upsert=await call('memory_batch',{entries:[{key:'options',value:'updated',namespace:'options1494',tags:['evidence'],metadata:{count:2}}]});assert.equal(report.upsert.success,true);
 await call('memory_store',{key:'single-options',value:'updated',namespace:'options1494',tags:['evidence'],metadata:{count:2}});
 report.preservedProvenance=await memory.listEntries({namespace:'options1494',provenanceFilter:['tool_result']});assert.ok(report.preservedProvenance.entries.some(row=>row.key==='options'&&row.provenanceType==='tool_result'));assert.ok(report.preservedProvenance.entries.some(row=>row.key==='single-options'&&row.provenanceType==='tool_result'));
 assert.equal((await call('memory_retrieve',{key:'scoped',scope:'project',agentId:'batch-agent'})).found,true);
 await new Promise(r=>setTimeout(r,30));report.expired=await call('memory_retrieve',{key:'short',namespace:'options1494'});assert.equal(report.expired.found,false);assert.equal((await call('memory_retrieve',{key:'single-short',namespace:'options1494'})).found,report.expired.found);
 // Honest populated-store measurement, supplemental and never a replacement gate.
 const populated=performance.now();report.populated=await call('memory_batch',{entries:entries.map(e=>({...e,key:'second-'+e.key,namespace:'populated1494'}))});report.populatedSeconds=(performance.now()-populated)/1000;assert.equal(report.populated.success,true);
 // An explicit alternate store with the same logical key must not answer from
 // the project-wide singleton. Its vectors remain authoritative in SQLite.
 const alternate=project+'/alternate.db';assert.equal((await memory.initializeMemoryDatabase({dbPath:alternate})).success,true);
 report.alternate=await call('memory_batch',{dbPath:alternate,entries:[{key:'bulk-0',namespace:'batch1494',value:'A pastry chef kneads sourdough bread for the village bakery.'}]});assert.equal(report.alternate.success,true);
 report.alternateSearch=await call('memory_search',{dbPath:alternate,namespace:'batch1494',query:'Freshly baked loaves prepared by a cook',threshold:0,limit:1,smart:false});assert.ok(JSON.stringify(report.alternateSearch).includes('pastry chef'));assert.ok(!JSON.stringify(report.alternateSearch).includes('Astronomers'));
 // Shutdown before a genuinely fresh read process opens the same durable store.
 await bridge.shutdownBridge();
 const child=spawnSync(process.execPath,[new URL(import.meta.url).pathname,project,'read'],{env:process.env,encoding:'utf8',timeout:90000,maxBuffer:4*1024*1024});report.freshProcess={exit:child.status,signal:child.signal,stderr:child.stderr,stdout:child.stdout,error:child.error?.message};assert.equal(child.status,0);report.freshRead=JSON.parse(readFileSync(project+'/feature-read.json','utf8'));assert.equal(report.freshRead.success,true);
}
try{await main();report.success=true;}catch(error){report.error=String(error);report.stack=error.stack;}
try{await bridge.shutdownBridge();report.shutdown='completed';}catch(error){report.success=false;report.shutdownError=String(error);}
save();process.exit(report.success?0:1);
