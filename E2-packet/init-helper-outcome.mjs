import {readFileSync,existsSync} from 'node:fs';import {createHash} from 'node:crypto';import {join} from 'node:path';
const project=process.argv[2],root=join(project,'.claude/helpers');const files={};
for(const n of ['.helpers-version','helpers.manifest.json','auto-memory-hook.mjs','hook-handler.cjs','intelligence.cjs','statusline.cjs','router.cjs','learning-loop.cjs']){
 const p=join(root,n);files[n]=existsSync(p)?createHash('sha256').update(readFileSync(p)).digest('hex'):null;
}
console.log(JSON.stringify({files}));
