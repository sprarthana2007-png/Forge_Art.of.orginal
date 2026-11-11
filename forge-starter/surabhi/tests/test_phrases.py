import json
import unittest
import storage
from phrases import find_phrases, headline


class PhraseTests(unittest.TestCase):
    def test_scam_page_phrases(self):
        suspects = json.loads(storage.STATE_FILE.read_text(encoding='utf-8'))['suspects']
        cases = {n: {'page_text': s['page_text']} for n, s in enumerate(suspects, 1)}
        found = {label: match for label, _, match, _ in find_phrases(cases[4]['page_text'])}
        self.assertIn('Fee to claim', found)
        self.assertIn('Prize or reward', found)
        self.assertEqual(found['Countdown pressure'].lower(), 'reserved for 04:59 minutes')
        self.assertEqual(headline(find_phrases(cases[4]['page_text'])), 'UPI only')
        self.assertIn('UPI only', [label for label, *_ in find_phrases(cases[1]['page_text'])])

    def test_plain_text_has_no_phrases(self):
        self.assertEqual(find_phrases('Seasonal collection. Free shipping over 2,999.'), [])
        self.assertIsNone(headline([]))


if __name__ == '__main__':
    unittest.main()
