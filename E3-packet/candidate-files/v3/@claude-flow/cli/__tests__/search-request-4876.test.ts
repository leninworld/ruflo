import { describe, expect, it } from 'vitest';
import { QueryVectorMemo, throwIfSearchAborted } from '../src/memory/search-request.js';

describe('request-owned query vectors (#4876)', () => {
  it('isolates namespaces through shared immutable vectors without sharing mutations', () => {
    const memo = new QueryVectorMemo();
    const provider = ['native', {}, () => {}];
    const original = [1, 0, 0];
    memo.put('query', 3, provider, original);
    original[0] = -1;
    const first = memo.get('query', 3, provider)!;
    expect(first).toEqual([1, 0, 0]);
    first[1] = 5;
    expect(memo.get('query', 3, provider)).toEqual([1, 0, 0]);
  });
  it('invalidates on query, dimensions, embed function, model and path changes', () => {
    const memo = new QueryVectorMemo();
    const embed = () => {};
    const model = {};
    memo.put('query', 2, ['native', embed, model], [1, 0]);
    expect(memo.get('other', 2, ['native', embed, model])).toBeUndefined();
    expect(memo.get('query', 3, ['native', embed, model])).toBeUndefined();
    expect(memo.get('query', 2, ['native', () => {}, model])).toBeUndefined();
    expect(memo.get('query', 2, ['native', embed, {}])).toBeUndefined();
    expect(memo.get('query', 2, ['sqljs', embed, model])).toBeUndefined();
    expect(new QueryVectorMemo().get('query', 2, ['native', embed, model])).toBeUndefined();
  });
  it.each([{ vector: [1] }, { vector: [1, Number.NaN] }, { vector: [1, Infinity] }])('does not remember invalid or mismatched vectors $vector', ({ vector }) => {
    const memo = new QueryVectorMemo();
    memo.put('query', 2, ['provider'], vector);
    expect(memo.get('query', 2, ['provider'])).toBeUndefined();
  });
  it('preserves embedding metadata and isolates returned records from mutation', () => {
    const memo = new QueryVectorMemo();
    const result = { embedding: [1, 0], dimensions: 2, model: 'actual-provider-model', backend: 'onnx' as const };
    memo.putEmbedding('query', 2, ['provider'], result);
    result.model = 'mutated';
    result.embedding[0] = 99;
    const hit = memo.getEmbedding('query', 2, ['provider'])!;
    expect(hit).toEqual({ embedding: [1, 0], dimensions: 2, model: 'actual-provider-model', backend: 'onnx' });
    hit.model = 'another mutation';
    hit.embedding[0] = -1;
    expect(memo.getEmbedding('query', 2, ['provider'])).toEqual({ embedding: [1, 0], dimensions: 2, model: 'actual-provider-model', backend: 'onnx' });
    memo.put('query', 2, ['provider'], [0, 1]);
    expect(memo.getEmbedding('query', 2, ['provider'])).toBeUndefined();
  });
  it('throws a typed cancellation rather than returning a backend failure', () => {
    expect(() => throwIfSearchAborted()).not.toThrow();
    const controller = new AbortController();
    expect(() => throwIfSearchAborted(controller.signal)).not.toThrow();
    controller.abort('timeout');
    expect(() => throwIfSearchAborted(controller.signal)).toThrow(expect.objectContaining({ name: 'AbortError' }));
  });
});
