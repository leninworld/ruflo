#!/bin/bash
# Separate bounded egress phase. Resolution only; no lifecycle execution here.
set -euo pipefail
archive="$(node -p "require('/work/evidence/cli-pack.json')[0].filename")"
root_archive="$(node -p "require('/work/evidence/root-pack.json')[0].filename")"
ruflo_archive="$(node -p "require('/work/evidence/ruflo-pack.json')[0].filename")"
for kind in native omit; do
 mkdir -p "/work/warm-$kind" "/work/consumer-$kind"
 printf '{"name":"ruflo4876-%s","version":"0.0.0","private":true}\n' "$kind" > "/work/warm-$kind/package.json"
 cd "/work/warm-$kind"
 flags=()
 if [ "$kind" = omit ]; then flags+=(--omit=optional); fi
 npm install "/work/pack/$archive" "/work/pack/$root_archive" "/work/pack/$ruflo_archive" "${flags[@]}" --package-lock-only --ignore-scripts --no-audit --no-fund
 # The resolved lock and package are reused only by the new fresh actual install.
 cp package.json package-lock.json "/work/consumer-$kind/"
 node /inputs/consumer-lock-proof.mjs "$PWD/package-lock.json" > "/work/evidence/consumer-lock-$kind.json"
done
