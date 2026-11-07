// Real installed CLI bin/flag startup + HTTP MCP initialization and memory call.
import {randomBytes} from 'node:crypto';import assert from 'node:assert/strict';import {spawn} from 'node:child_process';import {createRequire} from 'node:module';
import {createServer} from 'node:net';import {mkdirSync,writeFileSync,openSync,closeSync,readFileSync,existsSync} from 'node:fs';import {join,resolve} from 'node:path';import {pathToFileURL} from 'node:url';
const[kind,arg]=process.argv.slice(2),root=resolve(arg),req=createRequire(join(root,'package.json')),project=resolve('cli-child-'+kind);mkdirSync(project);process.chdir(project);
const token=randomBytes(32).toString('hex');
const env={...process.env,RUFLO_MCP_HTTP_TOKEN:token,CLAUDE_FLOW_DISABLE_BRIDGE:'1',CLAUDE_FLOW_MEMORY_PATH:project,RUFLO_MCP_ALLOW_UNAUTHENTICATED_HTTP:'1'};Object.assign(process.env,env);
const init=await import(pathToFileURL(join(root,'dist/src/memory/memory-initializer.js')).href);const hash=await init.generateLocalEmbedding('cli child query');assert.equal(hash.backend,'mock');
const sql=await import(pathToFileURL(req.resolve('sql.js')).href),SQL=await(sql.default.default||sql.default)(),db=new SQL.Database();db.run(init.MEMORY_SCHEMA_V3);const now=Date.now();db.run("INSERT INTO memory_entries(id,key,namespace,content,embedding,created_at,updated_at,status) VALUES(?,?,?,?,?,?,?,'active')",['cli-child-id','cli-child-key','cli-child','cli child query',JSON.stringify(hash.embedding),now,now]);writeFileSync(join(project,'memory.db'),Buffer.from(db.export()));db.close();
const probe=createServer();await new Promise((ok,no)=>probe.once('error',no).listen(0,'127.0.0.1',ok));const port=probe.address().port;await new Promise(ok=>probe.close(ok));
const argv=[join(root,'bin/cli.js'),'mcp','start','-t','http','--host','127.0.0.1','--port',String(port),'--request-timeout-ms','10000'];
const out=openSync(`/work/evidence/cli-child-${kind}.stdout`,'wx'),err=openSync(`/work/evidence/cli-child-${kind}.stderr`,'wx');const guardPath=process.env.E1_NETWORK_LOG||'/work/evidence/blocked-network.jsonl',guardBefore=existsSync(guardPath)?readFileSync(guardPath).length:0;
let child,spawnError,failure,lastHttpStatus=null,httpStatuses=[],report={kind,argv,authorization:'synthetic64hexBearerOnEveryRequest-valueNotRecorded',backend:'real deterministic hash/sql.js',onnx:false,timeoutClaim:'real CLI flag startup only; deterministic408 belongs to MCPServerManager requestTimeoutMs in packed-smoke'};
try{
 child=spawn(process.execPath,argv,{env,cwd:project,stdio:['ignore',out,err]});child.on('error',e=>{spawnError=e;});
 const end=Date.now()+20000;let initialized,headers={'content-type':'application/json',authorization:'Bearer '+token};
 while(Date.now()<end){
  assert(!spawnError,`real CLI spawn failed:${spawnError}`);assert(child.exitCode===null&&child.signalCode===null,'real CLI exited before MCP initialize');
  try{const r=await fetch(`http://127.0.0.1:${port}/mcp`,{method:'POST',headers,body:JSON.stringify({jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'E2-real-cli',version:'1'}}}),signal:AbortSignal.timeout(500)});lastHttpStatus=r.status;httpStatuses.push(r.status);if(r.status===200){initialized=await r.json();const session=r.headers.get('mcp-session-id');if(session)headers['mcp-session-id']=session;break;}}catch{}
  await new Promise(r=>setTimeout(r,100));
 }
 report.initialize=initialized;assert(initialized&&!initialized.error,'real CLI MCP initialization blocked');
 const response=await fetch(`http://127.0.0.1:${port}/mcp`,{method:'POST',headers,body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'memory_search_unified',arguments:{query:'cli child query',namespaces:['cli-child'],limit:1,threshold:0}}}),signal:AbortSignal.timeout(12000)});
 lastHttpStatus=response.status;httpStatuses.push(response.status);assert.equal(response.status,200,'real CLI memory transport failed');const envelope=await response.json();report.memoryEnvelope=envelope;assert(!envelope.error&&!envelope.result.isError,'real CLI memory tool failed');const payload=JSON.parse(envelope.result.content[0].text);report.memoryPayload=payload;assert.equal(payload.success,true);assert.equal(payload.results.length,1);assert.equal(payload.results[0].key,'cli-child-key');report={...report,state:'REAL_CLI_MCP_START_AND_MEMORY_OBSERVED',initialize:initialized,result:payload};
}catch(error){failure=error;report={...report,failure:String(error)};throw error;}
finally{
 const childExitedBeforeCleanup=!!child&&(child.exitCode!==null||child.signalCode!==null);
 if(child&&child.exitCode===null&&child.signalCode===null){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,1500))]);if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await new Promise(r=>child.once('exit',r));}}
 closeSync(out);closeSync(err);
 const scrub=text=>text.replaceAll(token,'[synthetic-token-redacted]');
 report.lastHttpStatus=lastHttpStatus;report.httpStatuses=httpStatuses;report.spawnError=spawnError?String(spawnError):null;
 report.stderrTail=scrub(readFileSync(`/work/evidence/cli-child-${kind}.stderr`,'utf8').slice(-8192));report.stdoutTail=scrub(readFileSync(`/work/evidence/cli-child-${kind}.stdout`,'utf8').slice(-8192));
 report.blockedNetworkSinceChild=existsSync(guardPath)?scrub(readFileSync(guardPath).subarray(guardBefore).toString()):'';
 const responseDiagnostic=JSON.stringify({initialize:report.initialize?.error,memoryEnvelope:report.memoryEnvelope?.error,memoryToolError:report.memoryEnvelope?.result?.isError?report.memoryEnvelope.result:undefined,memoryPayload:report.memoryPayload?.success===false?report.memoryPayload:undefined});
 const diagnostic=report.stderrTail+'\n'+report.stdoutTail+'\n'+(report.failure||'')+'\n'+responseDiagnostic;
 report.helperMismatchEvidence=diagnostic.split('\n').filter(line=>/manifest version 3\.71\.5 != package\.json 3\.72\.2|helper[^\n]*manifest[^\n]*(?:mismatch|invalid|reject|fail)|manifest[^\n]*3\.71\.5[^\n]*3\.72\.2/i.test(line));
 if(failure){
  if(spawnError||httpStatuses.includes(401)||report.blockedNetworkSinceChild.includes('BLOCKED_NON_LOOPBACK')||/E1_NETWORK_BLOCKED|offline network guard/.test(diagnostic))report.state='REAL_CLI_BLOCKED_HARNESS_OR_ENV';
  else if(report.helperMismatchEvidence.length&&(/manifest version 3\.71\.5 != package\.json 3\.72\.2|helper[^\n]*manifest[^\n]*(?:mismatch|invalid|reject|fail)/i.test(responseDiagnostic)||(!report.initialize&&childExitedBeforeCleanup)))report.state='PARK_HELPER_MANIFEST_MISMATCH';
  else report.state='REAL_CLI_STARTUP_OR_MEMORY_FAILED_UNATTRIBUTED';
 }
 report.childExit=child?.exitCode;report.childSignal=child?.signalCode;writeFileSync(`/work/evidence/cli-child-${kind}.json`,JSON.stringify(report,null,2));
}
