"""Tests for the TE8 interim two-person check.  python -m unittest discover -s te8 -v"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interim_two_person_check as c  # noqa: E402

F, B = c.FRAUD_QUEUE, c.BILLING_QUEUE


def rec(case, note="", reviewer="r1", frm=F, to=B, canary=None):
    r = {"case_id": case, "reviewer_id": reviewer, "human_action": "override",
         "overrides": [{"from_queue": frm, "to_queue": to, "reviewer_id": reviewer,
                        "reason_code": "customer_authorized_transaction", "note": note}]}
    if canary:
        r["canary"] = {"canary_id": canary}
    return r


class Check(unittest.TestCase):
    def test_deescalation_without_second_person_is_a_breach(self):
        out = c.check([rec("2121887")])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["breach"])

    def test_named_second_person_passes(self):
        out = c.check([rec("2121887", note="second: Yufei")])
        self.assertFalse(out[0]["breach"])
        self.assertEqual(out[0]["second"], "Yufei")

    def test_reviewer_cannot_be_their_own_second_person(self):
        self.assertTrue(c.check([rec("2121887", note="second: R1", reviewer="r1")])[0]["breach"])

    def test_empty_name_is_a_breach(self):
        self.assertTrue(c.check([rec("2121887", note="second: ")])[0]["breach"])

    def test_escalation_into_fraud_is_not_judged(self):
        self.assertEqual(c.check([rec("1", frm=B, to=F)]), [])

    def test_canary_is_left_to_the_vigilance_control(self):
        self.assertEqual(c.check([rec("1", canary="CAN-01")]), [])

    def test_accept_without_override_is_not_judged(self):
        self.assertEqual(c.check([{"case_id": "1", "human_action": "accept", "overrides": []}]), [])


class Cli(unittest.TestCase):
    def run_cli(self, lines):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "audit.jsonl"
            p.write_text("".join(lines), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = c.main([str(p)])
            return code, out.getvalue(), err.getvalue()

    def test_exit_1_on_breach_and_no_narrative_printed(self):
        r = rec("2121887")
        r["narrative"] = "SHOULD NOT BE PRINTED"
        code, out, _ = self.run_cli([json.dumps(r) + "\n"])
        self.assertEqual(code, 1)
        self.assertIn("BREACH", out)
        self.assertNotIn("SHOULD NOT BE PRINTED", out)

    def test_exit_0_when_clean(self):
        code, _, _ = self.run_cli([json.dumps(rec("2121887", note="second: Emy")) + "\n"])
        self.assertEqual(code, 0)

    def test_exit_2_on_partial_line(self):
        code, _, err = self.run_cli(['{"case_id": "1", "overr'])
        self.assertEqual(code, 2)
        self.assertIn("reconcile", err)


if __name__ == "__main__":
    unittest.main()
