import assert from 'node:assert/strict';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
const plan = JSON.parse(readFileSync('/packet/execution-plan.json'));
const profile = process.argv[2]; const row = plan.focused_runners[profile]; assert(row);
const binary = '/work/source/'+row.binary; assert(existsSync(binary));
let config = null;
if (profile === 'root20') {
  for (const prefix of ['vite.config.', 'vitest.config.']) for (const ext of ['ts', 'mts', 'cts', 'js', 'mjs', 'cjs'])
    assert(!existsSync('/work/source/'+prefix+ext), 'UNPRICED_ROOT_TEST_CONFIG');
} else {
  const file = '/work/source/'+row.config; const data = readFileSync(file);
  config = { file, sha256: createHash('sha256').update(data).digest('hex') };
}
console.log(JSON.stringify({ state: 'PASS', profile, binary: realpathSync(binary),
  config: config ?? 'actual root implicit Vitest defaults', cwd: row.cwd, command: row.command }));
