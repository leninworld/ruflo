#!/usr/bin/env node
// ruflo #2935 prerequisite diagnostic — Darwin memory signal probe.
// DIAGNOSTIC ONLY. Reads system memory statistics; runs no ruflo code, installs
// nothing, writes only to the evidence directory given as argv[2].
// Deterministic pass criteria are structural (parse + invariants). The live
// allow/deny verdicts are recorded for information and never decide pass/fail.

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { join } from 'node:path';

const outDir = process.argv[2];
if (!outDir) { console.error('usage: probe.mjs <evidence-dir>'); process.exit(2); }
mkdirSync(outDir, { recursive: true });

const SAMPLES = 3;
const SAMPLE_GAP_MS = 1000;
const THRESHOLD_PERCENT = 5; // current Darwin default in worker-daemon.ts (unchanged by the fix)

function run(file, args) {
  try {
    return { ok: true, stdout: execFileSync(file, args, { encoding: 'utf8', timeout: 5000, maxBuffer: 64 * 1024 }) };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error).slice(0, 500) };
  }
}

// Same contract the implementation will use: page size from the header, the
// four counters as safe non-negative integers, CRLF tolerated.
function parseVmStat(text) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const header = /page size of (\d+) bytes/.exec(lines[0] ?? '');
  if (!header) return { ok: false, reason: 'missing page-size header' };
  const pageSize = Number(header[1]);
  const want = { 'Pages free': 'free', 'Pages inactive': 'inactive', 'Pages speculative': 'speculative', 'Pages purgeable': 'purgeable' };
  const counters = {};
  for (const line of lines.slice(1)) {
    const m = /^([^:]+):\s+(\d+)\.?\s*$/.exec(line);
    if (m && want[m[1].trim()]) counters[want[m[1].trim()]] = Number(m[2]);
  }
  for (const key of ['free', 'inactive', 'speculative']) {
    if (!Number.isSafeInteger(counters[key]) || counters[key] < 0) return { ok: false, reason: `bad counter ${key}` };
  }
  if (!Number.isSafeInteger(pageSize) || pageSize <= 0) return { ok: false, reason: 'bad page size' };
  return { ok: true, pageSize, ...counters };
}

const env = {
  node: process.version,
  arch: process.arch,
  platform: process.platform,
  uname_m: run('/usr/bin/uname', ['-m']),
  sw_vers: run('/usr/bin/sw_vers', []),
  proc_translated: run('/usr/sbin/sysctl', ['-n', 'sysctl.proc_translated']),
  hw_pagesize: run('/usr/sbin/sysctl', ['-n', 'hw.pagesize']),
  hw_memsize: run('/usr/sbin/sysctl', ['-n', 'hw.memsize']),
};

const samples = [];
for (let i = 0; i < SAMPLES; i++) {
  const freemem = os.freemem();
  const totalmem = os.totalmem();
  const availableMemory = typeof process.availableMemory === 'function' ? process.availableMemory() : null;
  const memorystatusLevel = run('/usr/sbin/sysctl', ['-n', 'kern.memorystatus_level']);
  const raw = run('/usr/bin/vm_stat', []);
  const parsed = raw.ok ? parseVmStat(raw.stdout) : { ok: false, reason: 'vm_stat failed' };
  const s = { index: i, at: new Date().toISOString(), freemem, totalmem, availableMemory, memorystatusLevel, vm_stat_raw: raw, parsed };
  if (parsed.ok) {
    const freeCountBytes = (parsed.free + parsed.speculative) * parsed.pageSize; // == host free_count
    const available = Math.min(freeCountBytes + parsed.inactive * parsed.pageSize, totalmem);
    s.recompute = {
      formula: '(free + speculative + inactive) * pageSize, clamped to totalmem; purgeable excluded',
      free_count_bytes: freeCountBytes,
      available_bytes: available,
      available_percent: (available / totalmem) * 100,
      freemem_percent: (freemem / totalmem) * 100,
      // Independent cross-check: libuv's freemem = free_count * _SC_PAGESIZE, so
      // freemem + inactive * pageSize should approximate the parsed formula.
      independent_available_bytes: Math.min(freemem + parsed.inactive * parsed.pageSize, totalmem),
      freemem_vs_free_count_delta_bytes: freemem - freeCountBytes,
      purgeable_bytes_diagnostic: Number.isSafeInteger(parsed.purgeable) ? parsed.purgeable * parsed.pageSize : null,
      verdict_old_metric_at_5pct: (freemem / totalmem) * 100 < THRESHOLD_PERCENT ? 'deny' : 'allow',
      verdict_new_metric_at_5pct: (available / totalmem) * 100 < THRESHOLD_PERCENT ? 'deny' : 'allow',
    };
  }
  samples.push(s);
  if (i < SAMPLES - 1) await new Promise((r) => setTimeout(r, SAMPLE_GAP_MS));
}

// Deterministic structural checks only.
const checks = [];
const check = (name, pass, detail) => checks.push({ name, pass: Boolean(pass), detail });
check('platform is darwin', process.platform === 'darwin', process.platform);
check('every sample parsed', samples.every((s) => s.parsed.ok), samples.map((s) => s.parsed.reason ?? 'ok'));
const hwPage = env.hw_pagesize.ok ? Number(env.hw_pagesize.stdout.trim()) : NaN;
check('header page size equals hw.pagesize', samples.every((s) => s.parsed.ok && s.parsed.pageSize === hwPage), { hwPage, header: samples.map((s) => s.parsed.pageSize) });
check('available within (0, totalmem]', samples.every((s) => s.recompute && s.recompute.available_bytes > 0 && s.recompute.available_bytes <= s.totalmem));
check('available >= free_count bytes', samples.every((s) => s.recompute && s.recompute.available_bytes >= Math.min(s.recompute.free_count_bytes, s.totalmem)));

const result = { schema: 'ruflo2935-darwin-probe/1', env, samples, checks, pass: checks.every((c) => c.pass) };
writeFileSync(join(outDir, `probe-${process.version}.json`), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ pass: result.pass, checks }, null, 2));
process.exit(result.pass ? 0 : 1);
