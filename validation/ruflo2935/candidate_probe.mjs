#!/usr/bin/env node
// ruflo #2935 L4 — exercise the BUILT candidate module on a real runner.
// usage: candidate_probe.mjs <path-to-dist/src/services/available-memory.js> <evidence-dir>
// Darwin: the reader must use vm_stat and agree with an independent
// recomputation (separate parser below) within 5% of totalmem (the two reads
// are taken a moment apart). Other platforms: the reader must report
// os.freemem and never spawn vm_stat. Exit 0 only if every check passes.

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [modulePath, outDir] = process.argv.slice(2);
if (!modulePath || !outDir) { console.error('usage: candidate_probe.mjs <module> <evidence-dir>'); process.exit(2); }
mkdirSync(outDir, { recursive: true });

const mod = await import(pathToFileURL(resolve(modulePath)).href);
const reader = mod.createAvailableMemoryReader();
const reading = await reader.read();
const checks = [];
const check = (name, pass, detail) => checks.push({ name, pass: Boolean(pass), detail });
const result = { schema: 'ruflo2935-candidate-probe/1', node: process.version, platform: process.platform, arch: process.arch, reading };

check('exports are present', typeof mod.parseVmStat === 'function' && typeof mod.createAvailableMemoryReader === 'function');
check('percent within [0, 100]', reading.percent >= 0 && reading.percent <= 100, reading.percent);
check('no fallback reason', reading.fallbackReason === undefined, reading.fallbackReason);
check('no diagnostic', reading.diagnostic === undefined, reading.diagnostic);

if (process.platform === 'darwin') {
  const text = execFileSync('/usr/bin/vm_stat', [], { encoding: 'utf8', timeout: 5000 });
  const page = Number(/page size of (\d+) bytes/.exec(text)?.[1]);
  const count = (label) => Number(new RegExp(`^${label}:\\s+(\\d+)\\.`, 'm').exec(text)?.[1]);
  const free = count('Pages free'); const spec = count('Pages speculative'); const inactive = count('Pages inactive');
  const total = os.totalmem();
  const independent = Math.min((free + spec + inactive) * page, total);
  result.independent = { text, page, free, spec, inactive, total, independent };
  check('source is vm_stat', reading.source === 'vm_stat', reading.source);
  check('total matches os.totalmem()', reading.totalBytes === total, { reading: reading.totalBytes, total });
  check('agrees with independent recomputation within 5% of totalmem',
    Number.isFinite(independent) && Math.abs(reading.availableBytes - independent) / total <= 0.05,
    { reader: reading.availableBytes, independent });
  check('available >= os.freemem() - 5% of totalmem (reclaimable memory is counted)',
    reading.availableBytes >= os.freemem() - 0.05 * total, { reader: reading.availableBytes, freemem: os.freemem() });
} else {
  check('source is os.freemem', reading.source === 'os.freemem', reading.source);
}

result.checks = checks;
result.pass = checks.every((c) => c.pass);
writeFileSync(join(outDir, `candidate-probe-${process.platform}-${process.version}.json`), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ pass: result.pass, checks }, null, 2));
process.exit(result.pass ? 0 : 1);
