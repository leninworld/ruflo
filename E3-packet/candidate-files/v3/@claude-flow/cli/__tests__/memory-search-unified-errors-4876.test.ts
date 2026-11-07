import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const backend = vi.hoisted(() => ({ searchEntries: vi.fn(), listEntries: vi.fn() }));
vi.mock('../src/memory/memory-initializer.js', () => ({
  ...backend, storeEntry: vi.fn(), getEntry: vi.fn(), deleteEntry: vi.fn(),
  initializeMemoryDatabase: vi.fn(), checkMemoryInitialization: vi.fn(async () => ({ initialized: true })),
}));
const { memoryTools } = await import('../src/mcp-tools/memory-tools.js');
const tool = memoryTools.find(t => t.name === 'memory_search_unified')!;
let previous: string;
let directory: string;
beforeEach(() => {
  previous = process.cwd();
  directory = mkdtempSync(join(tmpdir(), 'ruflo-unified-errors-'));
  process.chdir(directory);
  vi.stubEnv('CLAUDE_FLOW_MEMORY_SEARCH_NAMESPACES', '');
  backend.searchEntries.mockReset();
  backend.listEntries.mockReset();
  backend.searchEntries.mockResolvedValue({ success: true, results: [], searchTime: 0 });
});
afterEach(() => { process.chdir(previous); rmSync(directory, { recursive: true, force: true }); vi.unstubAllEnvs(); });
const call = (input: Record<string, unknown>, context?: Record<string, unknown>) =>
  tool.handler({ query: 'retrieval query', ...input }, context) as Promise<any>;

describe('unified failure and completeness contracts (#4876)', () => {
  it('keeps partial results successful and exposes the failed namespace', async () => {
    backend.searchEntries.mockResolvedValueOnce({ success: true, results: [{ key: 'k', content: 'value', score: 0.8 }], searchTime: 1 });
    backend.searchEntries.mockResolvedValueOnce({ success: false, results: [], error: 'read refused', searchTime: 1 });
    const result = await call({ namespaces: ['good', 'bad'] });
    expect(result).toMatchObject({ success: true, complete: false, total: 1, namespaceErrorCount: 1, namespaceErrors: [{ namespace: 'bad', error: 'read refused' }] });
    expect(result.error).toBeUndefined();
    expect(result.elapsedMs).toBeGreaterThanOrEqual(0);
    expect(result.searchTime).toBeGreaterThan(1_000_000_000_000);
  });
  it('reports all namespaces failing instead of a successful empty result', async () => {
    backend.searchEntries.mockResolvedValue({ success: false, results: [], error: 'Database not found' });
    expect(await call({ namespaces: ['one', 'two'] })).toMatchObject({ success: false, complete: false, total: 0, error: expect.any(String), namespaceErrorCount: 2 });
  });
  it('preserves a successfully read empty namespace as complete', async () => {
    expect(await call({ namespace: 'empty' })).toMatchObject({ success: true, complete: true, total: 0, namespaceErrorCount: 0 });
  });
  it('preserves a successful empty result for an explicitly empty environment list', async () => {
    vi.stubEnv('CLAUDE_FLOW_MEMORY_SEARCH_NAMESPACES', ' ,  ');
    expect(await call({})).toMatchObject({ success: true, complete: true, total: 0, searchedNamespaces: [] });
    expect(backend.searchEntries).not.toHaveBeenCalled();
    expect(backend.listEntries).not.toHaveBeenCalled();
  });
  it('retains legacy namespace searches after enumeration fails', async () => {
    backend.listEntries.mockResolvedValue({ success: false, entries: [], total: 0, error: 'enumeration refused' });
    const result = await call({});
    expect(result).toMatchObject({ success: true, complete: false, namespaceSource: 'legacy-fallback', enumerationError: 'enumeration refused' });
    expect(result.searchedNamespaces).toEqual(['default', 'claude-memories', 'auto-memory', 'patterns', 'tasks', 'feedback']);
    expect(backend.searchEntries).toHaveBeenCalledTimes(6);
  });
  it('discloses the 100k enumeration cap without claiming completeness', async () => {
    backend.listEntries.mockResolvedValue({ success: true, entries: Array.from({ length: 100000 }, () => ({ namespace: 'one' })), total: 100001 });
    expect(await call({})).toMatchObject({ success: true, complete: false, truncatedAtCap: true, namespaceSource: 'dynamic' });
  });
  it('reports truncation when a backend returns fewer than the requested 100k rows', async () => {
    backend.listEntries.mockResolvedValue({ success: true, entries: [{ namespace: 'one' }], total: 2 });
    expect(await call({})).toMatchObject({ success: true, complete: false, truncatedAtCap: true });
  });
  it('bounds diagnostics while reporting the complete number of failures', async () => {
    backend.searchEntries.mockRejectedValue(new Error('read failed'));
    const result = await call({ namespaces: Array.from({ length: 12 }, (_, i) => `ns${i}`) });
    expect(result.namespaceErrors).toHaveLength(10);
    expect(result).toMatchObject({ namespaceErrorCount: 12, namespaceErrorsTruncated: true });
  });
  it('does not start another namespace or turn an abort into partial success', async () => {
    const controller = new AbortController();
    backend.searchEntries.mockImplementationOnce(async () => {
      controller.abort();
      return { success: true, results: [] };
    });
    await expect(call({ namespaces: ['one', 'two'] }, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(backend.searchEntries).toHaveBeenCalledTimes(1);
  });
  it('never converts an aborted enumeration into legacy fallback work', async () => {
    const controller = new AbortController();
    backend.listEntries.mockImplementationOnce(async () => { controller.abort(); throw new Error('interrupted'); });
    await expect(call({}, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(backend.searchEntries).not.toHaveBeenCalled();
  });
});
