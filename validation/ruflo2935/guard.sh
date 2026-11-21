# Verbatim body of the "Run tests" step of .github/workflows/v3-ci.yml (blob bb3bf65d…, lines 414-433),
# 10-space YAML indent removed. Run as: bash --noprofile --norc -eo pipefail guard.sh  (GitHub default bash shell)
# with RUNNER_TEMP set to a fresh evidence directory and cwd = v3/.
REPORT="$RUNNER_TEMP/vitest-report.json"
rm -f "$REPORT"
set +e
# The bin shim execs node, so a segfault surfaces as 139. Through
# `pnpm exec` a signalled child is reported as exit 1 instead.
./node_modules/.bin/vitest run --reporter=default --reporter=json --outputFile.json="$REPORT" 2>&1 | tee "$RUNNER_TEMP/vitest-output.log"
EXIT=${PIPESTATUS[0]}
set -e
if [ "$EXIT" -eq 139 ]; then
  # Count error lines explicitly: a scanner failure leaves ERRORS empty
  # and fails closed instead of reading as "no errors".
  ERRORS="$(sed -E 's/\x1b\[[0-9;]*m//g' "$RUNNER_TEMP/vitest-output.log" | grep -cE '^ *Errors +[0-9]+ errors?|Unhandled Errors|error during close' || true)"
  if [ -s "$REPORT" ] && [ -s "$RUNNER_TEMP/vitest-output.log" ] && [ "$ERRORS" = "0" ] \
    && node -e 'const r=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")); process.exit(r.success===true && r.numTotalTestSuites>0 && r.numFailedTestSuites===0 && r.numFailedTests===0 ? 0 : 1)' "$REPORT"; then
    echo "::warning::vitest exited with SIGSEGV (139) after it reported a passing run with no unhandled errors — tolerated as a shutdown crash"
    exit 0
  fi
  echo "::error::vitest exited with SIGSEGV (139) without a passing report (report present: $([ -s "$REPORT" ] && echo yes || echo no); error lines: ${ERRORS:-unreadable}); the suite did not finish, or it reported failures or unhandled errors"
fi
exit "$EXIT"
