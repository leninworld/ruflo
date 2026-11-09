"""Exact D0/candidate assertions; run only inside the approved Docker packet."""
import json
from pathlib import Path
import sys
from compare_reports import inspect_report

file, variant = sys.argv[1:]
assert variant in ['baseline', 'candidate']
report = json.loads(Path(file).read_text())
counts, messages, collection, files, failed = inspect_report(report)
assert len(files) == 1 and not collection and not failed
assert report['numTotalTests'] == 26
assert all(sum(values.values()) == 1 for values in counts.values())
expected_failures = {
    'exposes the bound on the real registered tool',
    'distinguishes the runtime UTF-16 bound from standard client code points',
    'reconciles the reachable empty-value diagnostic without attempting storage',
    *('refuses '+label+' before authorizer, native handler or storage' for label in [
        '10001 ASCII units', '5001 supplementary code points', '5001 combining sequences']),
} if variant == 'baseline' else set()
actual_failures = {identity[2] for identity, values in counts.items() if values.get('failed')}
assert actual_failures == expected_failures, ('UNEXPECTED_FOCUSED_FAILURES', sorted(actual_failures))
assert all(set(values) <= {'passed', 'failed'} for values in counts.values())
assert report['numPassedTests'] == 26-len(expected_failures)
assert report['numFailedTests'] == len(expected_failures)
assert report['success'] == (variant == 'candidate')
print(json.dumps({'state': 'PASS', 'variant': variant, 'cases': 26,
    'expected_pre_fix_failures': sorted(expected_failures), 'preservation_cases_passed': 20}))
