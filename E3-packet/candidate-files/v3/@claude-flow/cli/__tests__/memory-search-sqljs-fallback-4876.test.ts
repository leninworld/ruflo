/** Real sql.js fallback profile: run in its isolated, network-disabled consumer
 * without optional embedding providers. A real ONNX profile is separate. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { QueryVectorMemo } from '../src/memory/search-request.js';
import { MEMORY_SCHEMA_V3, generateLocalEmbedding, searchEntries, _resetMemoryRootCache } from '../src/memory/memory-initializer.js';

// This environment-specific profile is opt-in; the validation packet must run
// it explicitly and require all cases. Ordinary suites keep their own providers.
const fallback = process.env.RUFLO_TEST_4876_SQLJS_FALLBACK === '1';
let scratch: string;
let previous: string;
let dbPath: string;
beforeEach(async () => {
  if (!fallback) return;
  scratch = mkdtempSync(join(tmpdir(), 'ruflo-wasm-search-4876-'));
  previous = process.cwd();
  process.chdir(scratch);
  mkdirSync(join(scratch, 'home'));
  vi.stubEnv('HOME', join(scratch, 'home'));
  vi.stubEnv('XDG_CONFIG_HOME', join(scratch, 'home', 'config'));
  vi.stubEnv('CLAUDE_FLOW_MEMORY_PATH', scratch);
  vi.stubEnv('CLAUDE_FLOW_DISABLE_BRIDGE', '1');
  _resetMemoryRootCache();
  const actual = await generateLocalEmbedding('retrieval query');
  expect(actual.backend, 'This profile must exercise real hash fallback, not an available model').toBe('mock');
  const initSqlJs = (await import('sql.js')).default;
  const SQL = await initSqlJs();
  const db = new SQL.Database();
  try {
    db.run(MEMORY_SCHEMA_V3);
    const now = Date.now();
    for (let i = 0; i < 74; i++) {
      db.run(`INSERT INTO memory_entries (id,key,namespace,content,embedding,access_count,created_at,updated_at,status)
        VALUES (?,?,?,?,?,0,?,?,'active')`, [`id${i}`, 'same-key', `ns${i}`, 'retrieval query', JSON.stringify(actual.embedding), now, now]);
    }
    dbPath = join(scratch, 'memory.db');
    writeFileSync(dbPath, Buffer.from(db.export()));
  } finally { db.close(); }
});
afterEach(() => {
  if (!fallback) return;
  process.chdir(previous);
  vi.unstubAllEnvs();
  _resetMemoryRootCache();
  // A synthetic container-owned fixture, never an existing/external directory.
  rmSync(scratch, { recursive: true, force: true });
});

describe.skipIf(!fallback)('real sql.js hash fallback search (#4876)', () => {
  it('preserves all namespace scores and ties with a request memo present', async () => {
    const baseline = [];
    const candidate = [];
    const queryMemo = new QueryVectorMemo();
    for (let i = 0; i < 74; i++) {
      const options = { query: 'retrieval query', namespace: `ns${i}`, dbPath };
      const before = await searchEntries(options);
      const after = await searchEntries({ ...options, queryMemo });
      expect(before.success).toBe(true);
      expect(after.success).toBe(true);
      baseline.push(before.results);
      candidate.push(after.results);
    }
    expect(candidate).toEqual(baseline);
    expect(candidate.every(rows => rows.length === 1)).toBe(true);
  });
  it('propagates cancellation rather than returning an empty success or backend error', async () => {
    const controller = new AbortController();
    controller.abort('synthetic request deadline');
    await expect(searchEntries({ query: 'retrieval query', namespace: 'ns0', dbPath, signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' });
  });
  it('keeps degraded embedding metadata truthful when a memo is supplied', async () => {
    const queryMemo = new QueryVectorMemo();
    const first = await generateLocalEmbedding('retrieval query', { queryMemo });
    const next = await generateLocalEmbedding('retrieval query', { queryMemo });
    expect(next).toEqual(first);
    expect(next.backend).toBe('mock');
  });
});
