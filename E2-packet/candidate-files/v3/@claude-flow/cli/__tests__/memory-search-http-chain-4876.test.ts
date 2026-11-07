/** Real HTTP -> MCP server -> CLI adapter -> unified search -> native SQLite.
 * Only the vector provider is synthetic; this does not prove ONNX latency. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import type { Server, ServerResponse } from 'node:http';
import Database from 'better-sqlite3';
import { MCPServerManager } from '../src/mcp-server.js';
import { memoryTools } from '../src/mcp-tools/memory-tools.js';
import * as memoryInitializer from '../src/memory/memory-initializer.js';
import { isLocalCliCall } from '../src/mcp-tools/path-containment.js';
import { __setMemoryBridgeRegistryForTests, ensureBridgeSchema } from '../src/memory/memory-bridge.js';

const unified = memoryTools.find(tool => tool.name === 'memory_search_unified')!;
const original = unified.handler;
const vector = () => [1, ...Array(383).fill(0)];
const namespaces = Array.from({ length: 74 }, (_, i) => `ns${i}`);
let scratch: string;
let previous: string;
let db: Database.Database;
let manager: MCPServerManager | undefined;
let embed: ReturnType<typeof vi.fn>;
let contexts: Array<Record<string, unknown> | undefined>;
let searchObservation: ReturnType<typeof vi.spyOn>;
let finished: Promise<void>;
let didFinish: () => void;

beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), 'ruflo-http-memory-4876-'));
  previous = process.cwd();
  process.chdir(scratch);
  const memory = join(scratch, 'memory');
  mkdirSync(memory);
  mkdirSync(join(scratch, 'home'));
  vi.stubEnv('HOME', join(scratch, 'home'));
  vi.stubEnv('XDG_CONFIG_HOME', join(scratch, 'home', 'config'));
  vi.stubEnv('CLAUDE_FLOW_MEMORY_PATH', memory);
  vi.stubEnv('CLAUDE_FLOW_DISABLE_BRIDGE', '0');
  vi.stubEnv('RUFLO_DAEMON_AUTOSTART', '0');
  vi.stubEnv('RUFLO_MCP_ALLOW_UNAUTHENTICATED_HTTP', '1');
  vi.stubEnv('CLAUDE_FLOW_MEMORY_SEARCH_NAMESPACES', '');
  memoryInitializer._resetMemoryRootCache();
  db = new Database(join(memory, 'memory.db'));
  expect(ensureBridgeSchema(db)).toBe(true);
  const insert = db.prepare('INSERT INTO memory_entries (id,key,namespace,content,embedding) VALUES (?,?,?,?,?)');
  namespaces.forEach((namespace, i) => insert.run(`id${i}`, 'shared-key', namespace, `retrieval query ${i}`, JSON.stringify(vector())));
  embed = vi.fn(async () => vector());
  const agentdb = { database: db, embedder: { pipeline: {}, embed } };
  __setMemoryBridgeRegistryForTests({ getAgentDB: () => agentdb });
  contexts = [];
  // Observation only: Vitest forwards every call to the real implementation.
  searchObservation = vi.spyOn(memoryInitializer, 'searchEntries');
  finished = new Promise<void>(resolve => { didFinish = resolve; });
  unified.handler = async (input, context) => {
    contexts.push(context);
    try { return await original(input, context); }
    finally { if ((input.query as string)?.startsWith('slow ')) didFinish(); }
  };
});
afterEach(async () => {
  await manager?.stop(); manager = undefined;
  unified.handler = original;
  __setMemoryBridgeRegistryForTests(null);
  db?.close();
  process.chdir(previous);
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  memoryInitializer._resetMemoryRootCache();
  // This path is a newly created synthetic test directory inside the container.
  rmSync(scratch, { recursive: true, force: true });
});

async function start(timeout: number) {
  manager = new MCPServerManager({ transport: 'http', host: '127.0.0.1', port: 0,
    daemonize: false, tools: ['memory_search_unified'], requestTimeoutMs: timeout,
    pidFile: join(scratch, 'mcp.pid'), logFile: join(scratch, 'mcp.log') });
  // Preserve errors as ordinary test rejections rather than EventEmitter throws.
  manager.on('error', () => {});
  await manager.start();
  const internal = manager as unknown as { mcpServers: Array<{ transports: Array<{ server: Server }> }> };
  const port = (internal.mcpServers[0].transports[0].server.address() as AddressInfo).port;
  const post = (body: unknown) => fetch(`http://127.0.0.1:${port}/mcp`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const initialized = await post({ jsonrpc: '2.0', id: 0, method: 'initialize', params: {
    protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'memory-4876', version: '1' },
  } });
  expect(initialized.status).toBe(200);
  expect((await initialized.json()).error).toBeUndefined();
  return post;
}

const search = (id: number, method: string, query: string) => ({ jsonrpc: '2.0', id, method,
  params: method === 'tools/call'
    ? { name: 'memory_search_unified', arguments: { query, namespaces, limit: 100 } }
    : { query, namespaces, limit: 100 },
});

describe('native unified search through the real CLI HTTP adapter (#4876)', () => {
  it.each(['tools/call', 'memory_search_unified'])('reuses a request vector over 74 namespaces through %s without granting local authority', async method => {
    const post = await start(10000);
    const response = await post(search(1, method, 'retrieval query'));
    expect(response.status).toBe(200);
    const envelope = await response.json();
    expect(envelope.error).toBeUndefined();
    expect(envelope.result.isError).toBe(false);
    const payload = JSON.parse(envelope.result.content[0].text);
    expect(payload).toMatchObject({ success: true, complete: true, total: 74, namespaceErrorCount: 0 });
    expect(payload.searchedNamespaces).toEqual(namespaces);
    expect(new Set(payload.results.map((row: { namespace: string }) => row.namespace)).size).toBe(74);
    expect(embed).toHaveBeenCalledTimes(1);
    expect(searchObservation).toHaveBeenCalledTimes(74);
    expect(contexts).toHaveLength(1);
    expect(contexts[0]?.signal).toBeInstanceOf(AbortSignal);
    expect(contexts[0]?.transport).toBe('http');
    expect(isLocalCliCall(contexts[0])).toBe(false);
  });

  it('does not search the next namespace after timeout interrupts awaited native embedding', async () => {
    const post = await start(3000);
    let enter!: () => void;
    const entered = new Promise<void>(resolve => { enter = resolve; });
    let release!: (vector: number[]) => void;
    const held = new Promise<number[]>(resolve => { release = resolve; });
    embed.mockImplementationOnce(async () => { enter(); return await held; });
    const request = post(search(2, 'tools/call', 'slow retrieval query'));
    await entered;
    const internal = manager as unknown as { mcpServers: Array<{ transports: Array<{
      requestControllers: Map<ServerResponse, AbortController>;
      sessionControllers: Map<string, Set<AbortController>>;
    }> }> };
    const transport = internal.mcpServers[0].transports[0];
    const heldResponse = Array.from(transport.requestControllers.keys())[0];
    expect(heldResponse).toBeDefined();
    const closeListeners = heldResponse.listenerCount('close');
    try {
      // A separate unified call uses its own memo and finishes while slow
      // inference is held; it must not inherit that request's cancellation.
      const fast = await post(search(3, 'tools/call', 'retrieval query'));
      expect(fast.status).toBe(200);
      const fastEnvelope = await fast.json();
      expect(fastEnvelope.result.isError).toBe(false);
      expect(JSON.parse(fastEnvelope.result.content[0].text)).toMatchObject({ success: true, complete: true, total: 74 });
      expect(contexts).toHaveLength(2);
      expect((contexts[1]?.signal as AbortSignal).aborted).toBe(false);
      expect(contexts[0]?.signal).not.toBe(contexts[1]?.signal);
      const response = await request;
      expect(response.status).toBe(408);
      await response.json();
      expect(contexts[0]?.signal).toBeInstanceOf(AbortSignal);
      expect((contexts[0]?.signal as AbortSignal).aborted).toBe(true);
      expect(isLocalCliCall(contexts[0])).toBe(false);
    } finally { release(vector()); }
    await finished;
    // Let the real registry/server/transport continuations finish cleanup.
    await new Promise<void>(resolve => setImmediate(resolve));
    expect(embed).toHaveBeenCalledTimes(2);
    expect(searchObservation).toHaveBeenCalledTimes(75); // 1 cancelled + 74 fast
    expect((contexts[1]?.signal as AbortSignal).aborted).toBe(false);
    expect(transport.requestControllers.size).toBe(0);
    expect(transport.sessionControllers.size).toBe(0);
    expect(heldResponse.listenerCount('close')).toBe(closeListeners - 1);
  });
});
