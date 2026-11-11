import copy
import json
import tempfile
import unittest
from pathlib import Path

import storage
from schemas import state_from_dict, state_to_dict

DEMO = json.loads((Path(storage.ROOT) / 'forge_state.json').read_text(encoding='utf-8'))


class SharedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = storage.DB_FILE, storage.STATE_FILE
        storage.DB_FILE = Path(self.temp.name) / 'test.db'
        storage.STATE_FILE = Path(self.temp.name) / 'forge_state.json'
        self.write(DEMO)

    def tearDown(self):
        storage.DB_FILE, storage.STATE_FILE = self.old
        self.temp.cleanup()

    def write(self, data):
        storage.STATE_FILE.write_text(json.dumps(data), encoding='utf-8')

    def read(self):
        return json.loads(storage.STATE_FILE.read_text(encoding='utf-8'))

    def test_demo_file_matches_the_shared_schema(self):
        self.assertEqual(state_to_dict(state_from_dict(DEMO)), DEMO)

    def test_suspects_become_numbered_cases_and_ignored_are_hidden(self):
        data = copy.deepcopy(DEMO)
        data['suspects'].append({'url': 'https://harmless.example', 'domain': 'harmless.example', 'label': 'ignored'})
        self.write(data)
        cases = storage.load_cases()
        self.assertEqual([c['case_id'] for c in cases], [1, 2, 3, 4])
        self.assertEqual(cases[0]['domain'], 'northstar-sale.example')
        self.assertEqual(cases[0]['official_screenshot'], 'evidence/official.png')

    def test_review_survives_pipeline_rerun_and_ids_stay_stable(self):
        storage.load_cases()
        storage.save_review(1, decision='fake')
        data = copy.deepcopy(DEMO)
        data['suspects'].reverse()                    # pipeline writes suspects in another order
        data['suspects'][-1]['score'] = 99            # and rescored the first one
        data['suspects'].append({'url': 'https://new-copy.example', 'label': 'suspicious', 'score': 60})
        self.write(data)
        cases = {c['domain']: c for c in storage.load_cases()}
        self.assertEqual(cases['northstar-sale.example']['case_id'], 1)
        self.assertEqual(cases['northstar-sale.example']['decision'], 'fake')
        self.assertEqual(cases['northstar-sale.example']['risk_score'], 99)
        self.assertEqual(cases['new-copy.example']['case_id'], 5)

    def test_owner_decision_is_written_back_as_suspect_status(self):
        storage.load_cases()
        storage.save_review(2, decision='genuine')
        storage.save_review(1, decision='fake')
        storage.save_review(1, report_status='sent')
        after = {s['domain']: s for s in self.read()['suspects']}
        self.assertEqual(after['northstar-outlet.example']['status'], 'not_fake')
        self.assertEqual(after['northstar-sale.example']['status'], 'reported')
        before = {s['domain']: s for s in DEMO['suspects']}
        for domain, suspect in after.items():   # nothing but status changes
            self.assertEqual({k: v for k, v in suspect.items() if k != 'status'},
                             {k: v for k, v in before[domain].items() if k != 'status'})
        self.assertEqual(self.read()['brand_dna'], DEMO['brand_dna'])

    def test_pipeline_status_seeds_new_cases(self):
        data = copy.deepcopy(DEMO)
        data['suspects'][1]['status'] = 'not_fake'
        data['suspects'][3]['status'] = 'reported'
        self.write(data)
        cases = {c['domain']: c for c in storage.load_cases()}
        self.assertEqual(cases['northstar-outlet.example']['decision'], 'genuine')
        self.assertEqual((cases['northstar-rewards.example']['decision'], cases['northstar-rewards.example']['report_status']),
                         ('fake', 'sent'))

    def test_bad_state_is_rejected(self):
        for change in ({'score': 101}, {'label': 'maybe'}, {'domain_age_days': -3}, {'matched_image_pairs': [['a', 'b']]}):
            data = copy.deepcopy(DEMO)
            data['suspects'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                storage.validate_state(data)


if __name__ == '__main__':
    unittest.main()
