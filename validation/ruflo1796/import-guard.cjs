'use strict';
// Offline import-only observer. Docker independently enforces read-only mounts
// and no network. Native-addon syscalls are constrained by Docker; this shim
// does not claim to observe every native syscall.
const fs = require('node:fs');
const cp = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { syncBuiltinESMExports } = require('node:module');
const log = process.env.IMPORT_DENIAL_LOG;
const expectedCaller = process.env.IMPORT_EXPECTED_CALLER || '';
if (!log || !log.startsWith('/scratch/')) throw Error('Missing scratch denial log');
const expectedURL = expectedCaller ? pathToFileURL(expectedCaller).href : '';
function retained(value, seen = new Set(), depth = 0) {
  if (typeof value === 'function') return '[Function:' + (value.name || 'anonymous') + ']';
  if (value === undefined) return '[undefined]';
  if (typeof value === 'bigint') return String(value) + 'n';
  if (value === null || typeof value !== 'object') {
    return typeof value === 'string' && value.length > 4096 ? value.slice(0, 4096) + '[TRUNCATED]' : value;
  }
  if (seen.has(value)) return '[Circular]';
  if (depth >= 6) return '[Depth bound]';
  seen.add(value);
  if (Array.isArray(value)) return value.slice(0, 64).map(x => retained(x, seen, depth + 1));
  const result = {};
  for (const key of Object.keys(value).slice(0, 64)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    result[key] = descriptor && 'value' in descriptor ? retained(descriptor.value, seen, depth + 1) : '[Accessor not evaluated]';
  }
  return result;
}
function deny(operation) {
  return function blockedCall(...args) {
    const error = new Error('IMPORT_SIDE_EFFECT_DENIED:' + operation);
    const previousLimit = Error.stackTraceLimit;
    Error.stackTraceLimit = 64;
    Error.captureStackTrace(error, blockedCall);
    Error.stackTraceLimit = previousLimit;
    const stack = error.stack;
    const firstFrame = stack.split('\n').slice(1).map(x => x.trim()).find(Boolean) || '';
    const frame = /^at getBrowserTools \((.+):([0-9]+):([0-9]+)\)$/.exec(firstFrame);
    const opts = args[2];
    const exactArgs = args.length === 3 && args[0] === 'agent-browser'
      && Array.isArray(args[1]) && args[1].length === 1 && args[1][0] === '--version'
      && opts && Object.getPrototypeOf(opts) === Object.prototype
      && Object.keys(opts).sort().join(',') === 'stdio,timeout'
      && opts.stdio === 'ignore' && opts.timeout === 3000;
    const expected = operation === 'child_process.execFileSync' && exactArgs && expectedCaller
      && frame && (frame[1] === expectedCaller || frame[1] === expectedURL);
    const record = { operation, classification: expected ? 'EXPECTED_BLOCKED_PROBE' : 'FATAL',
      arguments: retained(args), stack, caller: firstFrame };
    fs.appendFileSync(log, JSON.stringify(record) + '\n');
    error.code = 'IMPORT_SIDE_EFFECT_DENIED';
    // Even an expected version check never executes; upstream catch selects
    // the deterministic browser-tools-absent branch.
    throw error;
  };
}
for (const method of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
  cp[method] = deny('child_process.' + method);
}
for (const [name, methods] of [
  ['node:net', ['connect', 'createConnection']], ['node:tls', ['connect']],
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
