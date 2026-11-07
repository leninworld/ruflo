import {createRequire} from 'node:module';import {readFileSync,existsSync,realpathSync} from 'node:fs';import {resolve,join} from 'node:path';import os from 'node:os';
const source=process.argv[2],req=createRequire(resolve(source,'v3/@claude-flow/cli/package.json'));
const libraries={};for(const n of ['vitest','better-sqlite3','agentdb','@ruvector/core','@ruvector/rabitq-wasm','@claude-flow/mcp','@claude-flow/cli-core'])try{libraries[n]={path:realpathSync(req.resolve(n))};}catch(e){libraries[n]={error:e.code};}
const lock=JSON.parse(readFileSync(join(source,'package-lock.json')));
const droppedOptionalPackages=Object.entries(lock.packages).filter(([p,v])=>v.optional&&!existsSync(join(source,p,'package.json'))).map(([p])=>p).sort();
console.log(JSON.stringify({node:process.version,arch:process.arch,availableParallelism:os.availableParallelism(),libraries,droppedOptionalPackages}));
