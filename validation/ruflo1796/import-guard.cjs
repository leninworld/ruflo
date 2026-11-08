'use strict';
// Offline import-only observer. Docker independently enforces read-only mounts
// and no network. This records JS network/subprocess attempts; native-addon
// syscalls are constrained by Docker, not claimed observable by this shim.
const fs = require('node:fs');
const cp = require('node:child_process');
const { syncBuiltinESMExports } = require('node:module');
const log = process.env.IMPORT_DENIAL_LOG;
if (!log || !log.startsWith('/scratch/')) throw Error('Missing scratch denial log');
function deny(operation) {
  return function () {
    fs.appendFileSync(log, JSON.stringify({ operation }) + '\n');
    const error = new Error('IMPORT_SIDE_EFFECT_DENIED:' + operation);
    error.code = 'IMPORT_SIDE_EFFECT_DENIED';
    throw error;
  };
}
for (const method of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
  cp[method] = deny('child_process.' + method);
}
for (const [name, methods] of [
  ['node:net', ['connect', 'createConnection']],
  ['node:tls', ['connect']],
  ['node:http', ['request', 'get']], ['node:https', ['request', 'get']],
  ['node:http2', ['connect']], ['node:dgram', ['createSocket']],
  ['node:dns', ['lookup', 'resolve', 'resolve4', 'resolve6']],
]) {
  const api = require(name);
  for (const method of methods) api[method] = deny(name + '.' + method);
}
require('node:net').Socket.prototype.connect = deny('Socket.connect');
require('node:net').Server.prototype.listen = deny('Server.listen');
for (const method of Object.keys(require('node:dns').promises)) {
  if (typeof require('node:dns').promises[method] === 'function') {
    require('node:dns').promises[method] = deny('dns.promises.' + method);
  }
}
globalThis.fetch = deny('fetch');
require('node:worker_threads').Worker = deny('Worker');
syncBuiltinESMExports();
