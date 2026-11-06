"""Compare complete Vitest JSON reports; execution belongs in approved Docker.

Keep duplicate test titles as a multiset. A green exit, inherited collection
error, or report with missing assertions cannot stand in for the actual scope.
"""
import collections
import copy
import json
from pathlib import Path
import sys

NEW_FILE = 'v3/@claude-flow/cli/__tests__/openai-configured-provider-4834.test.ts'
PRIMARY = [
    '#4834 uses saved endpoint for probe through configure and the real HTTP server',
    '#4834 executes saved OpenAI config through the real HTTP server',
]
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


def compare(baseline, candidate, source_root='/work/source', seed_new=True,
            expected_new_cases=47, require_new_file=True):
    old, old_errors, old_files, old_report_failure = inspect_report(baseline, source_root)
    new, new_errors, new_files, new_report_failure = inspect_report(candidate, source_root)
    assert not new_report_failure or old_report_failure, 'NEW_REPORT_LEVEL_FAILURE'
    assert set(old_files) <= set(new_files), 'LOST_TEST_FILE'
    assert set(new_files) - set(old_files) <= {NEW_FILE}, 'UNEXPECTED_TEST_FILE_DELTA'
    assert set(new_errors) <= set(old_errors), 'NEW_COLLECTION_FAILURE'
    for name, message in new_errors.items():
        assert message == old_errors[name], 'CHANGED_COLLECTION_FAILURE'
    assert NEW_FILE not in old_errors and NEW_FILE not in new_errors, 'REGRESSION_FILE_COLLECTION_FAILURE'
    for identity, previous in old.items():
        if identity[0] == NEW_FILE:
            continue
        assert identity in new, 'LOST_CASE_ID'
        current = new[identity]
        assert sum(previous.values()) == sum(current.values()), 'LOST_OR_DUPLICATED_CASE'
        assert current['passed'] >= previous['passed'], 'LOST_BASELINE_PASS'
        assert current['failed'] <= previous['failed'], 'NEW_EXISTING_CASE_FAILURE'
        assert sum(current[s] for s in SKIPS) <= sum(previous[s] for s in SKIPS), 'NEW_EXISTING_CASE_SKIP'
    assert not [i for i in new if i not in old and i[0] != NEW_FILE], 'UNEXPECTED_CASE_ID'
    regression = {i: v for i, v in new.items() if i[0] == NEW_FILE}
    if require_new_file:
        assert regression, 'NEW_FILE_NOT_COLLECTED_BY_GATE'
    if regression:
        assert sum(sum(v.values()) for v in regression.values()) == expected_new_cases, 'UNEXPECTED_NEW_CASE_COUNT'
    assert all(set(v) == {'passed'} for v in regression.values()), 'NEW_REGRESSION_NOT_ALL_PASS'
    if seed_new and regression:
        before = {i: v for i, v in old.items() if i[0] == NEW_FILE}
        assert set(before) == set(regression), 'REGRESSION_ID_MULTISET_CHANGED'
        assert all(sum(v.values()) == sum(regression[i].values()) for i, v in before.items()), 'REGRESSION_DUPLICATE_COUNT_CHANGED'
        for title in PRIMARY:
            cases = [v for i, v in before.items() if i[2] == title]
            assert len(cases) == 1 and cases[0] == {'failed': 1}, 'BASELINE_MUST_REPRODUCE_' + title
    inherited = sum(v['failed'] for i, v in new.items() if i[0] != NEW_FILE)
    return {
        'state': 'COMPARISON_PASS' if not inherited and not new_errors and not new_report_failure else 'BASELINE_ALSO_FAILS',
        'existing_case_occurrences': sum(sum(v.values()) for i, v in old.items() if i[0] != NEW_FILE),
        'new_case_occurrences': sum(sum(v.values()) for v in regression.values()),
        'new_case_identities': [[i[0], list(i[1]), i[2], sum(v.values())] for i, v in sorted(regression.items())],
        'inherited_failed_assertions': inherited,
        'inherited_collection_failures': new_errors,
        'inherited_report_level_failure': new_report_failure,
        'all_tests_passed': inherited == 0 and not new_errors and not new_report_failure,
        'note': 'BASELINE_ALSO_FAILS retains real failures; it is never an all-tests-passed claim.',
    }


def tiny_fixture():
    def report(new_status='passed'):
        cases = [{'ancestorTitles': ['duplicate suite'], 'title': 'same title', 'status': 'passed'} for _ in range(2)]
        tests = [
            {'name': '/work/source/existing.test.ts', 'status': 'passed', 'assertionResults': cases},
            {'name': '/work/source/' + NEW_FILE,
             'status': 'failed' if new_status == 'failed' else 'passed',
             'assertionResults': [{'ancestorTitles': ['regression'], 'title': t, 'status': new_status} for t in PRIMARY]},
        ]
        return {'success': new_status != 'failed', 'numTotalTests': 4,
                'numPassedTests': 4 if new_status == 'passed' else 2,
                'numFailedTests': 2 if new_status == 'failed' else 0, 'numPendingTests': 0,
                'numTotalTestSuites': 2, 'numFailedTestSuites': int(new_status == 'failed'), 'testResults': tests}
    old, good = report('failed'), report()
    assert compare(old, good, expected_new_cases=2)['new_case_occurrences'] == 2
    rejected = []
    def summarize(value):
        cases = [c for f in value['testResults'] for c in f['assertionResults']]
        value['numTotalTests'] = len(cases)
        value['numPassedTests'] = sum(c['status'] == 'passed' for c in cases)
        value['numFailedTests'] = sum(c['status'] == 'failed' for c in cases)
        value['numPendingTests'] = sum(c['status'] in SKIPS for c in cases)
        value['numTotalTestSuites'] = len(value['testResults'])
        for f in value['testResults']:
            if f['assertionResults']:
                f['status'] = 'failed' if any(c['status'] == 'failed' for c in f['assertionResults']) else 'passed'
        value['numFailedTestSuites'] = sum(f['status'] == 'failed' for f in value['testResults'])
        value['success'] = value['numFailedTests'] == value['numFailedTestSuites'] == 0
    def negative(label, mutate, repair_summary=True):
        bad = copy.deepcopy(good)
        mutate(bad)
        if repair_summary:
            summarize(bad)
        try:
            compare(old, bad, expected_new_cases=2)
        except AssertionError:
            rejected.append(label)
        else:
            raise AssertionError('FIXTURE_ACCEPTED_' + label)
    negative('lost duplicate', lambda x: x['testResults'][0]['assertionResults'].pop())
    negative('new skip', lambda x: x['testResults'][0]['assertionResults'][0].update(status='pending'))
    negative('lost pass', lambda x: x['testResults'][0]['assertionResults'][0].update(status='failed'))
    negative('missing regression file', lambda x: x['testResults'].pop())
    negative('false root success', lambda x: x.update(success=False), False)
    negative('new collection failure', lambda x: x['testResults'].append({'name': '/work/source/new.test.ts', 'status': 'failed', 'assertionResults': [], 'message': 'missing module'}))
    negative('incomplete summary', lambda x: x.pop('numTotalTests'), False)
    # Inherited collection failures stay visible and compare only when identical.
    bad_old, bad_new = copy.deepcopy(old), copy.deepcopy(good)
    for x in [bad_old, bad_new]:
        x['success'] = False
        x['numTotalTestSuites'] += 1
        x['numFailedTestSuites'] += 1
        x['testResults'].append({'name': '/work/source/environment.test.ts', 'status': 'failed', 'assertionResults': [], 'message': 'offline: model unavailable'})
    assert compare(bad_old, bad_new, expected_new_cases=2)['state'] == 'BASELINE_ALSO_FAILS'
    print(json.dumps({'fixture': 'PASS', 'negative_controls': rejected, 'duplicate_multiset': True,
                      'inherited_failures_remain_failures': True}))


if __name__ == '__main__':
    if sys.argv[1:] == ['fixture']:
        tiny_fixture()
    else:
        assert len(sys.argv) == 4, 'usage: compare_reports.py BASELINE CANDIDATE OUTPUT'
        result = compare(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()))
        with Path(sys.argv[3]).open('x') as output:
            json.dump(result, output, indent=2)
            output.write('\n')
