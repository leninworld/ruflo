// Baseline-only: actual compiled upstream handlers; no candidate code or stubs.
import {mkdirSync,writeFileSync,readdirSync,statSync,readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
const base='/work/source/v3/@claude-flow/cli/dist/src';
const project=process.argv[2],warmup=process.argv[3]==='warmup';
mkdirSync(project,{recursive:true});process.chdir(project);
const t=performance.now();
const rx=()=>readFileSync('/proc/net/dev','utf8').split('\n').slice(2).filter(x=>x.includes(':')&&x.split(':')[0].trim()!=='lo').reduce((n,x)=>n+Number(x.split(':')[1].trim().split(/\s+/)[0]),0);
const initialRx=rx();
const memory=await import(pathToFileURL(base+'/memory/memory-initializer.js'));
const {memoryTools}=await import(pathToFileURL(base+'/mcp-tools/memory-tools.js'));
const bridge=await import(pathToFileURL(base+'/memory/memory-bridge.js'));
const report={success:false,strict:process.env.RUFLO_REQUIRE_REAL_EMBEDDINGS==='1',forceSqlJs:process.env.CLAUDE_FLOW_DISABLE_BRIDGE==='1',warmup,project,cache:process.env.TRANSFORMERS_CACHE,rows:[],candidate:false,transport:'actual upstream handler; stdio is a later candidate gate'};
const save=()=>writeFileSync(project+'/probe.json',JSON.stringify(report,null,2));save();
const call=(name,input)=>memoryTools.find(x=>x.name===name).handler(input,{transport:'stdio'});
const distractors=[
 'A computer network firewall inspects incoming packets and rejects unauthorized remote connections.',
 'Bread dough requires flour and yeast and rises before baking in a hot oven.',
 'The national election chooses parliamentary representatives through secret ballots.',
 'An electric car battery stores energy and recharges at a roadside charging station.',
 'Mountain hikers carry camping equipment and navigate rocky trails with a map.',
 'The hospital surgeon repairs a broken bone during an orthopedic operation.'
];
async function semanticProof(){
 const a=await memory.generateEmbedding('A dog is chasing a ball across the grass.');
 const b=await memory.generateEmbedding('A puppy runs through a field to catch a toy.');
 const c=await memory.generateEmbedding('The central bank increased interest rates to reduce inflation.');
 const cosine=(x,y)=>x.reduce((n,v,i)=>n+v*y[i],0)/Math.sqrt(x.reduce((n,v)=>n+v*v,0)*y.reduce((n,v)=>n+v*v,0));
 report.semantic={paraphrase:cosine(a.embedding,b.embedding),unrelated:cosine(a.embedding,c.embedding),margin:0.15};
 report.embedding={model:a.model,modelId:a.modelId,backend:a.backend,dimensions:a.dimensions,finite:a.embedding.every(Number.isFinite)};save();
 if([a,b,c].some(v=>v.backend!=='onnx'||v.dimensions!==384||!v.embedding.every(Number.isFinite))||!(report.semantic.paraphrase>report.semantic.unrelated+0.15))throw Error('REAL_SEMANTIC_EMBEDDING_PREREQUISITE_FAILED');
}
async function ranking(query,namespace,key,target){
 const tokens=text=>new Set(text.toLowerCase().match(/[a-z0-9]+/g)??[]);
 const targetTokens=tokens(target),overlap=[...tokens(query)].filter(token=>targetTokens.has(token));
 if(overlap.length)throw Error('OVERLAPPING_QUERY_TARGET_TOKENS:'+key+':'+overlap.join(','));
 const result=await call('memory_search',{query,namespace,limit:3,threshold:0,smart:false});
 (report.searches??=[]).push({query,namespace,expected:key,result});save();
 if(result.error||result.results?.[0]?.key!==key)throw Error('PERSISTED_SEMANTIC_RANKING_FAILED:'+key);
}
async function measure(){
 await semanticProof();
 if(warmup)return;
 const stored=await call('memory_store',{key:'probe-0',value:'Apples grow on trees and fruit is harvested in autumn.',namespace:'baseline1494'});
 report.coldStore=stored;report.firstStoreSeconds=(performance.now()-t)/1000;save();
 report.nativeBridge=await bridge.isBridgeAvailable();report.bridgeFailure=bridge.getBridgeFailureReason();save();
 if(!stored.success||!stored.hasEmbedding||stored.embeddingError)throw Error('STORE_EMBEDDING_PREREQUISITE_FAILED');
 if(report.forceSqlJs?report.nativeBridge:!report.nativeBridge)throw Error('REQUESTED_BACKEND_NOT_ACTIVE');
 const warm=performance.now();
 for(let i=1;i<=64;i++){
  const at=performance.now(),result=await call('memory_store',{key:'probe-'+i,value:distractors[i%distractors.length]+' Section '+i,namespace:'baseline1494'});
  report.rows.push({i,ms:performance.now()-at,result});save();
  if(!result.success||!result.hasEmbedding||result.embeddingError)throw Error('WARM_WRITE_FAILED');
 }
 report.warm64Seconds=(performance.now()-warm)/1000;
 report.readback=await call('memory_retrieve',{key:'probe-64',namespace:'baseline1494'});
 if(!report.readback.found)throw Error('READBACK_FAILED');
 await ranking('Picking ripe orchard produce during the fall season','baseline1494','probe-0','Apples grow on trees and fruit is harvested in autumn.');
 const special={0:['Astronomers use a telescope to observe distant galaxies and planets in the night sky.','Studying faraway star systems with optical instruments after dark'],623:['Marine biologists study colorful coral reefs and the tropical fish living in the ocean.','Scientists researching undersea habitats teeming with aquatic creatures'],1246:['A violinist plays a classical concerto with a symphony orchestra in a concert hall.','Musicians performing string solos before audiences at an auditorium']};
 const entries=Array.from({length:1247},(_,i)=>({key:'bulk-'+i,namespace:'baseline1494bulk',value:special[i]?.[0]??distractors[i%distractors.length]+' Section '+i}));
 writeFileSync(project+'/input.json',JSON.stringify({entries}));
 const bulkStart=performance.now();
 report.bulk1247=await call('memory_import',{inputPath:project+'/input.json'});
 report.bulk1247Seconds=(performance.now()-bulkStart)/1000;save();
 if(!report.bulk1247.success||report.bulk1247.imported?.entries!==1247||report.bulk1247.imported?.vectors!==1247||report.bulk1247Seconds>25)throw Error('FULL_REPRESENTATIVE_BATCH_EXCEEDS_25S_OR_FAILED');
 for(const [i,values] of Object.entries(special))await ranking(values[1],'baseline1494bulk','bulk-'+i,values[0]);
}
try{await measure();report.success=true;}catch(error){report.success=false;report.error=String(error);}
try{await bridge.shutdownBridge();report.shutdown='completed';}catch(error){report.success=false;report.shutdownError=String(error);}
function files(dir){let result=[];try{for(const name of readdirSync(dir)){const p=dir+'/'+name,s=statSync(p);if(s.isDirectory())result.push(...files(p));else result.push({path:p,bytes:s.size});}}catch{}return result;}
report.cacheFiles=files(process.env.TRANSFORMERS_CACHE);report.cacheBytes=report.cacheFiles.reduce((a,b)=>a+b.bytes,0);report.networkRxBytes=rx()-initialRx;report.seconds=(performance.now()-t)/1000;save();
process.exit(report.success?0:1);
