import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sponsors import call_evidence, logging

class EvidenceTests(unittest.TestCase):
    def test_receipt_drops_private_payload_and_unexpected_values(self):
        value=call_evidence.receipt({'timestamp':'2026-10-18T12:00:00+00:00', 'result':'success',
            'session_id':'12345678-1234-1234-1234-123456789abc', 'latency_ms':12,
            'error':'a secret message', 'api_key':'secret', 'priors':{'medication':1},
            'audio':'private', 'summary':'private', 'user_id':'wearer', 'call_id':'fk-secret'})
        self.assertEqual(value['error'],'other')
        self.assertEqual(set(value),{'timestamp','result','session_id','latency_ms','error'})
    def test_new_logs_append_receipts_without_exporting_other_logs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(logging,'ROOT',Path(tmp)), patch.object(call_evidence,'ROOT',Path(tmp)):
            logging.log('grok_calls.jsonl',result='CONFIRM',api_attempted=True,transcript='private')
            logging.log('showcase.jsonl',message='private')
            rows=(Path(tmp)/'evidence/api_calls/grok_calls.jsonl').read_text().splitlines()
            self.assertEqual(len(rows),1)
            self.assertNotIn('transcript',json.loads(rows[0]))
            self.assertFalse((Path(tmp)/'evidence/api_calls/showcase.jsonl').exists())
    def test_receipt_disk_failure_preserves_private_log(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(logging,'ROOT',Path(tmp)), patch.object(call_evidence,'append_receipt',side_effect=OSError('full')):
            logging.log('chalk_queries.jsonl',result='success')
            self.assertEqual(json.loads((Path(tmp)/'logs/chalk_queries.jsonl').read_text())['result'],'success')
