/** Shared, fetch-free OpenAI connection selection for probe and execution (#4834). */
export interface OpenAIConnection {
  root: string;
  apiKey?: string;
  model?: string;
  canonical: boolean;
  source: 'environment' | 'project' | 'default';
  entryDisabled: boolean;
}

export type OpenAISelection =
  | { ok: true; configured: boolean; connection: OpenAIConnection }
  | { ok: false; configured: boolean; error: string; entryDisabled: boolean };

const CONFIG_ERROR = 'Invalid OpenAI configuration.';
const CANONICAL_ROOT = 'https://api.openai.com';
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** Keep the config manager's literal-dot-key compatibility and reject bad shapes. */
export function openAIProviderEntries(config: Record<string, unknown>): unknown[] {
  let providers: unknown;
  if (Object.hasOwn(config, 'agents.providers')) {
    providers = config['agents.providers'];
  } else {
    const agents = config.agents;
    if (agents !== undefined && !record(agents)) throw new Error(CONFIG_ERROR);
    providers = record(agents) ? agents.providers : undefined;
  }
  if (providers === undefined) return [];
  if (!Array.isArray(providers)) throw new Error(CONFIG_ERROR);
  return providers;
}

/** Accept an API root or terminal /v1, preserving a gateway's preceding path. */
export function normalizeOpenAIBase(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Invalid OpenAI endpoint URL.');
  const text = value.trim();
  let parsed: URL;
  try { parsed = new URL(text); }
  catch { throw new Error('Invalid OpenAI endpoint URL.'); }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password ||
      /^[a-z]+:\/\/[^/]*@/i.test(text) || text.includes('?') || text.includes('#')) {
    throw new Error('Invalid OpenAI endpoint URL.');
  }
  const path = parsed.pathname.replace(/\/+$/, '').replace(/\/v1$/, '');
  return parsed.origin + path;
}

/**
 * Select credentials together with their endpoint. In particular, a custom host
 * in project config never receives OPENAI_API_KEY from the user's environment.
 * CLI configure writes the project entry; explicit env settings override it only
 * within the same credential/endpoint trust boundary.
 */
export function selectOpenAIConnection(
  entries: unknown[],
  env: Record<string, string | undefined>,
): OpenAISelection {
  let configured = false;
  let entryDisabled = false;
  try {
    const found = entries.find(value => record(value) && typeof value.name === 'string' &&
      value.name.toLowerCase() === 'openai');
    entryDisabled = record(found) && found.enabled === false;
    const entry = record(found) && !entryDisabled ? found : undefined;
    const envBase = env.OPENAI_BASE_URL?.trim() || undefined;
    const envKey = env.OPENAI_API_KEY || undefined;
    configured = !!entry || !!envBase || !!envKey;
    for (const key of ['baseUrl', 'apiKey', 'model']) {
      if (entry?.[key] !== undefined && typeof entry[key] !== 'string') throw new Error(CONFIG_ERROR);
    }
    const savedRoot = entry?.baseUrl !== undefined ? normalizeOpenAIBase(entry.baseUrl) : undefined;
    const root = envBase ? normalizeOpenAIBase(envBase) : savedRoot || CANONICAL_ROOT;
    const canonical = root === CANONICAL_ROOT;
    const savedKey = typeof entry?.apiKey === 'string' ? entry.apiKey || undefined : undefined;
    const apiKey = envBase
      ? envKey || (savedRoot === root ? savedKey : undefined)
      : canonical ? savedKey || envKey : savedKey;
    const model = typeof entry?.model === 'string' ? entry.model.trim() || undefined : undefined;
    if (entryDisabled && !configured) {
      return { ok: false, configured, entryDisabled, error: 'The saved OpenAI provider is disabled.' };
    }
    if (canonical && !apiKey) {
      return { ok: false, configured, entryDisabled, error: 'OpenAI is not configured (no API key).' };
    }
    return { ok: true, configured, connection: {
      root, apiKey, model, canonical, entryDisabled,
      source: envBase ? 'environment' : savedRoot ? 'project' : 'default',
    } };
  } catch {
    return { ok: false, configured: true, entryDisabled, error: CONFIG_ERROR };
  }
}
