/** Error-injection tests for backend boundaries, separate from the real
 * transport/store integration. ANN failure/abort is deliberately injected. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import * as fs from 'fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import * as bridge from '../src/memory/memory-bridge.js';
import * as initializer from '../src/memory/memory-initializer.js';
import * as rabitq from '../src/memory/rabitq-index.js';
// Keep real filesystem behavior while observing reads through both import
// styles. Do not try to redefine an immutable native ESM namespace export.
const filesystemReads = vi.hoisted(() => ({
  readFileSync: vi.fn(),
  actual: undefined as typeof import('node:fs').readFileSync | undefined,
}));
vi.mock('fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('fs')>();
  filesystemReads.actual = actual.readFileSync;
  filesystemReads.readFileSync.mockImplementation(actual.readFileSync);
  const readFileSync = filesystemReads.readFileSync;
  return { ...actual, readFileSync, default: { ...actual, readFileSync } };
});
vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>();
  filesystemReads.actual = actual.readFileSync;
  filesystemReads.readFileSync.mockImplementation(actual.readFileSync);
  const readFileSync = filesystemReads.readFileSync;
  return { ...actual, readFileSync, default: { ...actual, readFileSync } };
});
let directory: string;
let db: Database.Database;
let dbPath: string;
beforeEach(() => {
  // The whole-v3 suite resets mocks before each test. Restore the real
  // forwarding after that reset so cancellation observes actual file reads.
  if (!filesystemReads.actual) throw new Error('Real filesystem reader was not captured');
  filesystemReads.readFileSync.mockImplementation(filesystemReads.actual);
  directory = mkdtempSync(join(tmpdir(), 'ruflo-cancel-boundary-4876-'));
  dbPath = join(directory, 'memory.db');
  vi.stubEnv('CLAUDE_FLOW_DISABLE_BRIDGE', '0');
  vi.stubEnv('CLAUDE_FLOW_MEMORY_PATH', directory);
  initializer._resetMemoryRootCache();
  db = new Database(dbPath);
  expect(bridge.ensureBridgeSchema(db)).toBe(true);
  bridge.__setMemoryBridgeRegistryForTests({ getAgentDB: () => ({ database: db,
    embedder: { pipeline: {}, embed: async () => [1, ...Array(383).fill(0)] } }) });
});
afterEach(() => {
  vi.restoreAllMocks();
  bridge.__setMemoryBridgeRegistryForTests(null);
  db.close();
  vi.unstubAllEnvs();
  initializer._resetMemoryRootCache();
  // Newly created synthetic container fixture only.
  rmSync(directory, { recursive: true, force: true });
});

describe('cancellation must survive backend fallback boundaries (#4876)', () => {
  it('does not enter SQL embedding after the bridge returns null with an aborted signal', async () => {
    const controller = new AbortController();
    vi.spyOn(bridge, 'bridgeSearchEntries').mockImplementationOnce(async () => {
      controller.abort('bridge initialization interrupted');
      return null;
    });
    const embedding = vi.spyOn(bridge, 'bridgeGenerateEmbedding');
    const ensure = vi.spyOn(rabitq, 'ensureRabitqIndex');
    await expect(initializer.searchEntries({ query: 'retrieval', dbPath, signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' });
    expect(embedding).not.toHaveBeenCalled();
    expect(ensure).not.toHaveBeenCalled();
  });
  it('does not enter SQL provenance reads after an HNSW await is cancelled', async () => {
    const controller = new AbortController();
    vi.spyOn(bridge, 'bridgeSearchEntries').mockResolvedValueOnce(null);
    vi.spyOn(rabitq, 'ensureRabitqIndex').mockResolvedValueOnce(false);
    vi.spyOn(rabitq, 'searchRabitq').mockResolvedValueOnce(null);
    const reads = vi.mocked(fs.readFileSync);
    reads.mockClear();
    let readsAtAbort = -1;
    const hnsw = vi.spyOn(bridge, 'bridgeSearchBruteForceCosine').mockImplementationOnce(async () => {
      readsAtAbort = reads.mock.calls.length;
      controller.abort('HNSW request ended');
      return [{ id: 'id0', key: 'key0', content: 'retrieval', score: 1, namespace: 'default' }];
    });
    await expect(initializer.searchEntries({ query: 'retrieval', dbPath, signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' });
    expect(hnsw).toHaveBeenCalledTimes(1);
    expect(readsAtAbort).toBeGreaterThan(0);
    expect(reads.mock.calls).toHaveLength(readsAtAbort);
  });
  it('does not start HNSW after a RaBitQ await rejects during cancellation', async () => {
    const controller = new AbortController();
    vi.spyOn(bridge, 'bridgeSearchEntries').mockResolvedValueOnce(null);
    const ensure = vi.spyOn(rabitq, 'ensureRabitqIndex').mockImplementationOnce(async () => {
      controller.abort('index request ended');
      throw new Error('index interrupted');
    });
    const candidates = vi.spyOn(rabitq, 'searchRabitq');
    // searchHNSWIndex's first real phase calls this bridge export.
    const hnsw = vi.spyOn(bridge, 'bridgeSearchBruteForceCosine');
    await expect(initializer.searchEntries({ query: 'retrieval', dbPath, signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' });
    expect(ensure).toHaveBeenCalledTimes(1);
    expect(candidates).not.toHaveBeenCalled();
    expect(hnsw).not.toHaveBeenCalled();
  });
});
