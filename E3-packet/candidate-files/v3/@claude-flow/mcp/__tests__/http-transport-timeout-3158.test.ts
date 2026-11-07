import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { HttpTransport } from '../src/transport/http.js';
import { WebSocket } from 'ws';
import type { ILogger, MCPResponse } from '../src/types.js';

describe('HTTP request timeout (#3158)', () => {
  const logger: ILogger = {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
  let transport: HttpTransport | undefined;

  afterEach(async () => {
    await transport?.stop();
    transport = undefined;
    vi.clearAllMocks();
  });

  it('sends one timeout response and ignores a tool result that arrives afterward', async () => {
    transport = new HttpTransport(logger, {
      host: '127.0.0.1',
      port: 0,
      corsEnabled: false,
      requestTimeout: 100,
    });
    let finish!: (response: MCPResponse) => void;
    const work = new Promise<MCPResponse>((resolve) => { finish = resolve; });
    let signal: AbortSignal | undefined;
    transport.onRequest(async (_request, context) => {
      signal = context?.signal;
      return await work;
    });
    await transport.start();

    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const url = `http://127.0.0.1:${port}/rpc`;
    const request = { jsonrpc: '2.0', id: 17, method: 'tools/call', params: { name: 'memory_store' } };
    const timedOut = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
    expect(timedOut.status).toBe(408);
    expect(signal).toBeDefined();
    expect(signal?.aborted).toBe(true);
    const timeoutBody = await timedOut.json();

    finish({ jsonrpc: '2.0', id: 17, result: { stored: true } });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(logger.error).not.toHaveBeenCalled();
    expect(timeoutBody).toMatchObject({ id: 17, error: { code: -32000, message: 'Request timeout' } });
    expect((await transport.getHealthStatus()).metrics).toMatchObject({ messagesSent: 0, errors: 0 });

    // The timed-out request must not poison the next HTTP request.
    transport.onRequest(async (next) => ({ jsonrpc: '2.0', id: next.id, result: { ok: true } }));
    const followUp = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...request, id: 18 }),
    });
    expect(followUp.status).toBe(200);
    expect(await followUp.json()).toMatchObject({ id: 18, result: { ok: true } });
  });

  it('does not try to send a second error when a timed-out tool rejects', async () => {
    transport = new HttpTransport(logger, {
      host: '127.0.0.1',
      port: 0,
      corsEnabled: false,
      requestTimeout: 100,
    });
    let fail!: (error: Error) => void;
    const work = new Promise<MCPResponse>((_resolve, reject) => { fail = reject; });
    let signal: AbortSignal | undefined;
    transport.onRequest(async (_request, context) => {
      signal = context?.signal;
      return await work;
    });
    await transport.start();

    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const response = await fetch(`http://127.0.0.1:${port}/mcp`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 19, method: 'tools/call' }),
    });
    expect(response.status).toBe(408);
    expect(signal).toBeDefined();
    expect(signal?.aborted).toBe(true);
    await response.json();

    fail(new Error('late tool failure'));
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(logger.error).not.toHaveBeenCalled();
  });

  it('cancels only the expired request while a concurrent request completes normally', async () => {
    transport = new HttpTransport(logger, {
      host: '127.0.0.1', port: 0, corsEnabled: false, requestTimeout: 100,
    });
    let finish!: (response: MCPResponse) => void;
    let started!: () => void;
    const startedWork = new Promise<void>((resolve) => { started = resolve; });
    const work = new Promise<MCPResponse>((resolve) => { finish = resolve; });
    const signals = new Map<unknown, AbortSignal | undefined>();
    transport.onRequest(async (request, context) => {
      signals.set(request.id, context?.signal);
      if (request.id === 21) {
        started();
        return await work;
      }
      return { jsonrpc: '2.0', id: request.id, result: { ok: true } };
    });
    await transport.start();
    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const post = (id: number) => fetch(`http://127.0.0.1:${port}/rpc`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id, method: 'tools/call' }),
    });
    const pending = post(21);
    await startedWork;
    const healthy = await post(22);
    expect(healthy.status).toBe(200);
    expect(await healthy.json()).toMatchObject({ id: 22, result: { ok: true } });
    const expired = await pending;
    expect(expired.status).toBe(408);
    await expired.json();
    expect(signals.get(21)).toBeDefined();
    expect(signals.get(22)).toBeDefined();
    expect(signals.get(21)?.aborted).toBe(true);
    expect(signals.get(22)?.aborted).toBe(false);
    expect(signals.get(21)).not.toBe(signals.get(22));
    finish({ jsonrpc: '2.0', id: 21, result: { late: true } });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(logger.error).not.toHaveBeenCalled();
  });

  it('cancels work when the POST client disconnects before the response', async () => {
    transport = new HttpTransport(logger, { host: '127.0.0.1', port: 0, corsEnabled: false });
    let started!: (signal: AbortSignal | undefined) => void;
    let stopped!: () => void;
    const received = new Promise<AbortSignal | undefined>(resolve => { started = resolve; });
    const cancelled = new Promise<void>(resolve => { stopped = resolve; });
    transport.onRequest(async (request, context) => {
      const signal = context?.signal;
      started(signal);
      if (!signal) return { jsonrpc: '2.0', id: request.id, result: { uncancellable: true } };
      return await new Promise<MCPResponse>((_resolve, reject) => {
        signal.addEventListener('abort', () => { stopped(); reject(signal.reason); }, { once: true });
      });
    });
    await transport.start();
    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const client = new AbortController();
    const pending = fetch(`http://127.0.0.1:${port}/rpc`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 23, method: 'tools/call' }), signal: client.signal,
    });
    const signal = await received;
    expect(signal).toBeDefined();
    client.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await cancelled;
    expect(signal?.aborted).toBe(true);
    expect((await transport.getHealthStatus()).metrics).toMatchObject({ messagesSent: 0, errors: 0 });
  });

  it.each(['reject', 'return'] as const)('cancels an SSE-routed POST after stream close even when the handler will %s', async (completion) => {
    transport = new HttpTransport(logger, { host: '127.0.0.1', port: 0, corsEnabled: false });
    let started!: (signal: AbortSignal | undefined) => void;
    let stopped!: () => void;
    const received = new Promise<AbortSignal | undefined>(resolve => { started = resolve; });
    const cancelled = new Promise<void>(resolve => { stopped = resolve; });
    transport.onRequest(async (request, context) => {
      const signal = context?.signal;
      started(signal);
      if (!signal) return { jsonrpc: '2.0', id: request.id, result: { uncancellable: true } };
      return await new Promise<MCPResponse>((resolve, reject) => {
        signal.addEventListener('abort', () => {
          stopped();
          if (completion === 'reject') reject(signal.reason);
          else resolve({ jsonrpc: '2.0', id: request.id, result: { ignoredCancellation: true } });
        }, { once: true });
      });
    });
    await transport.start();
    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const stream = await fetch(`http://127.0.0.1:${port}/mcp`);
    const reader = stream.body!.getReader();
    try {
      const chunk = await reader.read();
      const endpoint = /data: ([^\n]+)/.exec(new TextDecoder().decode(chunk.value))?.[1];
      expect(endpoint).toMatch(/^\/mcp\?sessionId=/);
      const pending = fetch(`http://127.0.0.1:${port}${endpoint}`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 24, method: 'tools/call' }),
      });
      const signal = await received;
      expect(signal).toBeDefined();
      await reader.cancel();
      await cancelled;
      const response = await pending;
      expect(response.status).toBe(500);
      expect(await response.json()).toMatchObject({ id: 24, error: { code: -32800 } });
      expect(signal?.aborted).toBe(true);
      expect((await transport.getHealthStatus()).metrics).toMatchObject({ messagesSent: 0, errors: 0 });
    } finally {
      await reader.cancel();
    }
  });

  it('does not cancel a completed SSE-routed request when the stream later closes', async () => {
    transport = new HttpTransport(logger, { host: '127.0.0.1', port: 0, corsEnabled: false });
    let signal: AbortSignal | undefined;
    transport.onRequest(async (request, context) => {
      signal = context?.signal;
      return { jsonrpc: '2.0', id: request.id, result: { ok: true } };
    });
    await transport.start();
    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const stream = await fetch(`http://127.0.0.1:${port}/mcp`);
    const reader = stream.body!.getReader();
    try {
      const chunk = await reader.read();
      const endpoint = /data: ([^\n]+)/.exec(new TextDecoder().decode(chunk.value))?.[1];
      const sessionId = new URL(endpoint!, `http://127.0.0.1:${port}`).searchParams.get('sessionId')!;
      const serverStream = (transport as unknown as { sseClients: Map<string, { once: (event: 'close', listener: () => void) => void }> }).sseClients.get(sessionId)!;
      const closed = new Promise<void>(resolve => serverStream.once('close', resolve));
      const response = await fetch(`http://127.0.0.1:${port}${endpoint}`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 25, method: 'tools/call' }),
      });
      expect(response.status).toBe(202);
      expect(signal).toBeDefined();
      expect(signal?.aborted).toBe(false);
      await reader.cancel();
      await closed;
      expect(signal?.aborted).toBe(false);
    } finally { await reader.cancel(); }
  });

  it('keeps WebSocket calls on the existing one-argument handler contract', async () => {
    transport = new HttpTransport(logger, { host: '127.0.0.1', port: 0, corsEnabled: false });
    let context: unknown = 'not called';
    transport.onRequest(async (request, received) => {
      context = received;
      return { jsonrpc: '2.0', id: request.id, result: { ok: true } };
    });
    await transport.start();
    const port = ((transport as unknown as { server: Server }).server.address() as AddressInfo).port;
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    try {
      const response = await new Promise<string>((resolve, reject) => {
        ws.once('error', reject);
        ws.once('message', data => resolve(data.toString()));
        ws.once('open', () => ws.send(JSON.stringify({ jsonrpc: '2.0', id: 26, method: 'ping' })));
      });
      expect(JSON.parse(response)).toEqual({ jsonrpc: '2.0', id: 26, result: { ok: true } });
      expect(context).toBeUndefined();
    } finally {
      const closed = new Promise<void>(resolve => ws.once('close', () => resolve()));
      ws.close();
      await closed;
    }
  });
});
