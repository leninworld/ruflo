#!/bin/bash
# Fresh actual consumer, offline; postinstall permitted only in reviewed container.
set -euo pipefail
kind="$1"
case "$kind" in native|omit);; *) exit 2;; esac
cd "/work/consumer-$kind"
test ! -e node_modules
flags=()
if [ "$kind" = omit ]; then flags+=(--omit=optional); fi
sha256sum package-lock.json > "/work/evidence/consumer-$kind-before.txt"
npm ci --offline "${flags[@]}" --no-audit --no-fund
sha256sum -c "/work/evidence/consumer-$kind-before.txt"
set +e
npm ls --all > "/work/evidence/npm-ls-$kind.txt" 2> "/work/evidence/npm-ls-$kind.stderr"
code=$?
set -e
printf '%s\n' "$code" > "/work/evidence/npm-ls-$kind.exit"
node /inputs/consumer-lock-proof.mjs package-lock.json > "/work/evidence/fast-uri-$kind.json"
node node_modules/@claude-flow/cli/bin/cli.js --version > "/work/evidence/cli-version-$kind.txt"
node node_modules/claude-flow/bin/cli.js --version > "/work/evidence/root-version-$kind.txt"
node node_modules/ruflo/bin/ruflo.js --version > "/work/evidence/ruflo-version-$kind.txt"
node node_modules/@claude-flow/cli/bin/cli.js policy status > "/work/evidence/cli-policy-$kind.txt"
node node_modules/claude-flow/bin/cli.js policy status > "/work/evidence/root-policy-$kind.txt"
node /inputs/cli-child-smoke.mjs "$kind" "$PWD/node_modules/@claude-flow/cli"
node /inputs/packed-smoke.mjs "$kind" "$PWD/node_modules/@claude-flow/cli" > "/work/evidence/packed-$kind.stdout"
if [ "$kind" = native ]; then
 node /inputs/ann-smoke.mjs hnsw "$PWD/node_modules/@claude-flow/cli" > /work/evidence/ann-hnsw.stdout
 node /inputs/ann-smoke.mjs rabitq "$PWD/node_modules/@claude-flow/cli" > /work/evidence/ann-rabitq.stdout
fi
# Init/helper refresh behavior is a differential outcome, not silently ignored.
mkdir project
set +e
(cd project && node ../node_modules/@claude-flow/cli/bin/cli.js init --codex --force --no-plugin-install --no-signup --no-global) > "/work/evidence/init-$kind.stdout" 2> "/work/evidence/init-$kind.stderr"
code=$?
set -e
printf '%s\n' "$code" > "/work/evidence/init-$kind.exit"
node /inputs/init-helper-outcome.mjs "$PWD/project" > "/work/evidence/init-helpers-$kind.json"
if [ "$kind" = omit ] && [ "$VARIANT" = candidate ]; then bash /inputs/fallback-source.sh; fi
