#!/bin/bash
# Source-only fallback regression lane beside, after, the untouched packed proof.
set -euo pipefail
test "$VARIANT" = candidate
mkdir /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation
cp -R /work/source/v3/@claude-flow/cli/src /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation/src
mkdir /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation/__tests__
cp /work/source/v3/@claude-flow/cli/__tests__/memory-search-sqljs-fallback-4876.test.ts /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation/__tests__/
cp /work/source/v3/@claude-flow/cli/package.json /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation/package.json
node /inputs/fallback-resolution-check.mjs /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation
# The runner is sealed tooling, not a provider dependency injected into source.
# Test runner resolution alias for vitest only; no model/backend replacements.
test -f /inputs/fallback-runner-config.mjs
cd /work/consumer-omit/node_modules/@claude-flow/cli/.e2-source-validation
RUFLO_TEST_4876_SQLJS_FALLBACK=1 /work/source/v3/node_modules/.bin/vitest run \
 --config /inputs/fallback-runner-config.mjs \
 --reporter=default --reporter=json --outputFile.json=/work/evidence/fallback-required.json
node - <<'NODE'
const r=require('/work/evidence/fallback-required.json');
if(r.success!==true||r.numTotalTests!==3||r.numPassedTests!==3||r.numPendingTests!==0||r.numFailedTests!==0)throw Error('all3fallbackcasesmustexecuteandpass');
NODE
