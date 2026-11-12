// Real installed consumer stdio: no registry/transport/model replacements.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,existsSync} from 'node:fs';
import {performance} from 'node:perf_hooks';
import {join} from 'node:path';
const consumer=process.argv[2],route=process.argv[3],nativeAbsent=route.startsWith('absent'),forced=route==='absent-forced';
const packageRoot=join(consumer,'node_modules/@claude-flow/cli');
const require=createRequire(join(packageRoot,'package.json'));
if(nativeAbsent){let found=false;try{require.resolve('better-sqlite3');found=true;}catch{}assert.equal(found,false,'consumer must actually lack better-sqlite3');}
const project=join(consumer,'stdio-project-'+route);mkdirSync(project,{recursive:true});
const report={success:false,nativeAbsent,forced,route,packageRoot,noise:[],responses:[]};
const save=()=>writeFileSync(join(consumer,'stdio-proof-'+route+'.json'),JSON.stringify(report,null,2));
const childEnv={...process.env,RUFLO_REQUIRE_REAL_EMBEDDINGS:'1'};delete childEnv.CLAUDE_FLOW_DISABLE_BRIDGE;if(forced)childEnv.CLAUDE_FLOW_DISABLE_BRIDGE='1';
const child=spawn(process.execPath,[join(packageRoot,'bin/mcp-server.js')],{cwd:project,env:childEnv,stdio:['pipe','pipe','pipe']});
let stderr='',nextId=1;child.stderr.setEncoding('utf8');child.stderr.on('data',s=>{stderr+=s;if(stderr.length>4*1024*1024)child.kill('SIGTERM');});
const lines=createInterface({input:child.stdout})[Symbol.asyncIterator]();
const closed=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',(code,signal)=>resolve({code,signal}));});
async function request(method,params,cap=30000){
 const id=nextId++;child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
 const end=performance.now()+cap;
 while(performance.now()<end){
  let timer;const line=await Promise.race([lines.next(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('stdio response deadline')),Math.max(1,end-performance.now()));})]).finally(()=>clearTimeout(timer));
  assert.equal(line.done,false,'server exited before response');
  let response;try{response=JSON.parse(line.value);}catch{report.noise.push(line.value);save();throw Error('Non-JSON stdout in actual MCP transport');}
  if(response.id!==id)continue;assert.equal(response.error,undefined,JSON.stringify(response));return response.result;
 }
 throw Error('stdio request deadline');
}
async function call(name,args,cap){const result=await request('tools/call',{name,arguments:args},cap);assert.ok(!result.isError,JSON.stringify(result));return JSON.parse(result.content.find(x=>x.type==='text').text);}
try{
 const initialized=await request('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'memory-batch-validation',version:'1.0'}});assert.ok(typeof initialized.protocolVersion==='string');
 child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
 report.tools=await request('tools/list',{});assert.equal(report.tools.tools.filter(x=>x.name==='memory_batch').length,1);
 // Cold model initialization is a separate request, before the representative
 // bulk latency measurement, exactly as the direct real-producer gate.
 report.warmup=await call('memory_store',{key:'warmup',value:'A dog runs across the grass after a ball.',namespace:'stdio1494warm'},180000);assert.equal(report.warmup.success,true);assert.equal(report.warmup.hasEmbedding,true);
 const entries=Array.from({length:1247},(_,i)=>({key:'stdio-'+i,namespace:'stdio1494',value:'Evidence record '+i+' describes a bicycle rider travelling through a city park.'}));
 const requestBytes=Buffer.byteLength(JSON.stringify({jsonrpc:'2.0',id:nextId,method:'tools/call',params:{name:'memory_batch',arguments:{entries}}}));assert.ok(requestBytes<4*1024*1024);report.requestBytes=requestBytes;
 const start=performance.now();report.batch=await call('memory_batch',{entries},30000);report.seconds=(performance.now()-start)/1000;report.selectedBackend=report.batch.backend;assert.equal(report.batch.success,true);assert.equal(report.batch.stored,1247);assert.equal(report.batch.embedded,1247);assert.ok(report.seconds<=25);
 for(const i of [0,623,1246])assert.equal((await call('memory_retrieve',{key:'stdio-'+i,namespace:'stdio1494'})).found,true);
 child.stdin.end();let timer;report.close=await Promise.race([closed,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('actual MCP shutdown deadline')),20000);})]).finally(()=>clearTimeout(timer));assert.equal(report.close.code,0);report.success=true;
}catch(error){report.error=String(error);report.stack=error.stack;}
finally{if(child.exitCode===null)child.kill('SIGTERM');report.stderr=stderr;save();}
process.exit(report.success?0:1);
