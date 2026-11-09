// Agent-authored validation driver. Execute only in the approved offline Docker lane.
// The client is the actual frozen MCP SDK; both servers are the actual installed CLI.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const [consumer, output, variant] = process.argv.slice(2);
assert(consumer?.startsWith('/work/') && output?.startsWith('/work/evidence/'));
assert(['baseline', 'candidate'].includes(variant));
const require = createRequire(join(consumer, 'package.json'));
const sha = file => createHash('sha256').update(readFileSync(file)).digest('hex');
function packageRoot(entry, name) {
  let folder = dirname(realpathSync(entry));
  for (let n = 0; n < 12; n++, folder = dirname(folder)) {
    const file = join(folder, 'package.json');
    if (existsSync(file) && JSON.parse(readFileSync(file)).name === name) return folder;
  }
  throw Error(`PACKAGE_ROOT_MISSING:${name}`);
}
const cli = packageRoot(require.resolve('@claude-flow/cli/package.json'), '@claude-flow/cli');
assert.equal(JSON.parse(readFileSync(join(cli, 'package.json'))).version, '3.74.0');
const cliRequire = createRequire(join(cli, 'package.json'));
const cliManifest = JSON.parse(readFileSync(join(cli,'package.json')));
assert.equal(cliManifest.dependencies['@metaharness/darwin'],'~0.13.0');
assert(!cliManifest.optionalDependencies?.['@metaharness/darwin']);
let darwin;
for (let folder=cli; folder.startsWith(resolve(consumer)); folder=dirname(folder)) {
  const candidate=join(folder,'node_modules/@metaharness/darwin/package.json');
  if (existsSync(candidate)) { darwin={path:realpathSync(candidate),metadata:JSON.parse(readFileSync(candidate))};break; }
  if (folder===dirname(folder)) break;
}
assert(darwin && /^0\.13\./.test(darwin.metadata.version),'REGULAR_DARWIN_DEPENDENCY_MISSING_OR_WRONG_RANGE');
assert(darwin.path.startsWith(realpathSync(consumer)+'/'));

const mcp = packageRoot(cliRequire.resolve('@claude-flow/mcp'), '@claude-flow/mcp');
const validatorFile = join(mcp, 'dist/schema-validator.js');
assert(existsSync(validatorFile), 'SHIPPED_MCP_VALIDATOR_DIST_MISSING');
const { validateSchema } = await import(pathToFileURL(validatorFile).href);
// Stop on a published dependency whose behavior differs from the reviewed workspace source.
for (const [value, valid] of [['a'.repeat(10000), true], ['a'.repeat(10001), false],
  ['\u{1F600}'.repeat(5000), true], ['\u{1F600}'.repeat(5001), false]]) {
  assert.equal(validateSchema(value, { type: 'string', maxLength: 10000 }).valid, valid,
    'SHIPPED_MCP_UTF16_BEHAVIOR_CHANGED');
}
// Use an already frozen dependency, never install a new SDK or borrow a user's SDK.
const sdkRoots = ['/work/source/node_modules/@modelcontextprotocol/sdk'];
const store = '/work/source/v3/node_modules/.pnpm';
if (existsSync(store)) for (const name of readdirSync(store).sort()) {
  if (/^@modelcontextprotocol\+sdk@1\.(27\.1|32\.1)(_|$)/.test(name))
    sdkRoots.push(join(store, name, 'node_modules/@modelcontextprotocol/sdk'));
}
const sdk = sdkRoots.find(folder => existsSync(join(folder, 'dist/esm/client/index.js')));
assert(sdk, 'FROZEN_REAL_MCP_SDK_MISSING');
const sdkMetadata = JSON.parse(readFileSync(join(sdk, 'package.json')));
assert(['1.27.1', '1.32.1'].includes(sdkMetadata.version));
const { Client } = await import(pathToFileURL(join(sdk, 'dist/esm/client/index.js')).href);
const { StdioClientTransport } = await import(pathToFileURL(join(sdk, 'dist/esm/client/stdio.js')).href);
const { SSEClientTransport } = await import(pathToFileURL(join(sdk, 'dist/esm/client/sse.js')).href);
const result = { state: 'INCOMPLETE', variant, sdk: { version: sdkMetadata.version,
  client_sha256: sha(join(sdk, 'dist/esm/client/index.js')) },
  darwin: { version:darwin.metadata.version,manifest_sha256:sha(darwin.path),regular_dependency:true },
  mcp: { version: JSON.parse(readFileSync(join(mcp, 'package.json'))).version,
    realpath: realpathSync(mcp), validator_sha256: sha(validatorFile), utf16_guard: 'PASS' },
  transports: {}, errors: [] };
const TOOL = 'agentdb_hierarchical-store';
const cases = [{ label: 'ascii10000', value: 'a'.repeat(10000), accepted: true },
  { label: 'ascii10001', value: 'a'.repeat(10001), accepted: false },
  { label: 'supplementary5000', value: '\u{1F600}'.repeat(5000), accepted: true },
  { label: 'supplementary5001', value: '\u{1F600}'.repeat(5001), accepted: false }];
const expectedDescription = 'Memory entry value; at most 10,000 UTF-16 code units (JavaScript string length). '
  + 'Standard JSON Schema maxLength counts Unicode code points. Supplementary characters use two runtime units each, '
  + 'so a value that passes a standard client validator can still be refused by this server.';
function decode(reply) {
  assert(reply && Array.isArray(reply.content) && reply.content.length === 1);
  assert.equal(reply.content[0].type, 'text');
  const text = reply.content[0].text;
  assert.equal(typeof text, 'string');
  if (text.startsWith('Invalid input: ')) return { registryError: text };
  const data = JSON.parse(text);
  assert(data && typeof data === 'object' && !Array.isArray(data));
  return data;
}
// Real evidence-decoder success, malformed, wrong envelope, and tamper controls.
assert.deepEqual(decode({ content: [{ type: 'text', text: '{"success":true}' }] }), { success: true });
for (const bad of [null, {}, { content: [] }, { content: [{ type: 'image', text: '{}' }] },
  { content: [{ type: 'text', text: 'malformed-json' }] }]) assert.throws(() => decode(bad));
result.decoder_fixtures = 'PASS';

async function availablePort() {
  const socket = createServer();
  await new Promise((ok, no) => { socket.once('error', no); socket.listen(0, '127.0.0.1', ok); });
  const port = socket.address().port;
  await new Promise((ok, no) => socket.close(error => error ? no(error) : ok()));
  return port; // A race produces a terminal bind failure, never a retry or a new port.
}
async function exercise(client, kind, observed) {
  const tools = [];
  let cursor;
  for (let page = 0; page < 20; page++) {
    const list = await client.listTools(cursor ? { cursor } : {});
    assert(Array.isArray(list.tools)); tools.push(...list.tools);
    cursor = list.nextCursor;
    if (!cursor) break;
  }
  assert(!cursor, 'TOOL_LIST_PAGINATION_BOUND');
  const matches = tools.filter(tool => tool.name === TOOL); assert.equal(matches.length, 1);
  const schema = matches[0].inputSchema.properties.value;
  assert.equal(schema.type, 'string');
  if (variant === 'candidate') {
    assert.equal(schema.maxLength, 10000); assert.equal(schema.description, expectedDescription);
  } else {
    assert(!Object.hasOwn(schema, 'maxLength')); assert.equal(schema.description, 'Memory entry value');
  }
  observed.tool = matches[0]; observed.server = client.getServerVersion(); observed.boundaries = [];
  for (const item of cases) {
    const reply = await client.callTool({ name: TOOL,
      arguments: { key: `synthetic-4892-${kind}-${item.label}`, value: item.value, tier: 'working' } },
    undefined, { timeout: 30000 });
    const data = decode(reply);
    if (item.accepted) {
      assert.equal(data.success, true, `REAL_ACCEPTED_STORAGE_FAILED:${JSON.stringify(data)}`);
      assert.equal(data.durable, true, 'ACCEPTED_STORAGE_NOT_DURABLE');
      assert(!reply.isError);
    } else if (kind === 'http' && variant === 'candidate') {
      assert.equal(reply.isError, true);
      assert.deepEqual(data, { registryError: 'Invalid input: value: String length must be <= 10000' });
    } else {
      assert.deepEqual(data, { success: false, error: 'value exceeds 10000 characters' });
    }
    // Inputs are synthetic. Retain response envelopes, not 10KB request payloads.
    observed.boundaries.push({ label: item.label, units: item.value.length, accepted: item.accepted, reply });
  }
}
async function session(kind) {
  const cwd = join(consumer, `synthetic-${kind}`); mkdirSync(cwd);
  const home = join(cwd, 'home'); mkdirSync(home);
  const env = { ...process.env, HOME: home, USERPROFILE: home, XDG_CONFIG_HOME: join(home, 'config'),
    XDG_CACHE_HOME: join(home, 'cache'), RUFLO_DAEMON_AUTOSTART: '0', RUFLO_NO_SKILLS_SH: '1',
    RUFLO_MCP_ENFORCE_POLICY: '0', RUFLO_FUNNEL_ENABLED: '0', DO_NOT_TRACK: '1',
    HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', npm_config_update_notifier: 'false' };
  let stderr = '', stdout = '', child, transport;
  const observed = { headers: [], boundaries: [], cleanup: 'UNVERIFIED' };
  result.transports[kind] = observed;
  const client = new Client({ name: 'synthetic-schema-4892', version: '1.0.0' }, { capabilities: {} });
  try {
    if (kind === 'stdio') {
      transport = new StdioClientTransport({ command: process.execPath,
        args: [join(cli, 'bin/mcp-server.js')], cwd, env, stderr: 'pipe' });
      transport.stderr?.on('data', data => { stderr = (stderr + data).slice(-16384); });
    } else {
      const port = await availablePort(); const token = 'synthetic-local-4892-token-only';
      const url = new URL(`http://127.0.0.1:${port}/mcp`);
      child = spawn(process.execPath, [join(cli, 'bin/cli.js'), 'mcp', 'start', '-t', 'http',
        '--host', '127.0.0.1', '-p', String(port)],
      { cwd, env: { ...env, RUFLO_MCP_HTTP_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe'] });
      child.on('error', error => { observed.spawn_error = String(error); });
      child.stderr.on('data', data => { stderr = (stderr + data).slice(-16384); });
      child.stdout.on('data', data => { stdout = (stdout + data).slice(-8192); });
      let ready = false;
      for (let n = 0; n < 100 && child.exitCode === null && !observed.spawn_error; n++) {
        try { ready = (await fetch(new URL('/health', url), { signal: AbortSignal.timeout(500) })).ok; }
        catch { /* Bounded readiness observation of this same process, not a rerun. */ }
        if (ready) break;
        await sleep(100);
      }
      assert(ready, 'INSTALLED_HTTP_PROCESS_NOT_READY');
      const unauthorized = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1,
        method: 'tools/list', params: {} }), signal: AbortSignal.timeout(1000) });
      assert.equal(unauthorized.status, 401); await unauthorized.body?.cancel();
      observed.unauthenticated_status = 401;
      const headers = { Authorization: `Bearer ${token}` }; // Harness configuration, not SDK-generated auth.
      const watchedFetch = async (input, init = {}) => {
        const target = new URL(input instanceof Request ? input.url : String(input));
        assert.equal(target.origin, url.origin, 'NON_LOOPBACK_OR_REDIRECT_ENDPOINT');
        const actual = new Headers(input instanceof Request ? input.headers : undefined);
        new Headers(init.headers).forEach((value, key) => actual.set(key, value));
        // Observe what the SDK supplied. Never repair/inject headers here.
        const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
        observed.headers.push({ method: init.method ?? (input instanceof Request ? input.method : 'GET'),
          rpc_method: body?.method ?? null, path: target.pathname,
          session_query: target.searchParams.has('sessionId'),
          harness_configured_headers: ['authorization'], observer_added_headers: [],
          authorization: actual.get('authorization') === `Bearer ${token}`,
          accept: actual.get('accept'), content_type: actual.get('content-type'),
          protocol_version: actual.get('mcp-protocol-version') });
        assert(observed.headers.length <= 32, 'SDK_REQUEST_OBSERVER_CAP');
        return fetch(input, { ...init, redirect: 'error' });
      };
      // Ruflo advertises the real legacy HTTP+SSE endpoint/sessionId query.
      // It does not return a Streamable HTTP Mcp-Session-Id header. Do not invent one.
      transport = new SSEClientTransport(url, { requestInit: { headers }, fetch: watchedFetch,
        eventSourceInit: { fetch: watchedFetch } });
    }
    await client.connect(transport, { timeout: 15000 });
    await exercise(client, kind, observed);
    if (kind === 'http') {
      assert(observed.headers.some(row => row.method === 'GET' && row.path === '/mcp'));
      assert(observed.headers.some(row => row.method === 'POST' && row.session_query));
      assert(observed.headers.every(row => row.authorization && row.observer_added_headers.length===0));
      assert(observed.headers.filter(row=>row.method==='GET').every(row=>row.accept==='text/event-stream'));
      assert(observed.headers.filter(row=>row.method==='POST').every(row=>row.content_type==='application/json'));
      assert(observed.headers.filter(row=>row.method==='POST' && row.rpc_method!=='initialize').every(row=>row.protocol_version));
    }
    observed.state = 'PASS';
  } catch (error) {
    observed.failure = String(error.stack ?? error); throw error;
  } finally {
    await client.close().catch(error => { observed.close_error = String(error); });
    if (child && child.exitCode === null) {
      const closed = new Promise(ok => child.once('close', ok)); child.kill('SIGTERM');
      await Promise.race([closed, sleep(3000)]);
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGKILL'); await Promise.race([closed, sleep(2000)]);
      }
      assert(child.exitCode !== null || child.signalCode !== null, 'CHILD_STOP_UNCONFIRMED');
    }
    observed.cleanup = observed.close_error ? 'FAILED' : 'CLOSED'; observed.stderr_tail = stderr; observed.stdout_tail = stdout;
    if (observed.close_error && !observed.failure) throw Error('SDK_CLOSE_FAILED:'+observed.close_error);
  }
}
try {
  await session('stdio'); await session('http'); result.state = 'PASS';
} catch (error) {
  result.errors.push(String(error.stack ?? error)); throw error;
} finally {
  writeFileSync(output, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
}
