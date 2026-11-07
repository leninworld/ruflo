/** Real HTTP -> MCP server -> registry dispatch. No transport/registry mocks. */
import { afterEach, describe, expect, it } from 'vitest';
import { PassThrough } from 'node:stream';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { createMCPServer } from '../src/server.js';
import { StdioTransport } from '../src/transport/stdio.js';
import type { ILogger, ToolContext } from '../src/types.js';

const logger: ILogger = { debug() {}, info() {}, warn() {}, error() {} };
let server: ReturnType<typeof createMCPServer> | undefined;
afterEach(async () => { await server?.stop(); server = undefined; });
const initialize = { jsonrpc: '2.0', id: 0, method: 'initialize', params: {
  protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'cancellation-regression', version: '1' },
} };

describe('request cancellation through both MCP dispatch routes (#4876)', () => {
  it.each(['tools/call', 'cancellation_probe'])('passes only the transport signal through %s and isolates another request', async (method) => {
    server = createMCPServer({ transport: 'http', host: '127.0.0.1', port: 0, requestTimeout: 150, enableCaching: false }, logger);
    let started!: () => void;
    let cancelled!: () => void;
    const entered = new Promise<void>(resolve => { started = resolve; });
    const stopped = new Promise<void>(resolve => { cancelled = resolve; });
    const contexts: ToolContext[] = [];
    server.registerTool({
      name: 'cancellation_probe', description: 'synthetic cancellation boundary',
      inputSchema: { type: 'object', properties: { wait: { type: 'boolean' }, signal: { type: 'object' } } },
      handler: async (input, context) => {
        if (!context) throw new Error("MCP dispatch omitted the tool context");
        contexts.push(context);
        if (!(input as { wait?: boolean }).wait) return { ok: true };
        started();
        if (!context.signal) return { missingSignal: true };
        await new Promise<void>((resolve) => {
          const abort = () => { cancelled(); resolve(); };
          if (context.signal!.aborted) abort();
          else context.signal!.addEventListener('abort', abort, { once: true });
        });
        return { ignoredCancellation: true };
      },
    });
    await server.start();
    const transport = (server as unknown as { transports: Array<{ server: Server }> }).transports[0];
    const port = (transport.server.address() as AddressInfo).port;
    const post = (message: unknown) => fetch(`http://127.0.0.1:${port}/rpc`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(message),
    });
    expect((await (await post(initialize)).json()).error).toBeUndefined();
    const message = (id: number, wait: boolean) => ({ jsonrpc: '2.0', id, method,
      params: method === 'tools/call'
        ? { name: 'cancellation_probe', arguments: { wait, signal: { aborted: true } } }
        : { wait, signal: { aborted: true } },
    });
    const slow = post(message(1, true));
    await entered;
    const fast = await post(message(2, false));
    expect(fast.status).toBe(200);
    expect((await fast.json()).error).toBeUndefined();
    expect(contexts).toHaveLength(2);
    expect(contexts[0].signal).toBeInstanceOf(AbortSignal);
    expect(contexts[1].signal).toBeInstanceOf(AbortSignal);
    expect(contexts[0].signal).not.toBe(contexts[1].signal);
    // A JSON argument named signal is input, never transport authority.
    expect(contexts[0].signal?.aborted).toBe(false);
    const expired = await slow;
    expect(expired.status).toBe(408);
    await expired.json();
    await stopped;
    expect(contexts[0].signal?.aborted).toBe(true);
    expect(contexts[1].signal?.aborted).toBe(false);
    expect(contexts[0].sessionId).toBe(contexts[1].sessionId);
  });

  it('keeps real stdio requests on the one-argument transport contract', async () => {
    const input = new PassThrough();
    const output = new PassThrough();
    const transport = new StdioTransport(logger, { inputStream: input, outputStream: output });
    let seen: unknown = 'not called';
    transport.onRequest(async (request, context) => {
      seen = context;
      return { jsonrpc: '2.0', id: request.id, result: { ok: true } };
    });
    const response = new Promise<string>(resolve => {
      let text = '';
      output.on('data', chunk => { text += chunk.toString(); if (text.includes('\n')) resolve(text); });
    });
    try {
      await transport.start();
      input.write(JSON.stringify({ jsonrpc: '2.0', id: 7, method: 'probe', params: { signal: { aborted: true } } }) + '\n');
      expect(JSON.parse(await response)).toMatchObject({ id: 7, result: { ok: true } });
      expect(seen).toBeUndefined();
    } finally {
      await transport.stop();
      input.destroy(); output.destroy();
    }
  });
});
