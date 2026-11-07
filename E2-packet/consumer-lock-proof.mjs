import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
const lock=JSON.parse(readFileSync(process.argv[2]));
const rows=Object.entries(lock.packages).filter(([p])=>p.endsWith('/fast-uri'));
assert(rows.length,'fast-uri missing');
for(const [path,p] of rows){assert.match(p.resolved||'',/^https:\/\/registry\.npmjs\.org\//,`fast-uri provenance:${path}`);assert.match(p.integrity||'',/^sha512-/,`fast-uri integrity:${path}`);}
console.log(JSON.stringify({state:'FAST_URI_REGISTRY_PROVENANCE',rows}));
