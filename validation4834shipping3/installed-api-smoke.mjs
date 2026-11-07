// Run only in the approved --network none shipping container after a real,
// fresh offline npm install of the branch-built tarball. No mocked transport.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [consumer, kind, output] = process.argv.slice(2);
assert(['baseline', 'candidate'].includes(kind));
assert(consumer?.startsWith('/work/') && output?.startsWith('/work/'));
const pkgDir = join(consumer, 'node_modules/@claude-flow/cli');
const metadata = JSON.parse(await readFile(join(pkgDir, 'package.json'), 'utf8'));
assert.equal(metadata.name, '@claude-flow/cli');
assert.equal(metadata.version, '3.72.2');
const req = createRequire(join(pkgDir, 'package.json'));
const absent = [];
for (const name of ['@ruvector/attention-linux-x64-gnu', '@ruvector/sona-linux-x64-gnu']) {
  assert.throws(() => req.resolve(name), error => error.code === 'MODULE_NOT_FOUND');
  absent.push(name);
}
// neural's regular dependency keeps memory present even with omit=optional.
const memoryRealpath = req.resolve('@claude-flow/memory');
const consumerLock = JSON.parse(await readFile(join(consumer, 'package-lock.json'), 'utf8'));
const memoryLockEntries = Object.entries(consumerLock.packages || {})
  .filter(([name]) => name.endsWith('node_modules/@claude-flow/memory'))
  .map(([name, value]) => ({ name, version: value.version, optional: value.optional ?? null }));
assert(memoryLockEntries.length > 0);
// Resolve the subpath production actually imports, not a new package entrypoint.
const cliCore = req.resolve('@claude-flow/cli-core/mcp-tools/types');
let coreDirectory = dirname(cliCore);
let coreVersion;
while (coreDirectory !== '/') {
  try {
    const core = JSON.parse(await readFile(join(coreDirectory, 'package.json'), 'utf8'));
    if (core.name === '@claude-flow/cli-core') { coreVersion = core.version; break; }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  coreDirectory = dirname(coreDirectory);
}
assert.equal(coreVersion, '3.7.5');
const project = join(consumer, 'project');
const home = join(consumer, 'home');
await mkdir(project, { recursive: false });
await mkdir(home, { recursive: false });
assert.notEqual(project, home);
assert.notEqual(project, '/');
for (const key of Object.keys(process.env)) {
  if (/API_KEY|AUTH_TOKEN|ACCESS_TOKEN|SECRET|PASSWORD|PROXY|BASE_URL/.test(key) ||
      ['GH_TOKEN', 'GITHUB_TOKEN', 'RUFLO_PROVIDER', 'CLAUDE_FLOW_CONFIG'].includes(key)) {
    delete process.env[key];
  }
}
Object.assign(process.env, { HOME: home, USERPROFILE: home, CLAUDE_FLOW_CWD: project,
  RUFLO_DAEMON_AUTOSTART: '0', CLAUDE_FLOW_AI_WORKERS: '0',
  HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', DO_NOT_TRACK: '1', CI: 'true' });
process.chdir(project);
await writeFile(join(project, 'claude-flow.config.json'), '{"agents":{"providers":[]}}');
let binRel = metadata.bin;
if (binRel && typeof binRel === 'object') {
  binRel = binRel['claude-flow'] || binRel.cli || Object.values(binRel)[0];
}
assert.equal(typeof binRel, 'string');
const bin = resolve(pkgDir, binRel);
assert(bin.startsWith(pkgDir + '/'));
const commands = [];
async function command(label, args) {
  const record = { label, args, cwd: project, stdout: '', stderr: '' };
  commands.push(record);
  return new Promise((resolveResult, reject) => {
    const child = spawn(process.execPath, [bin, ...args], {
      cwd: project, env: process.env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(label + ' timed out')); }, 30000);
    child.stdout.on('data', data => {
      record.stdout += String(data);
      if (record.stdout.length > 1024 * 1024) child.kill('SIGKILL');
    });
    child.stderr.on('data', data => {
      record.stderr += String(data);
      if (record.stderr.length > 1024 * 1024) child.kill('SIGKILL');
    });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', (code, signal) => {
      clearTimeout(timer); record.code = code; record.signal = signal;
      if (signal) reject(new Error(label + ' terminated: ' + signal));
      else resolveResult(record);
    });
  });
}
const requests = [];
const server = createServer(async (incoming, response) => {
  try {
    let body = '';
    for await (const chunk of incoming) body += String(chunk);
    requests.push({ method: incoming.method, url: incoming.url,
      authorization: incoming.headers.authorization, body: body ? JSON.parse(body) : null });
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ id: 'shipping-loopback', model: 'shipping-native-model',
      data: [{ id: 'shipping-native-model' }],
      choices: [{ message: { content: 'shipping loopback answer' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5 } }));
  } catch (error) {
    response.destroy(error);
  }
});
await new Promise((resolveReady, reject) => {
  server.once('error', reject); server.listen(0, '127.0.0.1', resolveReady);
});
const origin = `http://127.0.0.1:${server.address().port}`;
const watchdog = setTimeout(() => {
  console.error('Installed shipping smoke exceeded 120 seconds.'); process.exit(1);
}, 120000);
let result;
try {
  const version = await command('installed-version', ['--version']);
  assert.equal(version.code, 0);
  assert.match(version.stdout, /3\.72\.2/);
  const configure = await command('installed-configure', ['providers', 'configure',
    '--provider', 'openai', '--endpoint', origin + '/gateway/v1/',
    '--key', 'shipping-synthetic-secret', '--model', 'shipping-native-model']);
  assert.equal(configure.code, 0);
  const config = JSON.parse(await readFile(join(project, 'claude-flow.config.json'), 'utf8'));
  const entries = config['agents.providers'] || config.agents?.providers;
  assert(entries.some(p => p.name.toLowerCase() === 'openai' &&
    p.baseUrl === origin + '/gateway/v1/' && p.model === 'shipping-native-model'));
  const probe = await command('installed-probe', ['providers', 'test', '--provider', 'openai']);
  const types = await import(pathToFileURL(join(pkgDir, 'dist/src/mcp-tools/types.js')).href);
  assert.equal(types.getProjectCwd(), project);
  const { agentTools } = await import(pathToFileURL(join(pkgDir, 'dist/src/mcp-tools/agent-tools.js')).href);
  const spawnTool = agentTools.find(tool => tool.name === 'agent_spawn');
  const executeTool = agentTools.find(tool => tool.name === 'agent_execute');
  assert(spawnTool && executeTool);
  const spawned = await spawnTool.handler({ agentType: 'coder',
    config: { provider: 'openai', model: 'shipping-native-model' } });
  assert.equal(spawned.success, true);
  const executed = await executeTool.handler({ agentId: spawned.agentId, prompt: 'Return a short answer.' });
  if (kind === 'candidate') {
    assert.equal(probe.code, 0);
    assert.equal(executed.success, true);
    assert.equal(executed.output, 'shipping loopback answer');
    assert.deepEqual(requests.map(row => [row.method, row.url]),
      [['GET', '/gateway/v1/models'], ['POST', '/gateway/v1/chat/completions']]);
    assert(requests.every(row => row.authorization === 'Bearer shipping-synthetic-secret'));
    assert.equal(requests[1].body.model, 'shipping-native-model');
    assert.equal(requests[1].body.max_tokens, 1024);
    assert(!Object.hasOwn(requests[1].body, 'temperature'));
  } else {
    assert.equal(executed.success, false);
    assert.match(String(executed.error), /ANTHROPIC_API_KEY|API key/i);
    assert.equal(requests.length, 0, 'Baseline unexpectedly used the saved endpoint.');
    assert.match(probe.stdout + probe.stderr, /not configured|API key|failed/i);
  }
  result = { state: 'PASS', kind, version: metadata.version, absent, memoryRealpath, memoryLockEntries,
    cli_core_realpath: cliCore, cli_core_version: coreVersion, resolved_project: types.getProjectCwd(),
    commands, requests, execution: executed,
    transport: 'real native fetch to loopback; Docker network none; no public provider verification' };
} finally {
  clearTimeout(watchdog); server.closeAllConnections();
  await new Promise(resolveClosed => server.close(resolveClosed));
}
await writeFile(output, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ state: result.state, kind, requests: requests.length }));
