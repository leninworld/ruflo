/** Cooperative cancellation at memory-search phase and namespace boundaries. */
export function throwIfSearchAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  const error = new Error('Memory search cancelled', { cause: signal.reason });
  error.name = 'AbortError';
  throw error;
}

interface MemoizedEmbedding {
  embedding: number[];
  dimensions: number;
  model: string;
  backend: 'onnx' | 'mock';
}

/** One unified request owns this memo; it never owns database or model handles. */
export class QueryVectorMemo {
  private readonly identities = new WeakMap<object, number>();
  private readonly vectors = new Map<string, readonly number[]>();
  private readonly metadata = new Map<string, Readonly<Omit<MemoizedEmbedding, 'embedding'>>>();
  private nextIdentity = 0;

  private key(query: string, dimensions: number, provider: readonly unknown[]): string {
    const identities = provider.map((part) => {
      if ((typeof part === 'object' && part !== null) || typeof part === 'function') {
        const object = part as object;
        let identity = this.identities.get(object);
        if (identity === undefined) {
          identity = ++this.nextIdentity;
          this.identities.set(object, identity);
        }
        return ['object', identity];
      }
      return [typeof part, part];
    });
    return JSON.stringify([query, dimensions, identities]);
  }

  get(query: string, dimensions: number, provider: readonly unknown[]): number[] | undefined {
    const vector = this.vectors.get(this.key(query, dimensions, provider));
    return vector ? Array.from(vector) : undefined;
  }

  put(query: string, dimensions: number, provider: readonly unknown[], vector: readonly number[]): void {
    // Unexpected dimensions or invalid output retain the normal uncached path.
    if (dimensions <= 0 || vector.length !== dimensions || !vector.every(Number.isFinite)) return;
    const key = this.key(query, dimensions, provider);
    this.vectors.set(key, Object.freeze(Array.from(vector)));
    this.metadata.delete(key);
  }

  getEmbedding(query: string, dimensions: number, provider: readonly unknown[]): MemoizedEmbedding | undefined {
    const embedding = this.get(query, dimensions, provider);
    const metadata = this.metadata.get(this.key(query, dimensions, provider));
    return embedding && metadata ? { ...metadata, embedding } : undefined;
  }

  putEmbedding(query: string, dimensions: number, provider: readonly unknown[], result: MemoizedEmbedding): void {
    if (result.dimensions !== dimensions || result.embedding.length !== dimensions
      || !result.embedding.every(Number.isFinite) || dimensions <= 0) return;
    this.put(query, dimensions, provider, result.embedding);
    this.metadata.set(this.key(query, dimensions, provider), Object.freeze({
      dimensions: result.dimensions, model: result.model, backend: result.backend,
    }));
  }
}

export interface SearchEmbeddingOptions {
  normalize?: boolean;
  signal?: AbortSignal;
  queryMemo?: QueryVectorMemo;
  /** Keep native-search, bridge-embedding and SQL fallback preparation distinct. */
  memoScope?: string;
}
