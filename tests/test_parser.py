"""Offline unit tests for the structured-output parser (no model or network needed).

Run: python -m unittest discover -s tests -v
"""
import unittest

from src.parser import parse_model_output

CLEAN = ('{"case_summary": "Customer disputes a $40 charge.", "stated_facts": ["$40 charge"], '
         '"unknowns": ["merchant name"], "fraud_or_security_concern": false, '
         '"recommended_routing": "Card Billing Disputes", "human_review_required": false, '
         '"rationale": "Billing error, no fraud language."}')


class ParserTests(unittest.TestCase):
    def test_clean_json_is_ok(self):
        r = parse_model_output(CLEAN)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.record["recommended_routing"], "Card Billing Disputes")

    def test_code_fence_and_prose_are_repaired(self):
        r = parse_model_output("Here is the JSON:\n```json\n" + CLEAN + "\n```\nHope this helps")
        self.assertEqual(r.status, "repaired")
        self.assertIn("removed_code_fence", r.repairs)

    def test_leading_prose_without_fence(self):
        r = parse_model_output("Sure! " + CLEAN)
        self.assertEqual(r.status, "repaired")
        self.assertIn("removed_leading_text", r.repairs)

    def test_python_booleans_and_trailing_comma(self):
        bad = CLEAN.replace("false", "False").replace('"}', '",}')
        r = parse_model_output(bad)
        self.assertEqual(r.status, "repaired")
        self.assertFalse(r.record["human_review_required"])

    def test_routing_paraphrase_normalised(self):
        r = parse_model_output(CLEAN.replace('"Card Billing Disputes"', '"Card Fraud and Security team"'))
        self.assertEqual(r.record["recommended_routing"], "Card Fraud & Security")
        self.assertEqual(r.status, "repaired")

    def test_ambiguous_routing_is_invalid(self):
        r = parse_model_output(CLEAN.replace('"Card Billing Disputes"', '"Billing dispute with fraud review"'))
        self.assertEqual(r.status, "invalid")
        self.assertIsNone(r.record["recommended_routing"])

    def test_yes_no_strings_become_bool(self):
        r = parse_model_output(CLEAN.replace('"human_review_required": false', '"human_review_required": "Yes"'))
        self.assertTrue(r.record["human_review_required"])

    def test_missing_field_is_invalid(self):
        r = parse_model_output(CLEAN.replace('"human_review_required": false, ', ''))
        self.assertEqual(r.status, "invalid")
        self.assertIn("human_review_required: missing", r.errors)

    def test_truncated_output_fails(self):
        r = parse_model_output(CLEAN[:80])
        self.assertEqual(r.status, "failed")

    def test_braces_inside_strings(self):
        r = parse_model_output(CLEAN.replace("Billing error, no fraud language.", "Text with } brace"))
        self.assertEqual(r.status, "ok")

    def test_empty(self):
        self.assertEqual(parse_model_output("").status, "failed")


if __name__ == "__main__":
    unittest.main()
