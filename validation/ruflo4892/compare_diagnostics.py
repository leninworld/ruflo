"""Compare substantive diagnostics even when a wrapper masks the exit status."""
from collections import Counter
import json
from pathlib import Path
import re
import sys

ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
TS=re.compile(r'\berror TS\d+:')
LINT=re.compile(r'^\s*\d+:\d+\s+error\b|\berror\s+[A-Za-z0-9@][A-Za-z0-9@/_.-]+\s*$')
OTHER=re.compile(r'(?:^|\s)(?:[A-Za-z]*Error|UnhandledPromiseRejection|Unhandled Error|FATAL ERROR|LLVM ERROR):|Segmentation fault|core dumped|heap out of memory|"error"\s*:\s*"[^"\s]|"(?:ok|success)"\s*:\s*false|"(?:state|status)"\s*:\s*"(?:FAIL|FAILED|ERROR)"')


def extract(text,source_root='/work/source'):
    result=Counter();header=''
    for raw in text.splitlines():
        line=ANSI.sub('',raw).replace(source_root,'<source>').rstrip()
        if re.fullmatch(r'(?:<source>/|\./)?[^\s]+\.(?:[cm]?[jt]sx?)',line.strip()):header=line.strip()
        if TS.search(line):result['typescript: '+line.strip()]+=1
        elif LINT.search(line):result['lint: '+header+' '+line.strip()]+=1
        elif OTHER.search(line):result['runtime: '+line.strip()]+=1
    return result


def crashes(text,reports_complete=False):
    # Compare stable crash categories, not volatile test failure text or stack paths.
    patterns={'fatal runtime crash':r'FATAL ERROR|LLVM ERROR', 'heap out of memory':r'heap out of memory',
              'segmentation fault':r'Segmentation fault|core dumped'}
    if reports_complete:patterns.pop('segmentation fault')
    return Counter({name:len(re.findall(pattern,text,re.I)) for name,pattern in patterns.items() if re.search(pattern,text,re.I)})


def compare(before,after,test_runner=False,reports=None):
    if test_runner:
        from compare_reports import inspect_report
        assert isinstance(reports,(list,tuple)) and len(reports)==2,'MISSING_TEST_REPORTS'
        for report in reports:inspect_report(report)
        old,new=crashes(before,True),crashes(after,True)
    else:old,new=extract(before),extract(after)
    added=new-old
    assert not added,('NEW_COMMAND_DIAGNOSTIC',dict(added))
    return {'state':'NO_NEW_DIAGNOSTICS','baseline':dict(old),'candidate':dict(new),
            'recorded_shutdown_segfaults':{'baseline':len(re.findall(r'Segmentation fault|core dumped',before,re.I)), 'candidate':len(re.findall(r'Segmentation fault|core dumped',after,re.I))} if test_runner else None,
            'mode':'CRASH_ONLY_STRUCTURED_REPORT_IS_AUTHORITATIVE' if test_runner else 'COMMAND_DIAGNOSTICS',
            'qualification':'Existing diagnostics remain failures; this is not an all-checks-pass claim.'}


def fixture():
    p=Path(__file__).resolve().parent
    text=(p/'retained-ts-diagnostics.txt').read_text()
    observed=extract(text);assert any(key.startswith('typescript: ') for key in observed),'NO_REAL_RETAINED_TS_ERRORS'
    assert compare(text,text)['state']=='NO_NEW_DIAGNOSTICS'
    rejected=[]
    for label,extra in [('new TS error','\nnew.ts(1,1): error TS9999: deliberate new fixture failure'),
                        ('duplicate existing TS error','\n'+next(line for line in text.splitlines() if TS.search(line))),
                        ('new lint error','\n<source>/new.ts\n  2:3  error  deliberately invalid  no-undef'),
                        ('new runtime error','\nTypeError: deliberately changed helper failure'),
                        ('masked compiler crash','\nFATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory')]:
        try:compare(text,text+extra)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('ACCEPTED_'+label)
    assert compare(text,'')['state']=='NO_NEW_DIAGNOSTICS'
    report=json.loads((p/'retained-root-baseline.json').read_text())
    volatile='Error: expected value /tmp/test-42/file port 1987 duration 0.13s'
    changed='Error: expected value /tmp/test-91/file port 1988 duration 0.51s'
    assert compare(volatile,changed,test_runner=True,reports=[report,report])['state']=='NO_NEW_DIAGNOSTICS'
    try:compare(volatile,changed+'\nFATAL ERROR: heap out of memory',test_runner=True,reports=[report,report])
    except AssertionError:rejected.append('test-runner crash')
    else:raise AssertionError('ACCEPTED_TEST_RUNNER_CRASH')
    shutdown=compare(volatile,changed+'\nSegmentation fault (core dumped)',test_runner=True,reports=[report,report])
    assert shutdown['state']=='NO_NEW_DIAGNOSTICS' and shutdown['recorded_shutdown_segfaults']['candidate']>0
    truncated=dict(report);truncated.pop('testResults')
    for label,broken in [('truncated report',truncated),('missing report',None),('unparseable report','{')]:
        try:compare(volatile,changed+'\nSegmentation fault (core dumped)',test_runner=True,reports=[report,broken])
        except AssertionError:rejected.append(label+' with segfault')
        else:raise AssertionError('ACCEPTED_'+label)
    print(json.dumps({'fixture':'PASS','actual_retained_diagnostics':dict(observed),'negative_controls':rejected,'zero_exit_wrapper_does_not_hide_errors':True}))


if __name__=='__main__':
    assert sys.argv[1:]==['fixture'];fixture()
