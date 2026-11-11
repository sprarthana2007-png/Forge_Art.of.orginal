import unittest
import ui


class AgeTests(unittest.TestCase):
    def test_age_text(self):
        self.assertEqual(ui.age_text(9), '9 days')
        self.assertEqual(ui.age_text(1), '1 day')
        self.assertEqual(ui.age_text(448), '1 year 2 months')
        self.assertEqual(ui.age_text(245), '8 months')
        self.assertEqual(ui.age_text(8276), '22 years')
        self.assertIsNone(ui.age_text(None))
        self.assertIsNone(ui.age_text(-1))


if __name__ == '__main__':
    unittest.main()
