/** Real native SQLite/scorer with a deterministic synthetic vector provider.
 * This is algorithm/lifecycle proof, not ONNX inference or latency evidence. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { QueryVectorMemo } from '../src/memory/search-request.js';
import * as initializer from '../src/memory/memory-initializer.js';
import { __setMemoryBridgeRegistryForTests, bridgeSearchEntries, ensureBridgeSchema, shutdownBridge } from '../src/memory/memory-bridge.js';
let db: Database.Database;
let agentdb: any;
const vector = () => [1, ...Array(383).fill(0)];
beforeEach(() => {
  vi.stubEnv('CLAUDE_FLOW_DISABLE_BRIDGE', '0');
  db = new Database(':memory:');
  expect(ensureBridgeSchema(db)).toBe(true);
  agentdb = { database: db, embedder: { pipeline: {}, embed: vi.fn(async () => vector()) } };
  __setMemoryBridgeRegistryForTests({ getAgentDB: () => agentdb });
  const insert = db.prepare('INSERT INTO memory_entries (id,key,namespace,content,embedding) VALUES (?,?,?,?,?)');
  for (let i = 0; i < 74; i++) {
    insert.run(`id${i}`, 'shared-key', `ns${i}`, `retrieval query ${i}`, JSON.stringify(vector()));
  }
});
afterEach(() => { vi.restoreAllMocks(); __setMemoryBridgeRegistryForTests(null); db.close(); vi.unstubAllEnvs(); });
describe('native query reuse without changing namespace ranking (#4876)', () => {
  it('matches 74 independent namespace searches with one successful query vector', async () => {
    const baseline = [];
    for (let i = 0; i < 74; i++) baseline.push((await bridgeSearchEntries({ query: 'retrieval query', namespace: `ns${i}` }))?.results);
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(74);
    agentdb.embedder.embed.mockClear();
    const queryMemo = new QueryVectorMemo();
    const candidate = [];
    for (let i = 0; i < 74; i++) candidate.push((await bridgeSearchEntries({ query: 'retrieval query', namespace: `ns${i}`, queryMemo }))?.results);
    expect(candidate).toEqual(baseline);
    expect(candidate.every(rows => rows?.length === 1)).toBe(true);
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(1);
  });
  it('preserves real SQLite BM25-only scores when the local provider is degraded', async () => {
    agentdb.embedder.pipeline = null;
    // Explicit provider-failure injection. SQL/BM25/scoring are real; actual
    // hash vectors are covered separately by the offline sql.js profile.
    vi.spyOn(initializer, 'generateLocalEmbedding').mockResolvedValue({
      embedding: [1, ...Array(127).fill(0)], dimensions: 128,
      model: 'synthetic-degraded-provider', backend: 'mock',
    });
    const baseline = [];
    const candidate = [];
    const queryMemo = new QueryVectorMemo();
    for (let i = 0; i < 74; i++) {
      const options = { query: 'retrieval query', namespace: `ns${i}` };
      const before = await bridgeSearchEntries(options);
      const after = await bridgeSearchEntries({ ...options, queryMemo });
      expect(before?.searchMethod).toBe('bm25-only');
      expect(after?.searchMethod).toBe('bm25-only');
      baseline.push(before?.results);
      candidate.push(after?.results);
    }
    expect(candidate).toEqual(baseline);
    expect(candidate.every(rows => rows?.length === 1)).toBe(true);
    expect(agentdb.embedder.embed).not.toHaveBeenCalled();
  });
  it('re-embeds when the function changes on the same embedder', async () => {
    const queryMemo = new QueryVectorMemo();
    await bridgeSearchEntries({ query: 'query', namespace: 'ns0', queryMemo });
    const replacement = vi.fn(async () => vector());
    agentdb.embedder.embed = replacement;
    await bridgeSearchEntries({ query: 'query', namespace: 'ns1', queryMemo });
    expect(replacement).toHaveBeenCalledTimes(1);
  });
  it('re-embeds when a native model pipeline changes without replacing the embedder', async () => {
    const queryMemo = new QueryVectorMemo();
    await bridgeSearchEntries({ query: 'query', namespace: 'ns0', queryMemo });
    agentdb.embedder.pipeline = {};
    await bridgeSearchEntries({ query: 'query', namespace: 'ns1', queryMemo });
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(2);
  });
  it('keeps unknown dimensions uncached without rejecting the original result', async () => {
    agentdb.embedder.embed.mockResolvedValue([1, 0, 0]);
    const queryMemo = new QueryVectorMemo();
    for (const namespace of ['ns0', 'ns1']) {
      expect(await bridgeSearchEntries({ query: 'retrieval', namespace, queryMemo })).toMatchObject({ success: true });
    }
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(2);
  });
  it('observes a concurrent namespace update instead of reusing a database snapshot', async () => {
    const queryMemo = new QueryVectorMemo();
    await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns0', queryMemo });
    db.prepare('UPDATE memory_entries SET content=? WHERE namespace=?').run('retrieval newly written', 'ns1');
    const result = await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns1', queryMemo });
    expect(result?.results[0].content).toBe('retrieval newly written');
  });
  it('does not retain an unready result when the same provider becomes ready', async () => {
    const queryMemo = new QueryVectorMemo();
    agentdb.embedder.pipeline = undefined;
    await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns0', queryMemo });
    await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns1', queryMemo });
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(2);
    agentdb.embedder.pipeline = {};
    await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns2', queryMemo });
    await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns3', queryMemo });
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(3);
  });
  it('rechecks provider identity after shutdown between namespaces with the same request memo', async () => {
    const queryMemo = new QueryVectorMemo();
    expect(await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns0', queryMemo })).toMatchObject({ success: true });
    expect(agentdb.embedder.embed).toHaveBeenCalledTimes(1);
    await shutdownBridge();
    const replacement = { database: db, embedder: { pipeline: {}, embed: vi.fn(async () => vector()) } };
    __setMemoryBridgeRegistryForTests({ getAgentDB: () => replacement });
    expect(await bridgeSearchEntries({ query: 'retrieval', namespace: 'ns1', queryMemo })).toMatchObject({ success: true });
    expect(replacement.embedder.embed).toHaveBeenCalledTimes(1);
    expect(db.open).toBe(true);
  });
  it('keeps shutdown waiting for the current namespace lease during awaited embedding', async () => {
    let enter!: () => void;
    const entered = new Promise<void>(resolve => { enter = resolve; });
    let release!: (value: number[]) => void;
    const held = new Promise<number[]>(resolve => { release = resolve; });
    agentdb.embedder.embed.mockImplementationOnce(async () => { enter(); return await held; });
    const search = bridgeSearchEntries({ query: 'retrieval', namespace: 'ns0', queryMemo: new QueryVectorMemo() });
    await entered;
    let retired = false;
    const retirement = shutdownBridge().then(() => { retired = true; });
    try {
      await new Promise<void>(resolve => setImmediate(resolve));
      expect(retired).toBe(false);
    } finally { release(vector()); }
    expect(await search).toMatchObject({ success: true });
    await retirement;
    expect(retired).toBe(true);
    // The memo never owns or closes the borrowed native handle.
    expect(db.open).toBe(true);
  });
  it('propagates an abort after embedding instead of returning null for fallback', async () => {
    const controller = new AbortController();
    agentdb.embedder.embed.mockImplementationOnce(async () => { controller.abort(); return vector(); });
    await expect(bridgeSearchEntries({ query: 'retrieval', namespace: 'ns0', signal: controller.signal, queryMemo: new QueryVectorMemo() })).rejects.toMatchObject({ name: 'AbortError' });
  });
});
