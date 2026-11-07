// Packet-only verifier. No key acquisition/signing; execution belongs to G0/build lanes.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,verify,generateKeyPairSync} from 'node:crypto';
const critical=['auto-memory-hook.mjs','hook-handler.cjs','intelligence.cjs','statusline.cjs','router.cjs','learning-loop.cjs'];
const hash=b=>createHash('sha256').update(b).digest('hex');
function check(signed,key,bytes){
  assert.equal(signed.algorithm,'ed25519');
  const files=Object.fromEntries(Object.keys(signed.manifest.files).sort().map(k=>[k,signed.manifest.files[k]]));
  const canonical=Buffer.from(JSON.stringify({version:signed.manifest.version,files}));
  assert(verify(null,canonical,key,Buffer.from(signed.signature,'base64')),'signature');
  for(const name of critical)assert.equal(hash(bytes[name]),files[name],`hash:${name}`);
}
const [mode,packageRootArg,baseHelpersArg,helpersArg]=process.argv.slice(2);
const root=resolve(packageRootArg), base=resolve(baseHelpersArg), dir=resolve(helpersArg||join(root,'.claude/helpers'));
const raw=readFileSync(join(dir,'helpers.manifest.json'));
const signed=JSON.parse(raw), bytes=Object.fromEntries(critical.map(n=>[n,readFileSync(join(dir,n))]));
let key;
if(mode==='g0'){
  const source=readFileSync(join(root,'src/init/helper-signing.ts'),'utf8');
  key=/export const RUFLO_HELPERS_PUBKEY = `([^`]+)`/.exec(source)?.[1];
  assert(key,'literal public key missing; stop rather than invent extraction');
}else{
  ({RUFLO_HELPERS_PUBKEY:key}=await import(pathToFileURL(join(root,'dist/src/init/helper-signing.js')).href));
}
check(signed,key,bytes);
assert.deepEqual(raw,readFileSync(join(base,'helpers.manifest.json')),'base manifest bytes');
for(const n of critical)assert.deepEqual(bytes[n],readFileSync(join(base,n)),`base helper:${n}`);
if(mode==='g0'){
  const bad=structuredClone(signed);const signature=Buffer.from(bad.signature,'base64');signature[0]^=1;bad.signature=signature.toString('base64');
  assert.throws(()=>check(bad,key,bytes));
  assert.throws(()=>check(signed,key,{...bytes,[critical[0]]:Buffer.from('tamper')}));
  const wrong=generateKeyPairSync('ed25519').publicKey;
  assert.throws(()=>check(signed,wrong,bytes));
}
console.log(JSON.stringify({state:'SIGNED_BYTES_AND_BASE_EQUAL',mode,manifestVersion:signed.manifest.version,helpers:critical.map(n=>({name:n,sha256:hash(bytes[n])})),officialVersionCheck:'separate differential; never asserted here'}));
