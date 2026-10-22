"""Offline tests for the A7 authority layer. Synthetic narratives only, no network, no course data."""
import json
import tempfile
import unittest
from pathlib import Path

from src import a7_authority as a

BILL, FRAUD = a.BILLING, a.FRAUD
REPO = Path(__file__).resolve().parent.parent

MERCHANT = ("I bought a jacket online for $120 and returned it within the window. The store confirmed "
            "the return but the refund never posted to my card statement after six weeks.")
SCAM_WORD = ("The merchant charged me twice for one dinner and this is a scam. I paid with my card and "
             "only signed one receipt, so one of the two charges should be removed from my statement.")
UNAUTH_NO_KEYWORD = ("My card shows four charges from an electronics store in another state that I never "
                     "made or allowed. I still have my card in my wallet and I want them removed.")
LEGAL = ("The issuer refused to correct a billing error on my statement, which I believe violates the Fair "
         "Credit Billing Act, and my attorney asked me to write to you about the payment I made.")


def reply(route, review=False, concern=False, rationale="r"):
    return json.dumps({"case_summary": "s", "stated_facts": ["f"], "unknowns": [],
                       "fraud_or_security_concern": concern, "recommended_routing": route,
                       "human_review_required": review, "rationale": rationale})


class Routing(unittest.TestCase):
    def test_agree_billing_no_review(self):
        p = a.decide(MERCHANT, model_text=reply(BILL))
        self.assertEqual((p["default_queue"], p["route_basis"], p["mode"]), (BILL, "rule_and_model_agree", "normal"))
        self.assertFalse(p["human_review"]["required"])
        self.assertIsNone(p["deescalation_proposal"])

    def test_floor_holds_when_model_says_billing(self):
        p = a.decide(SCAM_WORD, model_text=reply(BILL, rationale="customer made the purchase"))
        self.assertEqual(p["default_queue"], FRAUD)
        self.assertEqual(p["route_basis"], "fraud_floor_held")
        self.assertEqual(p["deescalation_proposal"]["proposed_queue"], BILL)
        self.assertIn("routing_conflict", p["human_review"]["reasons"])
        self.assertIn("fraud_floor", p["human_review"]["reasons"])
        self.assertEqual(p["rule"]["matched_fraud_terms"], ["scam"])

    def test_model_may_escalate_alone(self):
        p = a.decide(UNAUTH_NO_KEYWORD, model_text=reply(FRAUD, concern=True))
        self.assertEqual((p["default_queue"], p["route_basis"]), (FRAUD, "model_escalation"))
        self.assertIsNone(p["deescalation_proposal"])
        self.assertTrue(p["human_review"]["required"])

    def test_agree_fraud(self):
        p = a.decide(SCAM_WORD, model_text=reply(FRAUD))
        self.assertEqual((p["default_queue"], p["route_basis"]), (FRAUD, "rule_and_model_agree"))
        self.assertNotIn("routing_conflict", p["human_review"]["reasons"])


class Review(unittest.TestCase):
    def test_model_review_flag_is_advisory_only(self):
        p = a.decide(MERCHANT, model_text=reply(BILL, review=True, concern=True))
        self.assertFalse(p["human_review"]["required"])
        self.assertTrue(p["model"]["review_advisory"])

    def test_model_cannot_clear_deterministic_review(self):
        p = a.decide(LEGAL, model_text=reply(BILL, review=False))
        self.assertEqual(p["human_review"]["reasons"], ["legal_or_regulatory_language"])

    def test_fee_and_short_narrative(self):
        self.assertIn("fee_dispute", a.decide("They charged a late fee I do not owe on my card.",
                                              model_text=reply(BILL))["human_review"]["reasons"])
        self.assertIn("short_narrative", a.decide("Wrong amount charged.", model_text=reply(BILL))["human_review"]["reasons"])


class Degradation(unittest.TestCase):
    def test_unparseable_reply(self):
        p = a.decide(MERCHANT, model_text="Sure! I think it is a billing issue.")
        self.assertEqual((p["mode"], p["default_queue"], p["route_basis"]), ("degraded", BILL, "rule_only_degraded"))
        self.assertIn("model_output_unusable", p["human_review"]["reasons"])

    def test_timeout_via_run_case_never_raises(self):
        def boom(cfg, messages):
            raise TimeoutError("timed out")
        p = a.run_case({"MODEL_ID": "m"}, SCAM_WORD, call=boom)
        self.assertEqual((p["mode"], p["default_queue"]), ("degraded", FRAUD))
        self.assertIn("model_unavailable", p["human_review"]["reasons"])
        self.assertEqual(a.validate_packet(p), [])

    def test_run_case_passes_timeout_and_meta(self):
        seen = {}

        def ok(cfg, messages):
            seen["timeout"] = cfg["TIMEOUT_S"]
            seen["user"] = messages[-1]["content"]
            return {"text": reply(BILL), "latency_s": 1.5, "model": "m"}
        p = a.run_case({"MODEL_ID": "m"}, MERCHANT, case_id="X1", call=ok)
        self.assertEqual(seen["timeout"], str(a.DEFAULT_MODEL_TIMEOUT_S))
        self.assertIn("refund never posted", seen["user"])
        self.assertEqual((p["model"]["latency_s"], p["model"]["prompt_version"]), (1.5, "prompted-v1"))

    def test_kill_switch(self):
        p = a.run_case({"MODEL_ID": "m", "MODEL_ENABLED": "false"}, MERCHANT,
                       call=lambda c, m: self.fail("model must not be called"))
        self.assertEqual(p["model"]["status"], "disabled")
        self.assertIn("model_disabled", p["human_review"]["reasons"])


class EmployeeAuthority(unittest.TestCase):
    def setUp(self):
        self.floor = a.decide(SCAM_WORD, model_text=reply(BILL))
        self.bill = a.decide(MERCHANT, model_text=reply(BILL))

    def test_deescalation_needs_reason_and_attestation(self):
        with self.assertRaises(a.AuthorityError):
            a.apply_employee_decision(self.floor, BILL, "emp1")
        with self.assertRaises(a.AuthorityError):
            a.apply_employee_decision(self.floor, BILL, "emp1", reason_code="keyword_used_figuratively")
        with self.assertRaises(a.AuthorityError):
            a.apply_employee_decision(self.floor, BILL, "emp1", reason_code="looks_fine",
                                      attest_customer_authorised=True)
        d = a.apply_employee_decision(self.floor, BILL, "emp1", reason_code="keyword_used_figuratively",
                                      attest_customer_authorised=True)
        self.assertEqual((d["action"], d["model_proposal_followed"]), ("deescalated", True))
        self.assertEqual(d["attestation"], a.ATTESTATION_TEXT)

    def test_escalation_is_cheap_but_attributed(self):
        with self.assertRaises(a.AuthorityError):
            a.apply_employee_decision(self.bill, FRAUD, "")
        d = a.apply_employee_decision(self.bill, FRAUD, "emp2", reason_code="other_security_concern")
        self.assertEqual(d["action"], "escalated")

    def test_accept_default_and_reject_proposal(self):
        d = a.apply_employee_decision(self.floor, FRAUD, "emp3")
        self.assertEqual((d["action"], d["model_proposal_followed"]), ("accepted_default", False))


class PacketContract(unittest.TestCase):
    def test_invariants_and_hash(self):
        for text, r in ((MERCHANT, reply(BILL)), (SCAM_WORD, reply(BILL)), (UNAUTH_NO_KEYWORD, reply(FRAUD)),
                        (LEGAL, "not json")):
            p = a.decide(text, model_text=r)
            self.assertEqual(a.validate_packet(p), [])
        p = a.decide(SCAM_WORD, model_text=reply(BILL))
        p["default_queue"] = BILL                       # tampering: the floor was bypassed
        self.assertTrue(any("INVARIANT" in e for e in a.validate_packet(p)))

    def test_deterministic_and_no_narrative_stored(self):
        p1 = a.decide(SCAM_WORD, case_id="c", model_text=reply(BILL), now="t1")
        p2 = a.decide(SCAM_WORD, case_id="c", model_text=reply(BILL), now="t2")
        self.assertEqual(p1["packet_sha256"], p2["packet_sha256"])
        self.assertNotIn("only signed one receipt", json.dumps(p1))

    def test_audit_log_roundtrip(self):
        p = a.decide(SCAM_WORD, model_text=reply(BILL))
        d = a.apply_employee_decision(p, FRAUD, "emp4")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            a.append_audit(path, p)
            a.append_audit(path, d)
            lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[1]["packet_sha256"], lines[0]["packet_sha256"])

    def test_json_schema_if_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (repo is stdlib-only); validate_packet covers the contract")
        schema = json.loads((REPO / "schemas" / "a7_recommendation.schema.json").read_text(encoding="utf-8"))
        for text, r in ((MERCHANT, reply(BILL)), (SCAM_WORD, reply(BILL)), (LEGAL, "")):
            jsonschema.validate(a.decide(text, model_text=r), schema)

    def test_empty_narrative_refused(self):
        with self.assertRaises(ValueError):
            a.decide("   ", model_text=reply(BILL))


if __name__ == "__main__":
    unittest.main()
