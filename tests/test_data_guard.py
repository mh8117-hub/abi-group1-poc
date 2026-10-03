"""Offline tests for src/data_guard.py (no course data, model or network needed).

Run: python -m unittest discover -s tests -v
"""
import csv
import tempfile
import unittest
from pathlib import Path

from src import data_guard as g


def row(**kw):
    r = {c: "" for c in g.EXPECTED_COLUMNS}
    r.update(course_record_id="DEV-00001", dataset_split="Development", case_id="1",
             product="Credit card", issue="Billing disputes",
             consumer_complaint_narrative="I was charged twice for one order.",
             timely_response="Yes", consumer_disputed="No", urgency_level="Medium",
             escalation_required="No", human_review_required="No", fraud_indicator="No",
             regulatory_indicator="No", label_status="Clear",
             routing_destination="Card Billing Disputes",
             reference_summary="Credit card billing dispute: customer reports a duplicate charge.",
             recommended_next_action="Review the disputed transaction and prior contacts.")
    r.update(kw)
    return r


def write(rows, name="ABI_Bank_Complaints_Development_8000.csv", columns=g.EXPECTED_COLUMNS):
    d = Path(tempfile.mkdtemp())
    p = d / name
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return str(p)


class SplitProtection(unittest.TestCase):
    def test_refuses_heldout_by_file_name(self):
        p = write([row()], name="ABI_Bank_Complaints_Heldout_Evaluation_1500.csv")
        with self.assertRaises(g.DataGuardError):
            g.load_development(p)

    def test_refuses_heldout_rows_inside_dev_file(self):
        p = write([row(), row(case_id="2", course_record_id="HEL-00007")])
        with self.assertRaises(g.DataGuardError):
            g.load_development(p)

    def test_excludes_dev_rows_duplicating_heldout_text(self):
        dup = "Charged TWICE   for one order!!"
        p = write([row(), row(case_id="2", consumer_complaint_narrative=dup)])
        hashes = {g.fingerprint("charged twice for one order")}   # case/spacing/punctuation differ
        rows, log = g.load_development(p, hashes)
        self.assertEqual([r["case_id"] for r in rows], ["1"])
        self.assertEqual(log["excluded_heldout_overlap"], ["2"])

    def test_hash_file_round_trip_never_stores_text(self):
        src = write([row(consumer_complaint_narrative="secret held-out text")],
                    name="ABI_Bank_Complaints_Heldout_Evaluation_1500.csv")
        out = Path(tempfile.mkdtemp()) / "h.txt"
        self.assertEqual(g.make_heldout_hashes(src, out), 1)
        self.assertNotIn("secret", out.read_text())
        self.assertIn(g.fingerprint("Secret held-out TEXT"), g.load_heldout_hashes(out))


class Schema(unittest.TestCase):
    def test_missing_column_fails(self):
        cols = tuple(c for c in g.EXPECTED_COLUMNS if c != "consumer_disputed")
        p = write([row()], columns=cols)
        with self.assertRaises(g.DataGuardError):
            g.load_development(p)

    def test_blank_consumer_disputed_is_allowed_but_bad_code_is_not(self):
        self.assertEqual(g.validate_schema([row(consumer_disputed="")], g.EXPECTED_COLUMNS), [])
        probs = g.validate_schema([row(human_review_required="Maybe")], g.EXPECTED_COLUMNS)
        self.assertTrue(any("human_review_required" in p for p in probs))

    def test_scope_filter(self):
        rows = [row(), row(case_id="2", issue="Late fee")]
        self.assertEqual(len(g.in_scope(rows)), 1)


class InputBoundary(unittest.TestCase):
    def test_model_input_is_narrative_only(self):
        payload = g.build_model_input(row())
        self.assertEqual(set(payload), {"consumer_complaint_narrative"})
        g.assert_input_boundary(payload, row())

    def test_target_post_outcome_and_surrogate_fields_rejected(self):
        for f in ("routing_destination", "consumer_disputed", "recommended_next_action",
                  "special_case_flags", "issue"):
            with self.assertRaises(g.DataGuardError, msg=f):
                g.assert_input_boundary({"consumer_complaint_narrative": "x", f: "y"})

    def test_undeclared_field_rejected(self):
        with self.assertRaises(g.DataGuardError):
            g.assert_input_boundary({"consumer_complaint_narrative": "x", "notes": "y"})

    def test_reference_summary_pasted_into_text_rejected(self):
        r = row()
        payload = {"consumer_complaint_narrative": r["consumer_complaint_narrative"] + " "
                   + r["reference_summary"]}
        with self.assertRaises(g.DataGuardError):
            g.assert_input_boundary(payload, r)


class LabelRisk(unittest.TestCase):
    def test_review_yes_only_from_post_outcome_dispute(self):
        a = g.annotate(row(human_review_required="Yes", consumer_disputed="Yes"))
        self.assertEqual(a["review_post_outcome_only"], "Yes")
        self.assertEqual(a["review_ref_intake"], "No")

    def test_review_from_urgency_is_not_post_outcome_only(self):
        a = g.annotate(row(human_review_required="Yes", consumer_disputed="Yes",
                           urgency_level="High"))
        self.assertEqual(a["review_post_outcome_only"], "No")
        self.assertEqual(a["review_ref_intake"], "Yes")

    def test_review_rule_check(self):
        ok = [row(), row(human_review_required="Yes", consumer_disputed="Yes")]
        self.assertEqual(g.review_rule_check(ok), 0)
        self.assertEqual(g.review_rule_check([row(human_review_required="Yes")]), 1)

    def test_lexicons_and_stratum(self):
        a = g.annotate(row(consumer_complaint_narrative="This is illegal under the FCBA; "
                           "fraudulent charge", fraud_indicator="Yes"))
        self.assertEqual((a["fraud_keyword_hit"], a["legal_language_hit"]), ("Yes", "Yes"))
        self.assertEqual(a["stratum"], "A_fraudtext_billing_owner")
        self.assertEqual(g.annotate(row(consumer_complaint_narrative="an issue"))
                         ["legal_language_hit"], "No")   # 'issue' must not match 'sue'


if __name__ == "__main__":
    unittest.main()
