'use strict';
// Actual child-process interception and real allowed command, not fake browser success.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const cp = require('node:child_process');
process.env.INSTALL_GLOBAL_GUARD_LOG = '/work/evidence/fixture-global-denials.jsonl';
require('/packet/install-global-guard.cjs');
const commands = ['npm install -g agent-browser@latest', 'npm install -g agent-browser@^0.41.0'];
for (const command of commands) assert.throws(() => cp.execSync(command), /Frozen validation forbids/);
assert.equal(cp.execSync('node -p "20+22"', { encoding: 'utf8' }).trim(), '42');
const rows = fs.readFileSync(process.env.INSTALL_GLOBAL_GUARD_LOG, 'utf8').trim().split('\n').map(JSON.parse);
assert.deepEqual(rows.map(row => row.command), commands);
assert(rows.every(row => row.state === 'BLOCKED_GLOBAL_INSTALL'));
console.log(JSON.stringify({ state: 'PASS', actual_denials: rows.length, real_allowed_command: 'PASS', network: 'none' }));
