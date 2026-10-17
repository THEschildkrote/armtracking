"""SDK-backed graph and request-contract tests; skipped when chalkpy is absent."""
import importlib.util
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from look_intent.scoring import Obj, Params, decide, params_json


@unittest.skipUnless(importlib.util.find_spec('chalk'), 'optional chalkpy not installed')
class ChalkContracts(unittest.TestCase):
    def test_resolver_graph_matches_local_for_both_ablations(self):
        import chalk_features as f
        objects=[Obj('c','cup',0,300),Obj('s','spoon',100,300)]
        gaze=[(i/30,0,300) for i in range(13)]
        history=['block']
        corrections=[dict(previous_cls='block',correct_cls='spoon')]
        for beta in (0,0.5,2):
            p=Params(beta=beta)
            raw=params_json(p)
            objs=json.dumps([o.__dict__ for o in objects])
            shares=f.resolve_gaze_shares(t_now=.4,gaze_json=json.dumps(gaze),objects_json=objs,params_json=raw)
            prior=f.resolve_transition_prior(history_json=json.dumps(history),objects_json=objs,
                                            corrections_json=json.dumps(corrections),params_json=raw)
            decision=f.resolve_decision(t_now=.4,gaze_json=json.dumps(gaze),objects_json=objs,
                                       shares_json=shares,prior_json=prior,params_json=raw)
            local=decide(objects,gaze,history,.4,p,corrections)
            self.assertEqual(decision.top_object_id,local.object_id)
            self.assertAlmostEqual(decision.confidence,local.confidence)
            self.assertEqual(decision.should_prehover,local.prehover)

    def test_online_request_includes_same_corrections_and_params(self):
        import chalk_features as f
        from look_intent.client import IntentService
        p=Params(beta=2)
        service=IntentService('test',use_chalk=False,params=p)
        self.addCleanup(service.close)
        service._client=Mock()
        response=service._client.query.return_value
        values={f.IntentRequest.top_object_id:'c',f.IntentRequest.confidence:.8,
                f.IntentRequest.should_prehover:True,f.IntentRequest.probs_json:'{"c":0.8,"s":0.2}'}
        response.get_feature_value.side_effect=values.get
        corrections=[dict(previous_cls='block',correct_cls='cup')]
        service.predict([Obj('c','cup',0,300)],[],['block'],1,corrections=corrections)
        kwargs=service._client.query.call_args.kwargs
        self.assertEqual(json.loads(kwargs['input'][f.IntentRequest.corrections_json]),corrections)
        self.assertEqual(json.loads(kwargs['input'][f.IntentRequest.params_json])['beta'],2)
        self.assertEqual(kwargs['input'][f.IntentRequest.id],service.last_query['query_id'])
        self.assertLessEqual(kwargs['request_timeout'],.15)

    def test_offline_request_preserves_distinct_configuration(self):
        import pandas as pd
        import chalk_features as f
        from evals.replay import Tick,predict_chalk
        tk=[Tick(1,[Obj('c','cup',0,300)],[],['block'],
                 [dict(previous_cls='block',correct_cls='cup')])]
        inputs=[]
        def offline_query(**kwargs):
            values=kwargs['input']
            inputs.append(values)
            frame=pd.DataFrame({'intent_request.id':values[f.IntentRequest.id],
                'intent_request.top_object_id':['c'],'intent_request.confidence':[.8],
                'intent_request.should_prehover':[False]})
            result=Mock()
            result.get_data_as_pandas.return_value=frame
            return result
        with patch('chalk.client.ChalkClient') as client:
            client.return_value.offline_query.side_effect=offline_query
            predict_chalk(tk,Params(beta=0))
            predict_chalk(tk,Params(beta=1))
        self.assertEqual([json.loads(v[f.IntentRequest.params_json][0])['beta'] for v in inputs],[0,1])
        self.assertTrue(json.loads(inputs[0][f.IntentRequest.corrections_json][0]))
