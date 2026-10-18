import tempfile
import unittest
from pathlib import Path
from sponsors.credentials import read_key

class CredentialTests(unittest.TestCase):
    def read(self, content, name):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'private.env';p.write_text(content)
            return read_key(p,name)
    def test_two_raw_provider_keys_are_separated(self):
        s='xai-example_test_only\nfk-example_test_only\n'
        self.assertEqual(self.read(s,'XAI_API_KEY'),'xai-example_test_only')
        self.assertEqual(self.read(s,'FACTORY_API_KEY'),'fk-example_test_only')
    def test_other_provider_is_not_a_fallback(self):
        self.assertIsNone(self.read('fk-example_test_only','XAI_API_KEY'))
    def test_named_assignments_and_comments(self):
        s='export XAI_API_KEY="xai-test" # comment\nFACTORY_API_KEY=fk-test\n'
        self.assertEqual(self.read(s,'FACTORY_API_KEY'),'fk-test')
    def test_ambiguous_keys_rejected(self):
        with self.assertRaises(ValueError):self.read('fk-one\nfk-two','FACTORY_API_KEY')
