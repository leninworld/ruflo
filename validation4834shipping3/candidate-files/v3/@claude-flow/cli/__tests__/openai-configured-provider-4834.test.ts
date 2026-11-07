import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createServer, type Server, type IncomingHttpHeaders } from 'node:http';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { providersCommand } from '../src/commands/providers.js';
import type { CommandContext } from '../src/types.js';
import { callAnthropicMessages, executeAgentTask } from '../src/mcp-tools/agent-execute-core.js';
import { agentTools } from '../src/mcp-tools/agent-tools.js';
import { getProjectCwd } from '../src/mcp-tools/types.js';
import { configManager } from '../src/services/config-file-manager.js';

const observed = vi.hoisted(() => ({ lines: [] as string[], fallback: vi.fn(), embed: vi.fn() }));
vi.mock('../src/output.js', () => ({ output: new Proxy({}, { get: (_target, name) =>
  ['writeln', 'printError'].includes(String(name))
    ? (...args: unknown[]) => observed.lines.push(args.map(String).join(' '))
    : (value: unknown) => String(value),
}) }));
// Learning/fallback isolation only. Requests to the loopback server use real fetch.
vi.mock('../src/ruvector/model-router.js', () => ({ recordModelOutcome: vi.fn(), recordModelOutcomeByModelId: vi.fn() }));
vi.mock('../src/ruvector/neural-router.js', () => ({ nextCostOptimalAlternative: observed.fallback }));
vi.mock('../src/ruvector/task-embedder.js', () => ({ embedTaskWithCache: observed.embed }));

const nativeFetch = globalThis.fetch;
const environment = [
  'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
  'OPENROUTER_API_KEY', 'OPENROUTER_BASE_URL', 'OLLAMA_API_KEY', 'OLLAMA_BASE_URL',
  'OLLAMA_DEFAULT_MODEL', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'RUFLO_PROVIDER',
  'CLAUDE_FLOW_CONFIG', 'CLAUDE_FLOW_ROUTER_FALLBACK_MAX_RETRIES',
];

describe('configured OpenAI-compatible connections (#4834)', () => {
  let root: string;
  let project: string;
  let origin: string;
  let server: Server;
  let responseStatus: number;
  let responseDelay: number;
  let responseText: string | undefined;
  let timers: Array<ReturnType<typeof setTimeout>>;
  let requests: Array<{ url: string; method: string; headers: IncomingHttpHeaders; body: Record<string, unknown> }>;
  let attempts: Array<{ url: string; init: Parameters<typeof fetch>[1] }>;

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), 'ruflo-openai-4834-'));
    project = join(root, 'project');
    const home = join(root, 'home');
    mkdirSync(project); mkdirSync(home);
    for (const key of environment) vi.stubEnv(key, '');
    vi.stubEnv('HOME', home); vi.stubEnv('USERPROFILE', home);
    vi.stubEnv('CLAUDE_FLOW_CWD', project);
    vi.spyOn(process, 'cwd').mockReturnValue(project);
    expect(project).not.toBe(home);
    expect(getProjectCwd()).toBe(project);
    expect(vi.isMockFunction(nativeFetch)).toBe(false);
    observed.lines.length = 0;
    observed.fallback.mockReset().mockResolvedValue(null);
    observed.embed.mockReset().mockResolvedValue([0, 0]);
    responseStatus = 200; responseDelay = 0; responseText = undefined;
    requests = []; attempts = []; timers = [];
    server = createServer(async (request, response) => {
      let text = '';
      for await (const chunk of request) text += String(chunk);
      requests.push({ url: request.url || '', method: request.method || '', headers: request.headers,
        body: text ? JSON.parse(text) : {} });
      const send = () => {
        if (response.destroyed) return;
        response.writeHead(responseStatus, { 'content-type': 'application/json' });
        response.end(responseText ?? JSON.stringify({ id: 'loopback', model: 'local-model',
          data: [{ id: 'local-model' }], choices: [{ message: { content: 'local answer' }, finish_reason: 'stop' }],
          content: [{ type: 'text', text: 'local answer' }], stop_reason: 'end_turn',
          usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5, input_tokens: 2, output_tokens: 3 },
        }));
      };
      if (responseDelay) timers.push(setTimeout(send, responseDelay)); else send();
    });
    await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    vi.stubGlobal('fetch', async (...args: Parameters<typeof fetch>) => {
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
      attempts.push({ url: url.href, init: args[1] });
      // A baseline's hardcoded public endpoint must fail without contacting it.
      if (url.origin !== origin) throw new Error('External networking denied by the test');
      return nativeFetch(...args);
    });
  });

  afterEach(async () => {
    for (const timer of timers) clearTimeout(timer);
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals();
    rmSync(root, { recursive: true, force: true });
  });

  function config(providers: unknown = []) {
    writeFileSync(join(project, 'claude-flow.config.json'), JSON.stringify({ agents: { providers } }));
  }
  function saved(extra: Record<string, unknown> = {}) {
    return { name: 'openai', enabled: true, baseUrl: origin, apiKey: 'saved-test-secret', model: 'local-model', ...extra };
  }
  async function command(name: string, flags: Record<string, unknown>) {
    const action = providersCommand.subcommands?.find(value => value.name === name)?.action;
    if (!action) throw new Error('Missing provider command');
    return action({ flags, args: [] } as unknown as CommandContext);
  }
  function register(extra: Record<string, unknown> = {}) {
    const dir = join(project, '.claude-flow', 'agents'); mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'store.json'), JSON.stringify({ version: '3.0.0', agents: {
      selected: { agentId: 'selected', agentType: 'coder', status: 'idle', health: 1,
        taskCount: 0, config: {}, createdAt: new Date().toISOString(), model: 'sonnet', provider: 'openai', ...extra },
    } }));
  }

  it('#4834 uses saved endpoint for probe through configure and the real HTTP server', async () => {
    config();
    expect(await command('configure', { provider: 'OpenAI', endpoint: `${origin}/v1/`, key: 'saved-test-secret', model: 'local-model' })).toMatchObject({ success: true });
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ url: '/v1/models', method: 'GET' });
    expect(requests[0].headers.authorization).toBe('Bearer saved-test-secret');
  });

  it('#4834 executes saved OpenAI config through the real HTTP server', async () => {
    config([saved()]);
    const result = await callAnthropicMessages({ prompt: 'test prompt' });
    expect(result).toMatchObject({ success: true, output: 'local answer', usage: { inputTokens: 2, outputTokens: 3, totalTokens: 5 } });
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ url: '/v1/chat/completions', method: 'POST', body: { model: 'local-model' } });
    expect(requests[0].body).toHaveProperty('max_tokens', 1024);
    expect(requests[0].body).not.toHaveProperty('temperature');
  });

  it.each(['', '/', '/v1', '/v1/', '/gateway/v1/'])('normalizes the configured base suffix %s for both operations', async suffix => {
    config([saved({ baseUrl: origin + suffix })]);
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    const prefix = suffix.startsWith('/gateway') ? '/gateway' : '';
    expect(requests.map(row => row.url)).toEqual([`${prefix}/v1/models`, `${prefix}/v1/chat/completions`]);
  });

  it('sends an explicitly supplied zero temperature and custom token limit', async () => {
    config([saved()]);
    expect((await callAnthropicMessages({ prompt: 'test', temperature: 0, maxTokens: 42 })).success).toBe(true);
    expect(requests[0].body).toMatchObject({ temperature: 0, max_tokens: 42 });
    expect(requests[0].body).not.toHaveProperty('max_completion_tokens');
  });

  it.each(['file:///tmp/model', 'https://user:secret@api.openai.com', 'https://api.openai.com/v1?secret=x'])('fails closed on the invalid saved URL %j', async baseUrl => {
    config([saved({ baseUrl })]); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: false });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(false);
    expect(attempts).toHaveLength(0);
    expect(observed.lines.join('\n')).not.toContain(baseUrl);
  });

  it('reads literal-dot provider configuration through both entrypoints', async () => {
    writeFileSync(join(project, 'claude-flow.config.json'), JSON.stringify({ 'agents.providers': [saved()] }));
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests).toHaveLength(2);
  });

  it('keeps an ambient OpenAI key away from a project-chosen host', async () => {
    config([saved({ apiKey: undefined })]); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests).toHaveLength(2);
    expect(requests.every(row => row.headers.authorization === undefined)).toBe(true);
  });

  it('uses only the saved key at a project endpoint when another key is in the environment', async () => {
    config([saved()]); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests.map(row => row.headers.authorization)).toEqual(['Bearer saved-test-secret', 'Bearer saved-test-secret']);
  });

  it('uses an explicit environment endpoint and its matching environment key', async () => {
    config([saved({ baseUrl: 'http://project.invalid', apiKey: 'project-test-secret' })]);
    vi.stubEnv('OPENAI_BASE_URL', origin + '/gateway/v1'); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests.map(row => row.headers.authorization)).toEqual(['Bearer environment-test-secret', 'Bearer environment-test-secret']);
    expect(requests.map(row => row.url)).toEqual(['/gateway/v1/models', '/gateway/v1/chat/completions']);
  });

  it('does not use a saved key at a different environment-selected endpoint', async () => {
    config([saved({ baseUrl: 'http://project.invalid', apiKey: 'project-test-secret' })]);
    vi.stubEnv('OPENAI_BASE_URL', origin);
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests[0].headers.authorization).toBeUndefined();
  });

  it('allows a saved key fallback only for the same normalized environment endpoint', async () => {
    config([saved({ baseUrl: origin + '/v1/' })]); vi.stubEnv('OPENAI_BASE_URL', origin);
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(true);
    expect(requests[0].headers.authorization).toBe('Bearer saved-test-secret');
  });

  it.each(['', 'not a url', 'file:///tmp/model', 'https://user:secret@example.test', 'https://example.test/v1?q=secret', 'https://example.test/v1#secret'])('rejects invalid configure input %j before changing disk or the cached object', async endpoint => {
    config([saved()]);
    const cached = configManager.getConfig(project);
    const before = JSON.stringify(cached);
    const disk = readFileSync(join(project, 'claude-flow.config.json'), 'utf8');
    expect(await command('configure', { provider: 'OpenAI', endpoint })).toMatchObject({ success: false });
    expect(JSON.stringify(cached)).toBe(before);
    expect(readFileSync(join(project, 'claude-flow.config.json'), 'utf8')).toBe(disk);
    expect(attempts).toHaveLength(0);
  });

  it.each(['baseUrl', 'apiKey', 'model'])('rejects a malformed stored %s without falling back to the cloud', async field => {
    config([saved({ [field]: 42 })]); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: false });
    expect((await callAnthropicMessages({ prompt: 'test' })).success).toBe(false);
    expect(attempts).toHaveLength(0);
  });

  it.each(['{broken', '[]', '{"agents":42}', '{"agents":{"providers":null}}', '{"agents":"environment-test-secret"'])('fails closed on unreadable or malformed config %j', async content => {
    writeFileSync(join(project, 'claude-flow.config.json'), content);
    vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    const result = await callAnthropicMessages({ prompt: 'test', provider: 'openai' as never });
    expect(result.success).toBe(false); expect(result.error).toMatch(/OpenAI.*config/i);
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: false });
    expect(observed.lines.join('\n')).not.toContain('environment-test-secret');
    expect(attempts).toHaveLength(0);
  });

  it('ignores a disabled saved entry for both probe and execution', async () => {
    config([saved({ enabled: false })]);
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: false });
    expect(observed.lines.join('\n')).toMatch(/disabled/i);
    expect((await callAnthropicMessages({ prompt: 'test', provider: 'openai' as never })).success).toBe(false);
    expect(attempts).toHaveLength(0);
  });

  it('can use environment settings without reviving a disabled saved entry', async () => {
    config([saved({ enabled: false, baseUrl: 'http://project.invalid' })]);
    vi.stubEnv('OPENAI_BASE_URL', origin); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    expect(await command('test', { provider: 'openai' })).toMatchObject({ success: true });
    expect(observed.lines.join('\n')).toMatch(/disabled/i);
    expect((await callAnthropicMessages({ prompt: 'test', provider: 'openai' as never, model: 'user-model', explicitModel: true } as Parameters<typeof callAnthropicMessages>[0])).success).toBe(true);
    expect(requests.map(row => row.headers.authorization)).toEqual(['Bearer environment-test-secret', 'Bearer environment-test-secret']);
  });

  it('requires a custom endpoint model before making an execution request', async () => {
    config([saved({ model: undefined })]);
    expect((await callAnthropicMessages({ prompt: 'test', provider: 'openai' as never })).error).toMatch(/model/i);
    expect(attempts).toHaveLength(0);
  });

  it.each(['native-user-model', 'anthropic/user-native-model'])('preserves the explicit native modelId %s', async modelId => {
    config([saved()]); register({ modelId, modelRoutedBy: 'explicit' });
    expect((await executeAgentTask({ agentId: 'selected', prompt: 'test' })).success).toBe(true);
    expect(requests[0].body.model).toBe(modelId);
  });

  it('uses the configured model instead of a router-picked vendor slug', async () => {
    config([saved()]); register({ modelId: 'inclusionai/router-model', modelRoutedBy: 'router' });
    expect((await executeAgentTask({ agentId: 'selected', prompt: 'test' })).success).toBe(true);
    expect(requests[0].body.model).toBe('local-model');
  });

  it('propagates an explicit OpenAI provider and model through real MCP spawn and execute', async () => {
    config([saved()]); vi.stubEnv('ANTHROPIC_API_KEY', 'other-provider-secret');
    const spawn = agentTools.find(tool => tool.name === 'agent_spawn'); expect(spawn).toBeDefined();
    // Deliberately no task: spawn must not start an embedder or download a model.
    const result = await spawn!.handler({ agentType: 'coder', config: { provider: 'openai', model: 'spawn-model' } }) as { success: boolean; agentId: string };
    expect(result.success).toBe(true);
    expect((await executeAgentTask({ agentId: result.agentId, prompt: 'test' })).success).toBe(true);
    expect(requests[0].body.model).toBe('spawn-model');
    expect(requests[0].url).toBe('/v1/chat/completions');
  });

  it.each(['anthropic', 'openrouter', 'ollama'] as const)('keeps explicit %s execution on its existing branch', async provider => {
    config([saved()]);
    vi.stubEnv('ANTHROPIC_BASE_URL', origin); vi.stubEnv('ANTHROPIC_API_KEY', 'anthropic-test');
    vi.stubEnv('OPENROUTER_BASE_URL', origin); vi.stubEnv('OPENROUTER_API_KEY', 'openrouter-test');
    vi.stubEnv('OLLAMA_BASE_URL', origin); vi.stubEnv('OLLAMA_API_KEY', 'ollama-test');
    vi.stubEnv('RUFLO_PROVIDER', 'openai');
    expect((await callAnthropicMessages({ prompt: 'test', provider, model: 'native-model' })).success).toBe(true);
    expect(requests[0].url).toBe(provider === 'anthropic' ? '/v1/messages' : '/v1/chat/completions');
    expect(requests[0].headers.authorization === 'Bearer saved-test-secret').toBe(false);
  });

  it('lets the explicit agent provider override an environment default', async () => {
    config([saved()]); vi.stubEnv('RUFLO_PROVIDER', 'anthropic');
    vi.stubEnv('ANTHROPIC_BASE_URL', origin); vi.stubEnv('ANTHROPIC_API_KEY', 'other-provider-secret');
    register();
    expect((await executeAgentTask({ agentId: 'selected', prompt: 'test' })).success).toBe(true);
    expect(requests[0].url).toBe('/v1/chat/completions');
  });

  it('does not cross provider boundaries on an OpenAI failure or echo a response secret', async () => {
    config([saved()]); register({ modelId: 'inclusionai/router-model', modelRoutedBy: 'router' });
    responseStatus = 503; responseText = 'saved-test-secret echoed by an untrusted server';
    vi.stubEnv('ANTHROPIC_BASE_URL', origin); vi.stubEnv('ANTHROPIC_API_KEY', 'other-provider-secret');
    const result = await executeAgentTask({ agentId: 'selected', prompt: 'test' });
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/OpenAI.*503/);
    expect(result.model).toBe('local-model');
    expect(JSON.stringify(result)).not.toContain('saved-test-secret');
    expect(result).not.toHaveProperty('provider');
    expect(observed.fallback).not.toHaveBeenCalled(); expect(observed.embed).not.toHaveBeenCalled();
    expect(requests).toHaveLength(1);
  });

  it('reports execution timeout without exposing the endpoint path or secret', async () => {
    config([saved({ baseUrl: origin + '/private-path-token' })]); responseDelay = 200;
    const result = await callAnthropicMessages({ prompt: 'test', timeoutMs: 25 });
    expect(result.success).toBe(false); expect(result.error).toMatch(/timed out/i);
    expect(result.error).not.toContain('private-path-token'); expect(result.error).not.toContain('saved-test-secret');
  });

  it('builds the canonical OpenAI request without contacting a public provider', async () => {
    config(); vi.stubEnv('OPENAI_API_KEY', 'environment-test-secret');
    const result = await callAnthropicMessages({ prompt: 'test', maxTokens: 37 });
    expect(result.success).toBe(false); // The test's external-network guard rejects it before network.
    expect(requests).toHaveLength(0);
    expect(attempts[0].url).toBe('https://api.openai.com/v1/chat/completions');
    const body = JSON.parse(String(attempts[0].init?.body));
    expect(body).toMatchObject({ model: 'gpt-4o', max_completion_tokens: 37 });
    expect(body).not.toHaveProperty('max_tokens'); expect(body).not.toHaveProperty('temperature');
  });

  it('does not execute canonical OpenAI without a key', async () => {
    config([{ name: 'openai', enabled: true, baseUrl: 'https://api.openai.com/v1/' }]);
    const result = await callAnthropicMessages({ prompt: 'test', provider: 'openai' as never });
    expect(result.success).toBe(false); expect(result.error).toMatch(/no API key/i); expect(attempts).toHaveLength(0);
  });

  it('keeps the no-provider error for an unreadable config without OpenAI settings', async () => {
    writeFileSync(join(project, 'claude-flow.config.json'), '{broken');
    const result = await callAnthropicMessages({ prompt: 'test' });
    expect(result.success).toBe(false);
    expect(result.error).toContain('No LLM provider configured');
    expect(attempts).toHaveLength(0);
  });
});
