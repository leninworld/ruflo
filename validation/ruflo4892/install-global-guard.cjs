'use strict';
// Install-only guard for the upstream browser workspace's conditional global
// fallback. No simulated browser binary, no overridden successful --version.
const cp = require('node:child_process');
const fs = require('node:fs');
const real = cp.execSync;
cp.execSync = function guardedExec(command, options) {
  if (typeof command === 'string' && /^\s*(?:\S*\/)?npm(?:\.cmd)?\s+(?:install|i)\b[\s\S]*(?:\s-g\b|\s--global\b)/.test(command)) {
    fs.appendFileSync(process.env.INSTALL_GLOBAL_GUARD_LOG,
      JSON.stringify({ state: 'BLOCKED_GLOBAL_INSTALL', command }) + '\n');
    throw new Error('Frozen validation forbids a global latest install.');
  }
  return real.call(this, command, options);
};
