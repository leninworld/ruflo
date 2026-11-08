/** #4852: the real helper exposes every matching identity without mutating memory. */
import { afterEach, describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { generateKeyPairSync } from 'node:crypto';
import { autoRefreshHelpersIfStale } from '../src/init/helper-refresh.js';

const here = dirname(fileURLToPath(import.meta.url));
const packageHook = resolve(here, '../.claude/helpers/auto-memory-hook.mjs');
const repositoryHook = resolve(here, '../../../../.claude/helpers/auto-memory-hook.mjs');
const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function installHook(source: string) {
  const root = fs.realpathSync(fs.mkdtempSync(join(tmpdir(), 'ruflo-4852-')));
  roots.push(root);
  const home = join(root, 'home');
  const project = join(root, 'project');
  const hook = join(home, '.claude', 'helpers', 'auto-memory-hook.mjs');
  const marker = join(home, 'memory-loaded.marker');
  const mock = join(home, 'mock-memory.mjs');
  fs.mkdirSync(project, { recursive: true });
  fs.mkdirSync(dirname(hook), { recursive: true });
  fs.copyFileSync(source, hook);
  // This is the upstream #4685 sidecar seam, not a replacement backend.
  fs.writeFileSync(mock, `import { writeFileSync } from 'node:fs';
writeFileSync(new URL('./memory-loaded.marker', import.meta.url), 'loaded');
throw new Error('read-matches must not load the optional memory package');`);
  fs.mkdirSync(join(home, '.claude-flow'), { recursive: true });
  fs.writeFileSync(join(home, '.claude-flow', 'memory-package.json'), JSON.stringify({ distPath: mock }));
  return { root, home, project, hook, marker, store: join(project, '.claude-flow', 'data', 'auto-memory-store.json') };
}

type Fixture = ReturnType<typeof installHook>;

function writeStore(f: Fixture, text: string) {
  fs.mkdirSync(dirname(f.store), { recursive: true });
  fs.writeFileSync(f.store, text);
}

function entry(id: string, extra: Record<string, unknown> = {}) {
  return { id, key: 'same', namespace: 'auto-memory', content: `content of ${id}`,
    type: 'semantic', tags: [], metadata: {}, createdAt: 1, updatedAt: 1, ...extra };
}

// As in #4685, compare file bytes/mtimes and the entire directory tree, not atime.
function snapshot(root: string) {
  const out: Record<string, { bytes: string; mtimeMs: number } | 'dir'> = {};
  const walk = (dir: string) => {
    for (const name of fs.readdirSync(dir).sort()) {
      const path = join(dir, name);
      const stat = fs.statSync(path);
      if (stat.isDirectory()) { out[path] = 'dir'; walk(path); }
      else out[path] = { bytes: fs.readFileSync(path).toString('base64'), mtimeMs: stat.mtimeMs };
    }
  };
  walk(root);
  return out;
}

function run(f: Fixture, args: string[]) {
  const result = spawnSync(process.execPath, [f.hook, ...args], {
    cwd: f.project,
    env: { ...process.env, CLAUDE_PROJECT_DIR: f.project, HOME: f.home, USERPROFILE: f.home },
    encoding: 'utf8', timeout: 15_000,
  });
  expect(result.error).toBeUndefined();
  expect(result.signal).toBeNull();
  expect(result.stderr).toBe('');
  return { status: result.status, out: JSON.parse(result.stdout), stdout: result.stdout };
}

for (const [name, source] of [['package', packageHook], ['repository', repositoryHook]] as const) {
  describe(`#4852 ${name} helper read-matches`, () => {
    for (const count of [2, 3]) {
      it(`returns all ${count} full records in file order and preserves ambiguous read refusal`, () => {
        const f = installHook(source);
        const matches = Array.from({ length: count }, (_, i) => entry(`id-${count - i}`, {
          metadata: { nested: ['α', { keep: true }], score: i / 10 },
          unknownField: { original: `record ${i}` }, tags: ['untouched'],
        }));
        // Synthetic direct JSON fixture construction follows #4685 tests.
        writeStore(f, JSON.stringify([entry('other-ns', { namespace: 'other' }), matches[0],
          entry('other-key', { key: 'different' }), ...matches.slice(1)]));
        const before = snapshot(f.root);
        const result = run(f, ['read-matches', 'auto-memory', 'same']);
        expect(result.status).toBe(0);
        expect(result.out).toEqual({ schemaVersion: 1, store: f.store, storePresent: true,
          namespace: 'auto-memory', key: 'same', matchCount: count, entries: matches });
        expect(snapshot(f.root)).toEqual(before);
        const legacy = run(f, ['read', 'auto-memory', 'same']);
        expect(legacy.status).toBe(1);
        expect(legacy.out).toMatchObject({ found: false, entry: null, error: { code: 'AMBIGUOUS_KEY' } });
        expect(snapshot(f.root)).toEqual(before);
        expect(fs.existsSync(f.marker)).toBe(false);
      });
    }

    it('preserves one full record and the original read response contract', () => {
      const f = installHook(source);
      const record = entry('one', { content: 'Exact content\nwith punctuation: {}', metadata: { x: 7 } });
      writeStore(f, JSON.stringify([record]));
      const before = snapshot(f.root);
      const result = run(f, ['read-matches', 'auto-memory', 'same']);
      expect(result.status).toBe(0);
      expect(result.out).toEqual({ schemaVersion: 1, store: f.store, storePresent: true,
        namespace: 'auto-memory', key: 'same', matchCount: 1, entries: [record] });
      const legacy = run(f, ['read', 'auto-memory', 'same']);
      expect(legacy.status).toBe(0);
      expect(legacy.out).toEqual({ schemaVersion: 1, store: f.store, storePresent: true,
        namespace: 'auto-memory', key: 'same', found: true, entry: record });
      expect(snapshot(f.root)).toEqual(before);
      expect(fs.existsSync(f.marker)).toBe(false);
    });

    for (const [label, text] of [['empty', '[]'], ['nonmatching', JSON.stringify([entry('other', { key: 'other' })])]]) {
      it(`reports zero matches for a present ${label} store`, () => {
        const f = installHook(source);
        writeStore(f, text);
        const before = snapshot(f.root);
        const result = run(f, ['read-matches', 'auto-memory', 'same']);
        expect(result.status).toBe(0);
        expect(result.out).toEqual({ schemaVersion: 1, store: f.store, storePresent: true,
          namespace: 'auto-memory', key: 'same', matchCount: 0, entries: [] });
        expect(snapshot(f.root)).toEqual(before);
        expect(fs.existsSync(f.marker)).toBe(false);
      });
    }

    it('reports an absent store without creating any project directory', () => {
      const f = installHook(source);
      const before = snapshot(f.root);
      const result = run(f, ['read-matches', 'auto-memory', 'same']);
      expect(result.status).toBe(0);
      expect(result.out).toEqual({ schemaVersion: 1, store: f.store, storePresent: false,
        namespace: 'auto-memory', key: 'same', matchCount: 0, entries: [] });
      expect(fs.existsSync(join(f.project, '.claude-flow'))).toBe(false);
      expect(snapshot(f.root)).toEqual(before);
      expect(fs.existsSync(f.marker)).toBe(false);
    });

    for (const [label, text, code] of [
      ['corrupt', '{broken', 'STORE_PARSE_FAILED'],
      ['non-array', '{}', 'STORE_NOT_ARRAY'],
      ['bad entry', '[null]', 'STORE_BAD_ENTRY'],
      ['missing id', '[{}]', 'STORE_BAD_ID'],
      ['duplicate id', JSON.stringify([entry('duplicate'), entry('duplicate')]), 'STORE_DUPLICATE_ID'],
    ]) {
      it(`refuses a ${label} store and preserves its bytes and tree`, () => {
        const f = installHook(source);
        writeStore(f, text);
        const before = snapshot(f.root);
        const result = run(f, ['read-matches', 'auto-memory', 'same']);
        expect(result.status).toBe(1);
        expect(result.out).toMatchObject({ schemaVersion: 1, store: f.store, storePresent: true,
          namespace: 'auto-memory', key: 'same', matchCount: 0, entries: [], error: { code } });
        expect(snapshot(f.root)).toEqual(before);
        expect(fs.existsSync(f.marker)).toBe(false);
      });
    }

    it.skipIf(process.platform === 'win32' || (typeof process.getuid === 'function' && process.getuid() === 0))(
      'refuses an unreadable real store (requires non-root POSIX)', () => {
        const f = installHook(source);
        writeStore(f, JSON.stringify([entry('one')]));
        const before = snapshot(f.root);
        fs.chmodSync(f.store, 0o000);
        try {
          const result = run(f, ['read-matches', 'auto-memory', 'same']);
          expect(result.status).toBe(1);
          expect(result.out).toMatchObject({ storePresent: true, matchCount: 0,
            entries: [], error: { code: 'STORE_READ_FAILED' } });
        } finally { fs.chmodSync(f.store, 0o644); }
        expect(snapshot(f.root)).toEqual(before);
        expect(fs.existsSync(f.marker)).toBe(false);
      },
    );

    for (const args of [[], ['auto-memory'], ['', 'same'], ['auto-memory', '']]) {
      it(`rejects invalid arguments ${JSON.stringify(args)} without touching the store`, () => {
        const f = installHook(source);
        const before = snapshot(f.root);
        const result = run(f, ['read-matches', ...args]);
        expect(result.status).toBe(1);
        expect(result.out).toMatchObject({ matchCount: 0, entries: [], error: { code: 'USAGE' } });
        expect(snapshot(f.root)).toEqual(before);
        expect(fs.existsSync(f.marker)).toBe(false);
      });
    }
  });
}

it('#4852 refreshes the disposable locally signed fixture and refuses wrong-key and tampered sources', async () => {
  const f = installHook(packageHook);
  const packageRoot = resolve(here, '..');
  const officialManifest = join(packageRoot, '.claude', 'helpers', 'helpers.manifest.json');
  const originalManifest = fs.readFileSync(officialManifest);
  const disposable = join(f.root, 'disposable-package');
  const sourceDir = join(disposable, '.claude', 'helpers');
  fs.mkdirSync(sourceDir, { recursive: true });
  fs.mkdirSync(join(disposable, 'scripts'));
  fs.copyFileSync(join(packageRoot, 'scripts', 'sign-helpers.mjs'), join(disposable, 'scripts', 'sign-helpers.mjs'));
  fs.copyFileSync(join(packageRoot, 'package.json'), join(disposable, 'package.json'));
  for (const name of ['auto-memory-hook.mjs', 'hook-handler.cjs', 'intelligence.cjs',
    'statusline.cjs', 'router.cjs', 'learning-loop.cjs']) {
    fs.copyFileSync(join(packageRoot, '.claude', 'helpers', name), join(sourceDir, name));
  }
  const { publicKey, privateKey } = generateKeyPairSync('ed25519');
  const key = join(f.root, 'fixture-only.key');
  fs.writeFileSync(key, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 });
  const env: NodeJS.ProcessEnv = { ...process.env, HOME: f.home, USERPROFILE: f.home,
    RUFLO_HELPERS_SIGNING_KEY: key };
  // The local-key fixture must never select the signer's GCP branch.
  delete env.RUFLO_HELPERS_SIGNING_SECRET;
  delete env.RUFLO_HELPERS_SIGNING_PROJECT;
  try {
    const signed = spawnSync(process.execPath, [join(disposable, 'scripts', 'sign-helpers.mjs')], {
      cwd: disposable, env, encoding: 'utf8', timeout: 15_000,
    });
    expect(signed.error).toBeUndefined();
    expect(signed.status).toBe(0);
    const pubkeyPemOverride = publicKey.export({ type: 'spki', format: 'pem' }).toString();
    const stale = (project: string) => {
      const helpers = join(project, '.claude', 'helpers');
      fs.mkdirSync(helpers, { recursive: true });
      fs.writeFileSync(join(helpers, 'hook-handler.cjs'), '// stale fixture');
      fs.writeFileSync(join(helpers, '.helpers-version'), '0.0.1');
      return helpers;
    };
    const helpers = stale(f.project);
    const options = { sourceDirOverride: sourceDir, pubkeyPemOverride, alsoRefreshGlobal: false };
    const refreshed = await autoRefreshHelpersIfStale(f.project, options);
    expect(refreshed.refreshed).toBe(true);
    const refreshedHook = join(helpers, 'auto-memory-hook.mjs');
    expect(fs.readFileSync(refreshedHook)).toEqual(fs.readFileSync(packageHook));
    const wrongProject = join(f.root, 'wrong-key-project');
    stale(wrongProject);
    const beforeWrong = snapshot(wrongProject);
    const wrongKey = generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }).toString();
    const wrong = await autoRefreshHelpersIfStale(wrongProject, { ...options, pubkeyPemOverride: wrongKey });
    expect(wrong.refreshed).toBe(false);
    expect(wrong.blocked).toMatch(/signature invalid|missing/);
    expect(snapshot(wrongProject)).toEqual(beforeWrong);

    const tamperedProject = join(f.root, 'tampered-source-project');
    stale(tamperedProject);
    fs.appendFileSync(join(sourceDir, 'auto-memory-hook.mjs'), '\n// tampered fixture\n');
    const beforeTampered = snapshot(tamperedProject);
    const tampered = await autoRefreshHelpersIfStale(tamperedProject, options);
    expect(tampered.refreshed).toBe(false);
    expect(tampered.blocked).toMatch(/integrity check failed/);
    expect(snapshot(tamperedProject)).toEqual(beforeTampered);

    // Check both rejection paths even when old code lacks read-matches.
    const records = [entry('first'), entry('second')];
    writeStore(f, JSON.stringify(records));
    const beforeRead = snapshot(f.project);
    const result = run({ ...f, hook: refreshedHook }, ['read-matches', 'auto-memory', 'same']);
    expect(result.status).toBe(0);
    expect(result.out).toMatchObject({ matchCount: 2, entries: records });
    expect(snapshot(f.project)).toEqual(beforeRead);
  } finally {
    expect(fs.readFileSync(officialManifest)).toEqual(originalManifest);
  }
});

it('#4852 keeps the repository and package helper byte-identical', () => {
  expect(fs.readFileSync(repositoryHook)).toEqual(fs.readFileSync(packageHook));
});
