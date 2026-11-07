"""Inspected Vitest multiset report inspector; no4834 test-specific comparator."""
import collections
STATUSES = {'passed', 'failed', 'pending', 'skipped', 'todo', 'disabled'}
SKIPS = STATUSES - {'passed', 'failed'}


def relative_file(name, source_root):
    assert isinstance(name, str) and name, 'MISSING_TEST_FILE_ID'
    root = str(source_root).rstrip('/') + '/'
    if name.startswith(root):
        name = name[len(root):]
    assert not name.startswith('/') and '..' not in name.split('/'), 'UNRESOLVED_TEST_FILE_ID'
    return name.removeprefix('./')


def inspect_report(report, source_root):
    assert isinstance(report, dict), 'REPORT_NOT_AN_OBJECT'
    assert isinstance(report.get('success'), bool), 'MISSING_ROOT_SUCCESS'
    for key in ['numTotalTests', 'numPassedTests', 'numFailedTests', 'numPendingTests',
                'numTotalTestSuites', 'numFailedTestSuites']:
        assert isinstance(report.get(key), int) and report[key] >= 0, 'MISSING_OR_INVALID_' + key
    files = report.get('testResults')
    assert isinstance(files, list) and files, 'EMPTY_TEST_FILES'
    counts = collections.defaultdict(collections.Counter)
    collection_errors = {}
    file_statuses = {}
    for result in files:
        assert isinstance(result, dict), 'BAD_FILE_REPORT'
        name = relative_file(result.get('name'), source_root)
        assert name not in file_statuses, 'DUPLICATE_FILE_REPORT'
        file_statuses[name] = result.get('status')
        assertions = result.get('assertionResults')
        assert isinstance(assertions, list), 'MISSING_ASSERTION_LIST'
        if result.get('status') == 'failed' and not any(c.get('status') == 'failed' for c in assertions):
            message = result.get('message')
            collection_errors[name] = message.replace(str(source_root), '<source>') if isinstance(message, str) and message.strip() else 'FILE_FAILED_WITHOUT_FAILED_ASSERTION'
        for case in assertions:
            ancestors, title, status = case.get('ancestorTitles'), case.get('title'), case.get('status')
            assert isinstance(ancestors, list) and all(isinstance(a, str) for a in ancestors), 'BAD_CASE_ANCESTORS'
            assert isinstance(title, str) and title, 'MISSING_CASE_TITLE'
            assert status in STATUSES, 'UNKNOWN_CASE_STATUS'
            identity = (name, tuple(ancestors), title)
            counts[identity][status] += 1
    total = sum(sum(c.values()) for c in counts.values())
    assert total > 0, 'NO_ASSERTIONS_COLLECTED'
    assert report['numTotalTests'] == total, 'SUMMARY_ASSERTION_COUNT_MISMATCH'
    observed = collections.Counter()
    for statuses in counts.values():
        observed.update(statuses)
    assert report['numPassedTests'] == observed['passed'], 'PASS_SUMMARY_MISMATCH'
    assert report['numFailedTests'] == observed['failed'], 'FAIL_SUMMARY_MISMATCH'
    assert report['numPendingTests'] + report.get('numTodoTests', 0) == sum(observed[s] for s in SKIPS), 'SKIP_SUMMARY_MISMATCH'
    if report['success']:
        assert observed['failed'] == 0 and not collection_errors and report['numFailedTestSuites'] == 0, 'FALSE_SUCCESS'
    report_level_failure = not report['success'] and not observed['failed'] and not collection_errors
    return counts, collection_errors, file_statuses, report_level_failure


