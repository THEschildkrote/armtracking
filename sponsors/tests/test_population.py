import unittest
from intent.look_routine.population import personalize,object_prior

class PopulationTests(unittest.TestCase):
    def test_new_user_and_personal_takeover(self):
        q={'phone':.8,'mug':.2}
        self.assertEqual(personalize({'phone':0,'mug':0},q),q)
        p=personalize({'phone':0,'mug':80},q)
        self.assertGreater(p['mug'],.9);self.assertAlmostEqual(sum(p.values()),1)
    def test_disabled_preserves_old_prior(self):
        self.assertEqual(personalize({'phone':0,'mug':0},{}),{'phone':.5,'mug':.5})
    def test_drink_does_not_claim_bottle_over_mug(self):
        q=object_prior({'hour_activity_priors':{'15':{'Drink':1.}}},['mug','water_bottle','inhaler'],15)
        self.assertEqual(q['mug'],q['water_bottle']);self.assertGreater(q['inhaler'],0)
    def test_unknown_objects_neutral(self):
        q=object_prior({'hour_activity_priors':{'15':{'Drink':1.}}},['unknown','other'],15)
        self.assertEqual(q,{'other':.5,'unknown':.5})
    def test_split_has_no_overlap_and_deploy_uses_train_only(self):
        from scripts.build_casas_prior import build
        rows={str(i):{'hours':[{'Drink':i+1} for _ in range(24)]} for i in range(10)}
        data={'recordings':rows,'archive_sha256':'test','source_url':'test'}
        p,e=build(data)
        self.assertFalse(set(p['training_recordings']) & set(p['held_out_recordings']))
        old=p['hour_activity_priors']
        for name in p['held_out_recordings']:rows[name]['hours']=[{'Phone':9999} for _ in range(24)]
        self.assertEqual(old,build(data)[0]['hour_activity_priors'])
