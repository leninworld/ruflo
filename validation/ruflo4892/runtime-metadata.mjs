import {createRequire} from 'node:module';
import {readFileSync,realpathSync,existsSync} from 'node:fs';
import {join,dirname} from 'node:path';
import os from 'node:os';
const source='/work/source';const cli=join(source,'v3/@claude-flow/cli');
const require=createRequire(join(cli,'package.json'));
const libraries={};
for(const name of ['vitest','@ruvector/attention','@ruvector/sona','ruvector','@claude-flow/cli-core/mcp-tools/types']) {
 try {libraries[name]={realpath:realpathSync(require.resolve(name))};}
 catch(error){libraries[name]={error:error.code};}
}
const lock=JSON.parse(readFileSync(join(source,'package-lock.json'),'utf8'));
const droppedOptionalPackages=Object.entries(lock.packages??{}).filter(([path,value])=>value.optional&&!existsSync(join(source,path,'package.json'))).map(([path])=>path).sort();
console.log(JSON.stringify({node:process.version,arch:process.arch,availableParallelism:os.availableParallelism(),libraries,droppedOptionalPackages},null,2));
