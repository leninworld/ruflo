#!/bin/bash
# Container only, invoked after reviewed frozen install/build; no signing hooks.
set -euo pipefail
cd /work/source
mkdir -p /work/pack /work/extracted
sha256sum package.json v3/@claude-flow/cli/package.json ruflo/package.json \
 package-lock.json v3/pnpm-lock.yaml > /work/evidence/protected-before.txt
node scripts/stage-internal-runtime-bundles.mjs --target v3/@claude-flow/cli
node scripts/stage-internal-runtime-bundles.mjs --target . --no-build
# Explicitly stage the TUI: prepare-publish cannot be called because it signs.
node plugins/ruflo-console/scripts/build-tui.mjs \
 --outfile /work/source/v3/@claude-flow/cli/plugins/ruflo-console/dist/tui.mjs
epoch="$(git show -s --format=%ct HEAD)"
SOURCE_DATE_EPOCH="$epoch" node v3/@claude-flow/cli/scripts/generate-catalog-manifest.mjs
sha256sum v3/@claude-flow/cli/catalog-manifest.json > /work/evidence/catalog-first.txt
SOURCE_DATE_EPOCH="$epoch" node v3/@claude-flow/cli/scripts/generate-catalog-manifest.mjs
sha256sum -c /work/evidence/catalog-first.txt
cp README.md v3/@claude-flow/cli/README.md
mkdir -p v3/@claude-flow/cli/plugins
# New isolated source tree only; no shared/external data.
test ! -L v3/@claude-flow/cli/plugins/ruflo-metaharness
rm -rf v3/@claude-flow/cli/plugins/ruflo-metaharness
cp -R plugins/ruflo-metaharness v3/@claude-flow/cli/plugins/ruflo-metaharness
cp README.md ruflo/README.md
node --input-type=module - <<'NODE'
import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
import {createBundledRuntimeManifest,INTERNAL_RUNTIME_PACKAGES} from './scripts/stage-internal-runtime-bundles.mjs';
for(const target of ['.','v3/@claude-flow/cli'])for(const p of INTERNAL_RUNTIME_PACKAGES){
 const source=JSON.parse(readFileSync(`v3/@claude-flow/${p.directory}/package.json`));
 const staged=JSON.parse(readFileSync(`${target}/node_modules/${p.name}/package.json`));
 assert.deepEqual(staged,createBundledRuntimeManifest(source));
}
NODE
for target in cli root ruflo; do
 case "$target" in cli) dir=./v3/@claude-flow/cli;; root) dir=.;; ruflo) dir=./ruflo;; esac
 npm pack --json --ignore-scripts --pack-destination /work/pack "$dir" > "/work/evidence/$target-pack.json"
 archive="$(node -p "require('/work/evidence/$target-pack.json')[0].filename")"
 python3 /inputs/archive.py "/work/pack/$archive" "/work/extracted/$target" > "/work/evidence/$target-members.json"
done
for target in cli root; do
 for bundle in security codex mcp plugin-agent-federation; do
  diff -qr "v3/@claude-flow/$bundle/dist" "/work/extracted/$target/package/node_modules/@claude-flow/$bundle/dist"
 done
done
diff -qr v3/@claude-flow/cli/dist /work/extracted/cli/package/dist
diff -qr v3/@claude-flow/cli/dist /work/extracted/root/package/v3/@claude-flow/cli/dist
node --input-type=module - <<'NODE'
import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
import {createBundledRuntimeManifest,INTERNAL_RUNTIME_PACKAGES} from './scripts/stage-internal-runtime-bundles.mjs';
for(const target of ['cli','root'])for(const p of INTERNAL_RUNTIME_PACKAGES){
 const source=JSON.parse(readFileSync(`v3/@claude-flow/${p.directory}/package.json`));
 const packed=JSON.parse(readFileSync(`/work/extracted/${target}/package/node_modules/${p.name}/package.json`));
 assert.deepEqual(packed,createBundledRuntimeManifest(source));
}
NODE
node /inputs/helper-proof.mjs build v3/@claude-flow/cli /work/base-helpers > /work/evidence/helpers-build.json
node /inputs/helper-proof.mjs extracted /work/extracted/cli/package /work/base-helpers > /work/evidence/helpers-extracted.json
for target in build extracted; do
 case "$target" in build) helpers=/work/source/v3/@claude-flow/cli/.claude/helpers;; extracted) helpers=/work/extracted/cli/package/.claude/helpers;; esac
 set +e
 node v3/@claude-flow/cli/scripts/verify-helpers.mjs "$helpers" > "/work/evidence/official-helper-$target.stdout" 2> "/work/evidence/official-helper-$target.stderr"
 result=$?
 set -e
 test "$result" -eq 1
 grep -F '[verify-helpers] manifest version 3.71.5 != package.json 3.72.2' "/work/evidence/official-helper-$target.stderr"
 printf '%s\n' "$result" > "/work/evidence/official-helper-$target.exit"
done
sha256sum package.json v3/@claude-flow/cli/package.json ruflo/package.json \
 package-lock.json v3/pnpm-lock.yaml > /work/evidence/protected-after.txt
diff -u /work/evidence/protected-before.txt /work/evidence/protected-after.txt
sha256sum /work/pack/*.tgz > /work/evidence/archive-sha256.txt
