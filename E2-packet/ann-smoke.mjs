// Actual optional ANN bindings + real SQL store + HTTP/CLI; no ONNX/model load.
import assert from 'node:assert/strict';import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';
const [mode,rootArg]=process.argv.slice(2);assert(['hnsw','rabitq'].includes(mode));const root=resolve(rootArg),req=createRequire(join(root,'package.json'));
const project=resolve('ann-'+mode);mkdirSync(project);process.chdir(project);process.env.CLAUDE_FLOW_MEMORY_PATH=project;process.env.CLAUDE_FLOW_DISABLE_BRIDGE='1';process.env.RUFLO_MCP_ALLOW_UNAUTHENTICATED_HTTP='1';
const load=f=>import(pathToFileURL(join(root,'dist/src',f)).href),m=await load('memory/memory-initializer.js');
const prepared=await m.generateLocalEmbedding('retrieval query');assert.equal(prepared.backend,'mock');const vector=prepared.embedding;
const sql=await import(pathToFileURL(req.resolve('sql.js')).href);const SQL=await(sql.default.default||sql.default)();const db=new SQL.Database();db.run(m.MEMORY_SCHEMA_V3);
// Under10vectors the actual RaBitQ auto-build policy declines, selecting HNSW.
const count=mode==='hnsw'?4:74,namespaces=Array.from({length:mode==='hnsw'?2:74},(_,i)=>'ann'+i),now=Date.now();
const entries=Array.from({length:count},(_,i)=>({i,n:mode==='hnsw'?namespaces[Math.floor(i/2)]:namespaces[i]}));
for(const {i,n}of entries)db.run("INSERT INTO memory_entries(id,key,namespace,content,embedding,created_at,updated_at,status) VALUES (?,?,?,?,?,?,?,'active')",['id'+i,'key'+i,n,'retrieval query',JSON.stringify(vector),now,now]);
writeFileSync(join(project,'memory.db'),Buffer.from(db.export()));db.close();
let calls=0,restore,returnedWindows=[];const sentinel=Symbol('E2-real-ANN-module');
if(mode==='hnsw'){
 const index=await m.getHNSWIndex({dbPath:join(project,'memory.db'),dimensions:vector.length,forceRebuild:true});assert(index,'actual @ruvector/core native binding required');
 const original=index.db.search;index.db.search=async function(...args){calls++;const result=await original.apply(this,args);returnedWindows.push(Array.isArray(result)?result.length:(result.results?.length??result.length??0));return result;};restore=()=>{index.db.search=original;};
}else{
 // Resolve dynamicimport from exactly the installed rabitq-index parentdirectory.
 const observer=join(root,'dist/src/memory/e2-ann-observer.mjs');writeFileSync(observer,"export const actual=await import('@ruvector/rabitq-wasm');\n");
 const {actual}=await import(pathToFileURL(observer).href);const proto=actual.RabitqIndex.prototype;const original=proto.search;assert.equal(typeof original,'function');proto[sentinel]=true;
 proto.search=function(...args){assert(this[sentinel]===true,'RaBitQmoduleidentity mismatch');calls++;const result=original.apply(this,args);returnedWindows.push(result.length);return result;};restore=()=>{proto.search=original;delete proto[sentinel];};
 const result=await(await load('memory/rabitq-index.js')).buildRabitqIndex({dbPath:join(project,'memory.db'),dimensions:vector.length,force:true});assert.equal(result.success,true,'real WASM RaBitQ must build');assert.equal(result.vectorCount,74);
}
const {MCPServerManager}=await load('mcp-server.js');const manager=new MCPServerManager({transport:'http',host:'127.0.0.1',port:0,daemonize:false,tools:['memory_search_unified'],requestTimeoutMs:10000,pidFile:join(project,'pid'),logFile:join(project,'log')});manager.on('error',()=>{});
try{
 await manager.start();const port=manager.mcpServers[0].transports[0].server.address().port;
 const post=body=>fetch(`http://127.0.0.1:${port}/mcp`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
 const initialized=await post({jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'E2-ANN',version:'1'}}});assert.equal(initialized.status,200);
 const response=await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'memory_search_unified',arguments:{query:'retrieval query',namespaces,limit:mode==='hnsw'?1:100,threshold:0}}});assert.equal(response.status,200);const envelope=await response.json();assert(!envelope.error);const payload=JSON.parse(envelope.result.content[0].text);assert.equal(payload.success,true);assert.equal(payload.results.length,mode==='hnsw'?1:count);if(mode==='hnsw')assert(returnedWindows.length>=2&&returnedWindows.every(n=>n>=2),'HNSWcandidatewindowmustfill2pernamespace');assert(calls>0,'silent SQL fallback is not ANN proof');
 writeFileSync(`/work/evidence/ann-${mode}.json`,JSON.stringify({state:'ACTUAL_ANN_HTTP_CHAIN',mode,observedRealSearchCalls:calls,returnedWindows,moduleIdentity:'same-parent-dynamic-import; sentinelobservedonactualsearch',count,embeddingBackend:prepared.backend,onnx:false,results:payload.results})+'\n');
}finally{await manager.stop();restore?.();}
