# Ruflo4876 E2 whole execution proposal

This packet is statically authored, disabled and unapproved. It preserves the
reviewed E1 packet and uses the parent's exact22-file I5 snapshot plus the
current3fe9d70 source pack. No runtime, dependency install, build, test, Docker,
network or Claude call occurred during E2 authoring.

## Scope and evidence

Seven profiles (root20, v3test22, v3build20, cli22, mcp22,
affected-smokes22, pack22) each run pristine baseline and exact candidate.
Root ratchet commands, committed baselines and exclusions stay unchanged.
Newly fixed files are allowed and reported. Full report comparisons preserve
existing passes, skips, assertion multiplicities, failing files and collection
errors; changed/new test-file expectations derive from directories actually
collected by each baseline profile. Candidate-only source tests are required
where that directory was collected; the three opt-in fallback cases run
separately and must all pass.

The immutable Node20/22 amd64 images and pinned pnpm8.15.0 archive are retained
inputs. Matching root/pnpm lock hashes and lifecycle metadata are provenance,
not new payload or runtime proof. Full canonical byte-seal recomputation runs
in host.py and inside.py before any packet helper import. Every packet file
except approval.json participates; manifest.sealed_packet_sha256 is normalized
to null. Files are sorted [relative path, byte size, sha256], canonical JSON,
then sha256. The installed consumer index has an additional exact hash check.
Bytecode writes are disabled. origin verifies the same seal and the external
workflow's exact bytes.

G0 runs offline before preparation: source import/extraction, actual manifest
signature/hash/wrong-key negatives, report fixtures, loopback-only network guard,
compiler/python/make/header presence, a tiny real native addon compilation/load,
and all root hasInstallScript, pnpm requiresBuild and local/workspace lifecycle
obligations against the pinned inventory. Actual lock os/cpu declarations and
neverBuiltDependencies are honored. Unknown applicable metadata stops before
network or scripts. Post-execution event receipts remain an independent check.

## One shared attempt and bounded resources

The proposed orphan branch contains only the external workflow and E2-packet.
No branch, commit or push has been performed or authorized. The workflow only
triggers on a push to that exact branch; no workflow_dispatch/default-branch
write is proposed. Every job rejects run_attempt other than1. Actions are pinned
by SHA from retained evidence; permissions are contents:read, credentials are
not persisted, and no token is passed to step environments or containers.

origin records actual ubuntu24 image version, UID/GID, CPU affinity, available
memory and free root-filesystem disk, verifies the seal and emits one UTC epoch.
Four effective CPUs require a public fork; a private fork/larger runner is not
silently substituted. Fourteen matrix lanes use independent ubuntu-24.04 VMs,
fail-fast:false and max-parallel:14. Queue/checkout delay consumes the same epoch.
Host and container commands, phases and owned shutdown end by origin+3420s;
late starts record NOT_STARTED_DEADLINE and never reset. Evidence-only uploads
have120s backstops. Aggregate runs outside lane clocks, always preserving partial
results. A failed lane remains terminal; there are no re-runs or clock resets.

Memory proposals retain the original14GiB MemAvailable floor, 12GiB container
memory=swap limit and focused current cgroup<=10GiB gate, with2GiB measured host
headroom. Focused tests do not prove full-suite memory sufficiency: a full-suite
OOM fails the attempt. The floor is measured at origin and every lane and is
never silently lowered. Containers use the actual runner UID:GID, synthetic
HOME/XDG/cache/tmp, no credentials, cap-drop ALL and no-new-privileges.

Disk proposal is12GiB owned delta plus16GiB initial free floor, derived from
retained4834 maximum10,167,320,576B plus explicit margins in resource-proposal.
Those old lanes never completed the two new fresh native consumer installs.
Their footprints and source-compile peaks remain unknown; the proposal is a
ceiling, not sufficient-capacity proof. Monitor uses admission-free minus
current-free statvfs delta on each exclusive VM, not a recursive node_modules
scan. No /mnt, external disk, shared cache, image/container/cache deletion or
new service/gateway is proposed.

Acquisition uses existing Docker bridge only during preparation and consumer
metadata/tarball acquisition, with proposed20GiB cumulative container RX and2GiB
host image RX sampled caps plus deadlines. Tests and actual scriptful fresh
consumers run with Docker network none and a Node loopback-only guard. Sampled
stop-on-breach is not a kernel bandwidth firewall; Docker daemon transfers can
outlive a terminated pull client. Actual cold payload bytes and time remain
unknown and are recorded within the same attempt, never priced as zero.

## Faithful package and installed runtime proof

Build/stage current shared/security/codex/MCP/federation, CLI/swarm and TUI using
working esbuild plus its linux-x64 optional binary from the full frozen root
ignore-scripts install, checked by an offline transform probe. Source versions/manifests/locks and
signed helpers remain unchanged. Reproduce the root README release copy,
metaharness and deterministic catalog from the actual base Git SHA; disclose
candidate patch identity separately because candidate HEAD remains base.
Compare staged/extracted bundled manifests against current source's
createBundledRuntimeManifest and dist bytes against current builds. Pack all
CLI/root/ruflo archives with ignore-scripts. Run actual official helper verifier
on build/extracted bytes and require the identical baseline/candidate known
3.71.5-versus3.72.2 mismatch; never call that mismatch a pass. Separately prove
canonical signature, all six hashes, emitted public key and base-byte equality.

In the same attempt, after archives exist, resolve two package-lock-only,
ignore-scripts consumer graphs. Before tarball acquisition or scriptful install,
admit registry versions/SRI, local archive scripts, every bundled manifest's
scripts and correctly host-relative nested bundle paths. Unknown live versions
or metadata STOP before acquisition/scripts; this can end F without proof.
Then use npm cache add for each unique admitted registry artifact, suppressing
scripts, without creating either warm node_modules tree; freeze locks and perform two
fresh offline installs (native and omit-optional). npm_config_nodedir=/usr/local
uses image headers for source compilation of better-sqlite3/hnswlib-node.
Optional sharp may drop; dropped optionals are recorded and never counted as
native success. No model download is required. Capture npm ls stdout/stderr/exit
and compare baseline/candidate instead of treating inherited complaints as a
new product failure.

Both consumers run the actual installed CLI bin with mcp start HTTP flags,
a synthetic random64-hex RUFLO_MCP_HTTP_TOKEN and Bearer on every request,
initialize MCP and call memory_search_unified over loopback. Record lastHTTPstatus, stderr/stdouttails and guard entries added since child
startup. A401, guard block or spawn error is REAL_CLI_BLOCKED_HARNESS_OR_ENV;
unknown refusal stays unattributed. Only a failed startup/memory probe with
helper-manifest mismatch diagnostic evidence PARKS the issue; aggregate
preserves this distinction even for an incomplete lane.
The deterministic408 proof is separately in-process MCPServerManager
requestTimeoutMs, not a claim about CLI-flag timeout timing. Native real SQLite
with a declared synthetic vector provider and real sql.js/hash profiles retain
score/order differential checks. Offline hash/BM25/cancellation/full-chain tests
are mandatory. ONNX is optional with no latency claim.

HNSW uses two namespaces/two rows each, unified limit1, actual native calls and
filled candidate windows. RaBitQ observes the same-parent dynamic-import module
instance through a sentinel and real WASM search calls. Missing/unused bindings
fail. The optional fallback source test runs only after packed proofs, in a new
subtree under installedCLI, preserving bundled/hoisted consumer ancestry;
the validation subtree is explicitly inlined by Vitest server.deps to transform
its .ts imports behind .js specifiers; required bare runtime imports resolve
there, optional missing model/capability
imports are recorded without creating model prerequisites. The Vitest runner
comes from reviewed V3 tooling only.

## Explicitly not run or claimed

Full all-plugins/external npx fleet, full host matrix/remote Codex marketplace,
Claude vendor binary, CodeQL, advisory/security/published-alpha/global simulated
jobs, macOS/Windows, stable-tag/sign/publish/auth/dist-tags are NOT_RUN. Local
source-dependent registry/guard checks and affected product suites remain
required. A subset is never labelled as a full external workflow.

E2 provenance rewrites private host prefixes to retained IDs while preserving
original E1/caches and exact public upstream source, licenses and author/contact
emails. No private history or host cache is added. A whole public-content review
and exact execution review remain required. approval.json is intentionally absent;
execution_enabled remains false. The draft byte seal identifies bytes only and
is neither a passing validation result nor execution authorization.

## Required final controller delta

Measure-build uses the same600s cap as test-phase builds; the encompassing
measurement phase allows900s for the build and focused tests, still under the
one shared deadline. Consumer-test phase allows1200s for its two600s commands,
also clipped to origin+3375 inside and origin+3420 overall. Neither memory nor
disk caps increased. Two fresh consumer footprints and critical-path time remain
unknown. The pinned root tree has no vite/vitest config variant to shadow.

The unchanged base @claude-flow/browser workspace postinstall runs during V3
prepare. If its agent-browser--version probe fails, its literal fallback is
npm install -g agent-browser@latest; EACCES is expected under the nonroot
container UID and is caught by that base script. This is an explicitly disclosed
base behavior, not an optional warning or a product regression. The exact script
is allowlisted and bounded by acquisition caps; actual behavior remains unrun.

The retained old graph has173unique registryURLs pervariant. E3 uses a pool of
at most4 npm cache-add processes, a shared300s cache budget and60s perartifact,
within the existing preparation/phase/origin limits. Current graph size and cold
startup/transfer time remain unknown; no300s fit or price closure is claimed.
The pool creates no warm consumer install trees and does not raise resource caps.
