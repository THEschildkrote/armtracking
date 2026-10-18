import unittest
from sponsors.evaluation import describe

class EvaluationTextTests(unittest.TestCase):
    def report(self,n=0,pre=0,bad=0):
        metrics=dict(fixations=n,prehovers=pre,coverage=0,lead_s_median=0,top1_at_300ms=0,false_prehover=0,wrong_predictions_at_correction=0,correction_cases=0)
        return dict(backend='local',synthetic=True,results={'gaze only':dict(metrics),'gaze + history':dict(metrics)},safety={'s':{'descends':bad,'descend_without_authorization':bad}})
    def test_no_evidence_is_not_perfect_performance(self):
        text=describe(self.report(),'Synthetic',1)
        self.assertIn('Authorization audit: N/A',text)
        self.assertIn('Top prediction 300 ms before confirmation: N/A',text)
        self.assertIn('SYNTHETIC SMOKE TEST ONLY',text)
        self.assertIn('no Chalk API call',text)
    def test_violations_remain_visible(self):
        self.assertIn('2 flagged descents / 2 logged descents',describe(self.report(bad=2),'test',1))
    def test_negative_change_not_hidden(self):
        r=self.report(n=10);r['results']['gaze only']['top1_at_300ms']=.8;r['results']['gaze + history']['top1_at_300ms']=.5
        self.assertIn('accuracy -30.0 percentage points',describe(r,'test',1))
