// Helper fixtures only, to be run within the exact approved Docker packet.
import { mkdirSync, copyFileSync, writeFileSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';

if (process.argv[2] === '--guard-only') {
  const directory = resolve(process.argv[3]);
  assert(directory.startsWith('/scratch/'));
  mkdirSync(directory, { recursive: false });
  const caller = join(directory, 'mcp-client.mjs');
  const other = join(directory, 'other-client.mjs');
  const here = fileURLToPath(new URL('.', import.meta.url));
  function guardedCase(name, module, args, classification, network = false) {
    const log = join(directory, name + '.jsonl');
    const body = network ? "connect(9, '127.0.0.1')" :
      `execFileSync('agent-browser', ${JSON.stringify(args)}, {stdio:'ignore',timeout:3000})`;
    writeFileSync(module, `import {execFileSync} from 'node:child_process';
import {connect} from 'node:net';
export function getBrowserTools() {
  try { ${body}; throw Error('GUARD_DID_NOT_THROW'); }
  catch(e) { if(e.code !== 'IMPORT_SIDE_EFFECT_DENIED') throw e; return []; }
}
if (getBrowserTools().length !== 0) throw Error('BROWSER_TOOLS_PRESENT');
`);
    const child = spawnSync(process.execPath, ['--require=' + join(here, 'import-guard.cjs'), module],
      { env: {...process.env, IMPORT_DENIAL_LOG:log, IMPORT_EXPECTED_CALLER:caller},
        encoding:'utf8',timeout:5000,maxBuffer:65536 });
    assert(!child.error, String(child.error));
    assert.equal(child.signal,null);
    assert.equal(child.status,0,child.stderr);
    const rows = readFileSync(log,'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows.length,1);
    assert.equal(rows[0].classification,classification);
    assert(rows[0].stack.includes('getBrowserTools'));
    assert(Array.isArray(rows[0].arguments));
    return rows[0];
  }
  const records = [
    guardedCase('exact-caller',caller,['--version'],'EXPECTED_BLOCKED_PROBE'),
    guardedCase('other-module',other,['--version'],'FATAL'),
    guardedCase('other-arguments',caller,['--help'],'FATAL'),
    guardedCase('network',caller,[],'FATAL',true),
  ];
  console.log(JSON.stringify({guardFixtures:'PASS',records}));
  process.exit(0);
}

const [sourceArg, workArg] = process.argv.slice(2);
assert(sourceArg && workArg && process.argv.length === 4);
const root = resolve(workArg);
const here = fileURLToPath(new URL('.', import.meta.url));
mkdirSync(root, { recursive: false });
mkdirSync(join(root, 'scripts'));
const cli = join(root, 'v3/@claude-flow/cli');
for (const folder of ['src/commands', 'dist/src/commands']) mkdirSync(join(cli, folder), { recursive: true });
copyFileSync(join(resolve(sourceArg), 'scripts/audit-skill-cli-commands.mjs'),
  join(root, 'scripts/audit-skill-cli-commands.mjs'));
writeFileSync(join(root, 'package.json'), '{"type":"module"}\n');
writeFileSync(join(cli, 'src/commands/index.ts'),
  "const commandLoaders: Record<string, CommandLoader> = {\n  lazy: () => import('./lazy.js'),\n};\nimport { coreCommand } from './core.js';\n");
writeFileSync(join(cli, 'dist/src/commands/core.js'),
  "export const coreCommand={name:'core',description:'synthetic core',action(){throw Error('ACTION_CALLED')}};\n");
writeFileSync(join(cli, 'dist/src/commands/lazy.js'),
  "export default {name:'lazy',description:'synthetic lazy',aliases:['l'],subcommands:[{name:'child',description:'child'}],action(){throw Error('ACTION_CALLED')}};\n");
writeFileSync(join(cli, 'dist/src/commands/index.js'),
  "import {coreCommand} from './core.js';export const commands=[coreCommand];export const getLazyCommandNames=()=>['lazy'];\n");

function run(name, expected) {
  const report = join(root, name + '.json');
  const child = spawnSync(process.execPath, [join(here, 'probe.mjs'), root, report],
    { encoding: 'utf8', timeout: 20000, maxBuffer: 65536 });
  assert(!child.error, String(child.error));
  assert.equal(child.signal, null);
  assert.equal(child.status, expected, child.stderr);
  const result = JSON.parse(readFileSync(report, 'utf8'));
  assert.equal(result.actionsInvoked, 0);
  assert.equal(result.complete, expected === 0);
  assert.equal(result.harnessError, false);
  assert(Array.isArray(result.activeResources));
  return result;
}
const good = run('success', 0);
assert.equal(good.modules.length, 2);
assert.equal(good.commands.length, 3);
assert.equal(good.maximumDepth, 2);
writeFileSync(join(cli, 'dist/src/commands/lazy.js'), "throw new Error('SYNTHETIC_IMPORT_FAILURE');\n");
const failed = run('import-failure', 2);
assert(failed.errors.some(error => error.scope === 'lazy' && error.message === 'SYNTHETIC_IMPORT_FAILURE'));
assert(failed.modules.some(module => module.key === 'lazy' && module.status === 'import-error'));
writeFileSync(join(cli, 'dist/src/commands/lazy.js'),
  "export const a={name:'lazy',description:'a'};export const b={name:'lazy',description:'b'};\n");
const ambiguous = run('ambiguous-export', 2);
assert(ambiguous.modules.some(module => module.key === 'lazy' && module.status === 'ambiguous'));
writeFileSync(join(cli, 'dist/src/commands/lazy.js'),
  "export default {name:'different',description:'actual default differs from loader key'};\n");
const mismatch = run('default-name-mismatch', 2);
assert(mismatch.modules.some(module => module.key === 'lazy' && module.status === 'name-mismatch'
  && module.registeredName === 'different' && module.resolution === 'default'));
writeFileSync(join(cli, 'dist/src/commands/lazy.js'),
  "export default {name:'lazy',description:'alias collision',aliases:['core']};\n");
const collision = run('alias-collision', 2);
assert.equal(collision.aliasCollisions.length, 1);
console.log(JSON.stringify({ helperFixtures: 'PASS', cases: ['success', 'import failure', 'ambiguous exports', 'different default name', 'alias collision'] }));
