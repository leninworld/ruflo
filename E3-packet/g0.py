"""Tiny report acceptance/rejection fixture, executed only inside approved G0."""
import copy
from report_contract import inspect_report
from compare import compare
r={'success':True,'numTotalTests':1,'numPassedTests':1,'numFailedTests':0,'numPendingTests':0,'numTotalTestSuites':1,'numFailedTestSuites':0,'testResults':[{'name':'/work/source/a.test.ts','status':'passed','assertionResults':[{'ancestorTitles':[],'title':'case','status':'passed'}]}]}
assert inspect_report(r,'/work/source')
assert compare(r,r,'/work/source','/work/source',[])['all_tests_passed']
for mutation in ['empty','count','lost-pass','skip']:
 b=copy.deepcopy(r)
 if mutation=='empty':b['testResults']=[]
 elif mutation=='count':b['numTotalTests']=2
 else:
  b['testResults'][0]['assertionResults'][0]['status']='failed' if mutation=='lost-pass' else 'pending'
  b['numPassedTests']=0;b['success']=False
  b['numFailedTests']=int(mutation=='lost-pass');b['numPendingTests']=int(mutation=='skip')
 try:compare(r,b,'/work/source','/work/source',[])
 except AssertionError:pass
 else:raise AssertionError('FALSE_GREEN_'+mutation)
print('G0_REPORT_FIXTURES_COMPLETE')
