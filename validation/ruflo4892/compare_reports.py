"""Complete Vitest comparison for #1796; execute only in approved Docker.

The inherited report validator retains collection failures and duplicate cases.
Only the independently source-proven event timestamp token can be normalized.
"""
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

EVENT_FILE = 'plugins/ruflo-console/tests/events-sweep.spec.ts'
EVENT_FIXTURE = 'plugins/ruflo-console/tests/fixtures/ev-rig.ts'
EVENT_ANCESTORS = ['every button does something']
EVENT_TITLE = re.compile(r'^(events: ev-open-)\d+(:[^:]+:\d+:[a-z0-9]+)$')
STATUSES = {'passed', 'failed', 'pending', 'skipped', 'todo', 'disabled'}
SKIPS = STATUSES - {'passed', 'failed'}


def relative_file(name, source_root):
    assert isinstance(name, str) and name, 'MISSING_TEST_FILE_ID'
    root = str(source_root).rstrip('/') + '/'
    if name.startswith(root):
        name = name[len(root):]
    assert not name.startswith('/') and '..' not in name.split('/'), 'UNRESOLVED_TEST_FILE_ID'
    return name.removeprefix('./')


def verify_event_sources(baseline, candidate):
    """Actual baseline/candidate source digests; never infer equality from titles."""
    for name in [EVENT_FILE, EVENT_FIXTURE]:
        old, new = baseline.get(name), candidate.get(name)
        assert isinstance(old, str) and re.fullmatch('[0-9a-f]{64}', old), 'MISSING_UI_SOURCE_HASH'
        assert old == new, 'CHANGED_UI_SOURCE_HASH'


def inspect_report(report, source_root='/work/source', normalize_events=False):
    assert isinstance(report, dict), 'REPORT_NOT_AN_OBJECT'
    assert isinstance(report.get('success'), bool), 'MISSING_ROOT_SUCCESS'
    for key in ['numTotalTests', 'numPassedTests', 'numFailedTests', 'numPendingTests',
                'numTotalTestSuites', 'numFailedTestSuites']:
        assert type(report.get(key)) is int and report[key] >= 0, 'MISSING_OR_INVALID_' + key
    files = report.get('testResults')
    assert isinstance(files, list) and files, 'EMPTY_TEST_FILES'
    counts, messages = defaultdict(Counter), defaultdict(Counter)
    collection_errors, file_statuses = {}, {}
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
            if normalize_events and name == EVENT_FILE and ancestors == EVENT_ANCESTORS:
                title = EVENT_TITLE.sub(r'\1<TIME>\2', title)
            identity = (name, tuple(ancestors), title)
            counts[identity][status] += 1
            failure = case.get('failureMessages', [])
            assert isinstance(failure, list) and all(isinstance(m, str) for m in failure), 'BAD_FAILURE_MESSAGES'
            # Keep messages exact apart from the already declared source-root prefix.
            messages[identity][(status, tuple(m.replace(str(source_root), '<source>') for m in failure))] += 1
    total = sum(sum(c.values()) for c in counts.values())
    assert total > 0, 'NO_ASSERTIONS_COLLECTED'
    assert report['numTotalTests'] == total, 'SUMMARY_ASSERTION_COUNT_MISMATCH'
    observed = Counter()
    for statuses in counts.values():
        observed.update(statuses)
    assert report['numPassedTests'] == observed['passed'], 'PASS_SUMMARY_MISMATCH'
    assert report['numFailedTests'] == observed['failed'], 'FAIL_SUMMARY_MISMATCH'
    assert report['numPendingTests'] + report.get('numTodoTests', 0) == sum(observed[s] for s in SKIPS), 'SKIP_SUMMARY_MISMATCH'
    if report['success']:
        assert observed['failed'] == 0 and not collection_errors and report['numFailedTestSuites'] == 0, 'FALSE_SUCCESS'
    report_failure = not report['success'] and not observed['failed'] and not collection_errors
    return counts, messages, collection_errors, file_statuses, report_failure


def compare(baseline, candidate, source_hashes, expected_new=None, source_root='/work/source'):
    """expected_new maps exact added test paths to their reviewed case counts."""
    expected_new = expected_new or {}
    verify_event_sources(*source_hashes)
    old, old_messages, old_errors, old_files, old_failure = inspect_report(baseline, source_root, True)
    new, new_messages, new_errors, new_files, new_failure = inspect_report(candidate, source_root, True)
    assert not new_failure or old_failure, 'NEW_REPORT_LEVEL_FAILURE'
    assert set(old_files) <= set(new_files), 'LOST_TEST_FILE'
    assert set(new_files) - set(old_files) <= set(expected_new), 'UNEXPECTED_TEST_FILE_DELTA'
    assert set(new_errors) <= set(old_errors), 'NEW_COLLECTION_FAILURE'
    for name, message in new_errors.items():
        assert message == old_errors[name], 'CHANGED_COLLECTION_FAILURE'
    for identity, previous in old.items():
        assert identity in new, 'LOST_CASE_ID'
        current = new[identity]
        assert sum(previous.values()) == sum(current.values()), 'LOST_OR_DUPLICATED_CASE'
        assert current['passed'] >= previous['passed'], 'LOST_BASELINE_PASS'
        assert current['failed'] <= previous['failed'], 'NEW_EXISTING_CASE_FAILURE'
        assert sum(current[s] for s in SKIPS) <= sum(previous[s] for s in SKIPS), 'NEW_EXISTING_CASE_SKIP'
        # Failure text changes are not hidden by timestamp normalization.
        old_failures = Counter({key: value for key, value in old_messages[identity].items() if key[0] == 'failed'})
        new_failures = Counter({key: value for key, value in new_messages[identity].items() if key[0] == 'failed'})
        assert not (new_failures - old_failures), 'CHANGED_EXISTING_FAILURE_MESSAGE'
    assert all(i in old or i[0] in expected_new for i in new), 'UNEXPECTED_CASE_ID'
    added = {}
    for filename, count in expected_new.items():
        assert filename not in old_files and filename not in new_errors, 'INVALID_NEW_FILE_BASELINE_OR_COLLECTION'
        cases = [v for i, v in new.items() if i[0] == filename]
        assert cases and sum(sum(v.values()) for v in cases) == count, 'NEW_CASE_COUNT_MISMATCH'
        assert all(set(v) == {'passed'} for v in cases), 'NEW_REGRESSION_NOT_ALL_PASS'
        added[filename] = count
    inherited = sum(v['failed'] for v in new.values())
    return {'state': 'BASELINE_ALSO_FAILS' if inherited or new_errors or new_failure else 'COMPARISON_PASS',
            'all_tests_passed': not inherited and not new_errors and not new_failure,
            'baseline_case_occurrences': sum(sum(v.values()) for v in old.values()),
            'candidate_case_occurrences': sum(sum(v.values()) for v in new.values()),
            'new_cases': added, 'inherited_failed_assertions': inherited,
            'inherited_collection_failures': new_errors, 'inherited_report_failure': new_failure,
            'normalization': 'Only exact event file/ancestors/timestamp token; source digests equal; raw reports retained.'}


def resummarize(report):
    """Fixture mutations repair summaries, so controls test comparison, not counts."""
    cases = [c for f in report['testResults'] for c in f['assertionResults']]
    report.update(numTotalTests=len(cases), numPassedTests=sum(c['status'] == 'passed' for c in cases),
                  numFailedTests=sum(c['status'] == 'failed' for c in cases),
                  numPendingTests=sum(c['status'] in SKIPS for c in cases), numTodoTests=0)
    for f in report['testResults']:
        if f['assertionResults']:
            f['status'] = 'failed' if any(c['status'] == 'failed' for c in f['assertionResults']) else 'passed'
    report['numFailedTestSuites'] = sum(f['status'] == 'failed' for f in report['testResults'])
    report['success'] = report['numFailedTests'] == report['numFailedTestSuites'] == 0


def retained_fixtures(baseline_path, candidate_path, sources):
    baseline, candidate = [json.loads(Path(p).read_text()) for p in [baseline_path, candidate_path]]
    # The old provider feature is unrelated to #1796. Restore only its exact
    # baseline report entry to isolate the real retained timestamp comparison.
    legacy = 'v3/@claude-flow/cli/__tests__/openai-configured-provider-4834.test.ts'
    before = [f for f in baseline['testResults'] if relative_file(f['name'], '/work/source') == legacy]
    after = [f for f in candidate['testResults'] if relative_file(f['name'], '/work/source') == legacy]
    assert len(before) == len(after) == 1, 'RETAINED_FIXTURE_INPUT_CHANGED'
    candidate = copy.deepcopy(candidate)
    candidate['testResults'][candidate['testResults'].index(after[0])] = copy.deepcopy(before[0])
    resummarize(candidate)
    positive = compare(baseline, candidate, sources)
    event = next(f for f in candidate['testResults'] if relative_file(f['name'], '/work/source') == EVENT_FILE)
    matching = [i for i, c in enumerate(event['assertionResults']) if c['ancestorTitles'] == EVENT_ANCESTORS and EVENT_TITLE.fullmatch(c['title'])]
    assert len(matching) == 12, 'RETAINED_EVENT_SET_CHANGED'
    negatives = []
    def reject(label, mutate):
        bad = copy.deepcopy(candidate)
        target = next(f for f in bad['testResults'] if relative_file(f['name'], '/work/source') == EVENT_FILE)
        mutate(target['assertionResults'])
        resummarize(bad)
        try:
            compare(baseline, bad, sources)
        except AssertionError as error:
            negatives.append({'control': label, 'error': str(error)})
        else:
            raise AssertionError('ACCEPTED_NEGATIVE_' + label)
    number = matching[0]
    reject('missing case', lambda rows: rows.pop(number))
    reject('duplicated case', lambda rows: rows.append(copy.deepcopy(rows[number])))
    reject('status regression', lambda rows: rows[number].update(status='failed', failureMessages=['deliberate fixture failure']))
    reject('semantic ID suffix change', lambda rows: rows[number].update(title=rows[number]['title'] + '-changed'))
    reject('new skipped case', lambda rows: rows[number].update(status='pending'))
    # A source mismatch independently disables timestamp mapping.
    wrong = (sources[0], dict(sources[1], **{EVENT_FIXTURE: '0' * 64}))
    try:
        compare(baseline, candidate, wrong)
    except AssertionError as error:
        negatives.append({'control': 'changed UI fixture source', 'error': str(error)})
    else:
        raise AssertionError('ACCEPTED_CHANGED_SOURCE')
    return {'fixture': 'PASS', 'positive': positive, 'negative_controls': negatives,
            'qualification': 'Actual retained report inputs with unrelated #4834 entry reset to baseline; not a new product test or old-run pass.'}


if __name__ == '__main__':
    assert len(sys.argv) == 6 and sys.argv[1] in ['compare', 'retained-fixtures'], 'mode BASELINE CANDIDATE PLAN OUTPUT'
    plan = json.loads(Path(sys.argv[4]).read_text())
    if sys.argv[1] == 'retained-fixtures':
        result = retained_fixtures(sys.argv[2], sys.argv[3], plan['source_hashes'])
    else:
        result = compare(json.loads(Path(sys.argv[2]).read_text()), json.loads(Path(sys.argv[3]).read_text()),
                         plan['source_hashes'], plan['expected_new'])
    with Path(sys.argv[5]).open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
