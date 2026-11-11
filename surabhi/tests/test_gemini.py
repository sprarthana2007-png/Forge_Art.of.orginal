import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import gemini
import storage

REPLY = {'verdict': 'Likely impersonation', 'confidence': 140, 'summary': 'Copies the brand.',
         'findings': [{'title': 'Logo copied', 'detail': 'SHA-256 match.', 'points_to': 'impersonation'}],
         'recommended_action': 'Report to the registrar.'}


class FakeClient:
    """Stands in for google.genai.Client and records what was sent."""
    def __init__(self, text):
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self._text = text

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=None, text=self._text)


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = storage.DB_FILE
        storage.DB_FILE = Path(self.temp.name) / 'test.db'
        self.case = storage.load_cases()[0]
        self.brand = storage.load_brand()
        self.profile = dict(storage.PROFILE_DEFAULTS)

    def tearDown(self):
        storage.DB_FILE = self.old_db
        self.temp.cleanup()

    def test_investigation_contains_the_evidence(self):
        inv = gemini.build_investigation(self.case, self.profile, self.brand, today=date(2026, 11, 11))
        self.assertEqual(inv['brand']['name'], 'Northstar')
        self.assertEqual(inv['brand']['official_store'], 'https://northstar.example')
        self.assertEqual(inv['suspect']['domain'], 'northstar-sale.example')
        self.assertEqual(inv['suspect']['domain_age_days'], self.case['domain_age_days'])
        self.assertTrue(inv['automated_checks']['logo_copied'])
        self.assertEqual(inv['automated_checks']['exact_image_copies'], 4)  # product photos; the logo is logo_copied
        self.assertTrue(inv['automated_checks']['image_matches'])
        json.dumps(inv)  # must be serialisable

    def test_get_verdict_sends_json_and_screenshots(self):
        client = FakeClient(json.dumps(REPLY))
        sent, result, model = gemini.get_verdict(self.case, self.profile, self.brand, client=client, model='test-model')
        call = client.calls[0]
        self.assertEqual(call['model'], 'test-model')
        self.assertEqual(call['config'].response_mime_type, 'application/json')
        self.assertTrue(call['contents'][0].startswith('Investigation (JSON):'))
        self.assertEqual(json.loads(call['contents'][0].split('\n', 1)[1])['suspect']['domain'],
                         'northstar-sale.example')
        images = [p for p in call['contents'] if not isinstance(p, str)]
        self.assertEqual(len(images), 2)
        self.assertEqual(result['confidence'], 100)  # clamped
        self.assertEqual(sent['brand']['name'], 'Northstar')

    def test_bad_reply_raises(self):
        with self.assertRaises(Exception):
            gemini.get_verdict(self.case, self.profile, self.brand, client=FakeClient('{"verdict": "Maybe"}'), model='m')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = storage.DB_FILE
        storage.DB_FILE = Path(self.temp.name) / 'test.db'

    def tearDown(self):
        storage.DB_FILE = self.old_db
        self.temp.cleanup()

    def test_verdict_and_profile_round_trip(self):
        self.assertIsNone(storage.latest_verdict(1))
        storage.save_verdict(1, 'm1', {'a': 1}, {'verdict': 'Suspicious'})
        storage.save_verdict(1, 'm2', {'a': 2}, {'verdict': 'Likely impersonation'})
        self.assertEqual(storage.latest_verdict(1)['model'], 'm2')
        self.assertEqual(storage.load_profile()['appearance'], 'Light')
        self.assertEqual(storage.save_profile(website='brand.example')['website'], 'brand.example')
        with self.assertRaises(ValueError):
            storage.save_profile(brand_name=' ')


if __name__ == '__main__':
    unittest.main()
