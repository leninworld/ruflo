// Resolve from the owned subtree under installedCLI: bundled + hoisted ancestry.
import assert from 'node:assert/strict';import {createRequire} from 'node:module';import {readFileSync,writeFileSync} from 'node:fs';import {join,resolve} from 'node:path';
const root=resolve(process.argv[2]),req=createRequire(join(root,'package.json')),inventory=JSON.parse(readFileSync('/inputs/fallback-import-inventory.json'));
const falsePositives=new Set(['no controller registered',',']);
const typeOnly=new Set(['@claude-flow/cli-core/mcp-tools/types']);
const required=new Set(['sql.js','@claude-flow/cli-core/mcp-tools/validate-input']);
const rows=[];
for(const [specifier,uses]of Object.entries(inventory.bare_specifiers)){
 if(falsePositives.has(specifier)){rows.push({specifier,status:'STATIC_REGEX_FALSE_POSITIVE_COMMENT_OR_STRING'});continue;}
 if(typeOnly.has(specifier)){rows.push({specifier,status:'TYPE_ONLY_ERASED'});continue;}
 try{const path=req.resolve(specifier);assert(path.startsWith('/work/consumer-omit/node_modules/'),`sourceworkspace provider leaked:${specifier}:${path}`);rows.push({specifier,path,status:'ACTUAL_OMIT_CONSUMER_ANCESTRY'});}
 catch(error){if(required.has(specifier))throw error;rows.push({specifier,status:'OPTIONAL_CAPABILITY_ABSENT_NOT_REQUIRED_FOR_FALLBACK',error:error.code||String(error)});}
}
writeFileSync('/work/evidence/fallback-resolution.json',JSON.stringify({state:'OWNED_SOURCE_SUBTREE_UNDER_INSTALLED_CLI',rows,modelRequirement:false,limitations:inventory.limitations},null,2));
