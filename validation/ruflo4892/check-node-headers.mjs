// Early fixture in the actual immutable image, before any native addon install.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
const file = '/usr/local/include/node/node_version.h';
const text = readFileSync(file, 'utf8');
const version = ['MAJOR', 'MINOR', 'PATCH'].map(part => {
  const match = text.match(new RegExp(`^#define NODE_${part}_VERSION\\s+(\\d+)`, 'm'));
  assert(match, `NODE_HEADER_VERSION_MISSING:${part}`); return Number(match[1]);
}).join('.');
assert.equal(version, process.versions.node, 'IMAGE_HEADERS_DO_NOT_MATCH_NODE');
assert.equal(process.env.npm_config_nodedir, '/usr/local');
assert(readFileSync('/usr/local/include/node/node.h').length > 0);
assert(readFileSync('/usr/local/include/node/common.gypi').length > 0);
console.log(JSON.stringify({ state: 'PASS', version, nodedir: '/usr/local',
  header_sha256: createHash('sha256').update(text).digest('hex') }));
