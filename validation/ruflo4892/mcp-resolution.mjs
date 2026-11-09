// Actual dependency identity and behavior, after the early core/MCP builds.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
const require = createRequire('/work/source/v3/@claude-flow/cli/package.json');
const mcpEntry = require.resolve('@claude-flow/mcp');
const file = join(dirname(mcpEntry), 'schema-validator.js');
const coreEntry = require.resolve('@claude-flow/cli-core/mcp-tools/validate-input');
assert(existsSync(file));
const { validateSchema } = await import(pathToFileURL(file).href);
const { validateText } = await import(pathToFileURL(coreEntry).href);
for (const [value, allowed] of [['a'.repeat(10000), true], ['a'.repeat(10001), false],
  ['\u{1F600}'.repeat(5000), true], ['\u{1F600}'.repeat(5001), false],
  ['e\u0301'.repeat(5000), true], ['e\u0301'.repeat(5001), false]]) {
  assert.equal(validateText(value, 'value').valid, allowed);
  assert.equal(validateSchema(value, { type: 'string', maxLength: 10000 }).valid, allowed);
}
const identity = path => ({ path, realpath: realpathSync(path),
  sha256: createHash('sha256').update(readFileSync(path)).digest('hex') });
console.log(JSON.stringify({ state: 'PASS', mcp: identity(file), core: identity(coreEntry),
  boundaries: 'ASCII/supplementary/combining UTF16 guards agree' }, null, 2));
