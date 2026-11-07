// Real installed package HTTP/CLI/MCP/store probe; no source-tree runtime import.
import assert from 'node:assert/strict';import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';import {join,resolve} from 'node:path';import {pathToFileURL} from 'node:url';
const [kind,packageArg]=process.argv.slice(2),pkg=resolve(packageArg),req=createRequire(join(pkg,'package.json'));
assert(['native','omit'].includes(kind));const candidate=process.env.VARIANT==='candidate';
const project=resolve('memory-project');mkdirSync(project);process.chdir(project);
process.env.CLAUDE_FLOW_MEMORY_PATH=project;process.env.CLAUDE_FLOW_DISABLE_BRIDGE=kind==='omit'?'1':'0';
process.env.RUFLO_MCP_ALLOW_UNAUTHENTICATED_HTTP='1';
const load=f=>import(pathToFileURL(join(pkg,'dist/src',f)).href);
const init=await load('memory/memory-initializer.js');
let agentdbResolution,sqliteResolution;try{agentdbResolution=req.resolve('agentdb');}catch(e){agentdbResolution=e.code;}try{sqliteResolution=req.resolve('better-sqlite3');}catch(e){sqliteResolution=e.code;}
let nativeDb,embedCalls=0,held=false;
const vector=kind==='native'?[1,...Array(383).fill(0)]:(await init.generateLocalEmbedding('retrieval query')).embedding;
if(kind==='omit'){const result=await init.generateLocalEmbedding('retrieval query');assert.equal(result.backend,'mock','hash profile accidentally loaded model');}
const namespaces=Array.from({length:74},(_,i)=>'ns'+i);
if(kind==='native'){
 assert(sqliteResolution&&!sqliteResolution.startsWith('MODULE_'),'NATIVE_BINDING_ABSENT');
 const Database=req('better-sqlite3');nativeDb=new Database(join(project,'memory.db'));
 const bridge=await load('memory/memory-bridge.js');assert(bridge.ensureBridgeSchema(nativeDb));
 const row=nativeDb.prepare('INSERT INTO memory_entries (id,key,namespace,content,embedding) VALUES (?,?,?,?,?)');
 namespaces.forEach((n,i)=>row.run('id'+i,'same-key',n,'retrieval query',JSON.stringify(vector)));
 const embed=async()=>{embedCalls++;if(held&&embedCalls===1)await new Promise(r=>setTimeout(r,150));return vector;};
 const agentdb={database:nativeDb,embedder:{pipeline:{},embed}};
 bridge.__setMemoryBridgeRegistryForTests({getAgentDB:()=>agentdb});
}else{
 const sql=await import(pathToFileURL(req.resolve('sql.js')).href);const SQL=await (sql.default.default||sql.default)();const db=new SQL.Database();
 db.run(init.MEMORY_SCHEMA_V3);const now=Date.now();
 for(const [i,n]of namespaces.entries())db.run("INSERT INTO memory_entries (id,key,namespace,content,embedding,created_at,updated_at,status) VALUES (?,?,?,?,?,?,?,'active')",['id'+i,'same-key',n,'retrieval query',JSON.stringify(vector),now,now]);
 writeFileSync(join(project,'memory.db'),Buffer.from(db.export()));db.close();
}
const {MCPServerManager}=await load('mcp-server.js');let manager;
async function start(timeout){
 manager=new MCPServerManager({transport:'http',host:'127.0.0.1',port:0,daemonize:false,tools:['memory_search_unified'],requestTimeoutMs:timeout,pidFile:join(project,'pid'),logFile:join(project,'log')});manager.on('error',()=>{});await manager.start();
 const port=manager.mcpServers[0].transports[0].server.address().port;
 const post=body=>fetch(`http://127.0.0.1:${port}/mcp`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
 const initialized=await post({jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'E2-packed',version:'1'}}});assert.equal(initialized.status,200);return post;
}
const request=id=>({jsonrpc:'2.0',id,method:'tools/call',params:{name:'memory_search_unified',arguments:{query:'retrieval query',namespaces,limit:100}}});
const results={kind,candidate,package:pkg,agentdbResolution,sqliteResolution,backend:kind==='native'?'native SQLite/synthetic384vector':'sql.js/real deterministic hash',onnx:false};
try{
 const post=await start(10000);const response=await post(request(1));assert.equal(response.status,200);const envelope=await response.json();assert(!envelope.error);const payload=JSON.parse(envelope.result.content[0].text);assert.equal(payload.success,true);assert.equal(payload.results.length,74);
 if(candidate){assert.equal(payload.complete,true);assert.equal(payload.searchedNamespaces.length,74);assert.equal(typeof payload.elapsedMs,'number');assert(payload.elapsedMs>=0);}
 if(candidate&&kind==='native')assert.equal(embedCalls,1);
 results.normal=payload;await manager.stop();manager=undefined;
 if(kind==='native'){
  held=true;embedCalls=0;const short=await start(25);const timeout=await short(request(2));assert.equal(timeout.status,408,'must observe real server408');results.timeoutMechanism='MCPServerManager requestTimeoutMs=25 (in-process), not CLIflag latency proof';results.timeoutStatus=timeout.status;await new Promise(r=>setTimeout(r,350));
  if(candidate)assert.equal(embedCalls,1,'no later namespace embedding after cancellation');results.afterTimeoutEmbedCalls=embedCalls;
 }else results.timeout='Native packed lane proves deterministic held-await408; fallback source/full-chain cancellation profile separately required';
 results.state='PACKED_PATH_OBSERVED';writeFileSync(`/work/evidence/packed-${kind}.json`,JSON.stringify(results)+'\n');
}finally{await manager?.stop();if(kind==='native'){(await load('memory/memory-bridge.js')).__setMemoryBridgeRegistryForTests(null);nativeDb.close();}}
