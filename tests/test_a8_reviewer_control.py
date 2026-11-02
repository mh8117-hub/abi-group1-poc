"""Offline tests for the A8 reviewer de-escalation control. Synthetic narratives, no network."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from src import a7_authority as a7
from src import a8_reviewer_control as c
from src import data_guard as g

BILL, FRAUD = a7.BILLING, a7.FRAUD
CODE = "customer_made_or_allowed_transaction"


def reply(route, concern=False):
    return json.dumps({"case_summary": "s", "stated_facts": ["f"], "unknowns": [],
                       "fraud_or_security_concern": concern, "recommended_routing": route,
                       "human_review_required": True, "rationale": "r"})


def proposal(i, extra=""):
    """A fraud-floor case on which the model proposes Billing (keyword 'scam', merchant dispute)."""
    text = (f"Case {i}. The restaurant charged my card twice for one dinner and this is a scam. "
            f"I signed one receipt only and want the second charge removed. {extra}")
    return a7.decide(text, case_id=f"S{i:03d}", model_text=reply(BILL), now="t"), text


def canary_spec(packet):
    return {"case_id": packet["case_ref"]["case_id"],
            "narrative_sha256": packet["case_ref"]["narrative_sha256"],
            "reference_queue": FRAUD}


class Base(unittest.TestCase):
    def setUp(self):
        self.real = [proposal(i)[0] for i in range(1, 31)]
        self.can, _ = proposal(900, "Someone else also used my card for a charge I never made.")
        self.ctl = c.ReviewerControl({self.can["case_ref"]["case_id"]: canary_spec(self.can)})

    def accept(self, p, rid="rev1", **kw):
        kw.setdefault("dwell_s", 90)
        kw.setdefault("full_text_shown", True)
        return self.ctl.record_decision(p, rid, BILL, reason_code=CODE, attest=True, **kw)

    def decline(self, p, rid="rev1"):
        return self.ctl.record_decision(p, rid, FRAUD, dwell_s=90)


class Suspension(Base):
    def test_canary_acceptance_suspends_at_once_and_is_not_routed(self):
        rec = self.accept(self.can)
        self.assertTrue(rec["canary"])
        self.assertFalse(rec["routed"])
        self.assertFalse(rec["executed"])
        self.assertEqual(self.ctl.status("rev1"), c.SUSPENDED)

    def test_suspended_reviewer_cannot_deescalate_but_can_decline(self):
        self.accept(self.can)
        with self.assertRaises(c.ControlRefusal):
            self.accept(self.real[0])
        rec = self.decline(self.real[1])
        self.assertEqual(rec["final_queue"], FRAUD)
        self.assertIn("deescalation_refused", [e["event"] for e in self.ctl.events])

    def test_suspension_is_per_reviewer(self):
        self.accept(self.can, rid="rev1")
        self.accept(self.real[0], rid="rev2")          # rev2 keeps authority
        self.assertEqual(self.ctl.status("rev2"), c.ACTIVE)

    def test_refused_even_without_model_proposal(self):
        self.accept(self.can)
        agree = a7.decide("A stolen card was used for three charges.", case_id="X",
                          model_text=reply(FRAUD, True), now="t")
        with self.assertRaises(c.ControlRefusal):
            self.ctl.record_decision(agree, "rev1", BILL, reason_code=CODE, attest=True,
                                     full_text_shown=True)

    def test_careful_reviewer_is_never_suspended(self):
        for p in self.real[:10]:
            self.accept(p)
        self.decline(self.can)
        self.assertEqual(self.ctl.status("rev1"), c.ACTIVE)
        self.assertEqual(self.ctl.summary("rev1")["canaries_passed"], 1)


class LookbackAndResumption(Base):
    def test_lookback_lists_real_deescalations_since_last_passed_canary(self):
        self.accept(self.real[0])
        self.decline(self.can)                           # passed: covers real[0]
        can2, _ = proposal(901, "A card I never had was opened in my name.")
        self.ctl.canaries[can2["case_ref"]["case_id"]] = canary_spec(can2)
        self.accept(self.real[1]); self.accept(self.real[2])
        self.accept(can2)                                # failed
        self.assertEqual(self.ctl.lookback("rev1"), ["S002", "S003"])

    def test_reinstatement_needs_supervisor_closed_lookback_and_recert(self):
        self.accept(self.real[0]); self.accept(self.can)
        with self.assertRaises(c.ControlRefusal):
            self.ctl.reinstate("rev1", "rev1")           # not the reviewer
        with self.assertRaises(c.ControlRefusal):
            self.ctl.reinstate("rev1", "sup1")           # lookback open
        with self.assertRaises(c.ControlRefusal):
            self.ctl.close_lookback("rev1", "sup1", {})  # incomplete
        self.ctl.close_lookback("rev1", "sup1", {"S001": FRAUD})
        with self.assertRaises(c.ControlRefusal):
            self.ctl.reinstate("rev1", "sup1")           # no recert yet
        for k in (902, 903):
            ck, _ = proposal(k, "The charge was made by someone who stole my card number.")
            self.ctl.canaries[ck["case_ref"]["case_id"]] = canary_spec(ck)
            self.decline(ck)
        ev = self.ctl.reinstate("rev1", "sup1")
        self.assertEqual(ev["event"], "reviewer_reinstated")
        self.assertEqual(self.ctl.status("rev1"), c.ACTIVE)
        self.assertEqual(self.ctl.lookback("rev1"), ["S001"])   # history kept

    def test_failed_recert_attempt_resets_count(self):
        self.accept(self.can)
        ck, _ = proposal(904, "My card was used by someone else.")
        self.ctl.canaries[ck["case_ref"]["case_id"]] = canary_spec(ck)
        with self.assertRaises(c.ControlRefusal):
            self.accept(ck)
        self.assertEqual(self.ctl.r["rev1"]["recert_passes"], 0)


class LeadingIndicatorsAndPrevention(Base):
    def test_accept_streak_puts_reviewer_on_watch_not_suspension(self):
        for p in self.real[:c.STREAK_WATCH]:
            self.accept(p)
        self.assertEqual(self.ctl.status("rev1"), c.WATCH)

    def test_decline_resets_streak(self):
        for p in self.real[:c.STREAK_WATCH - 1]:
            self.accept(p)
        self.decline(self.real[c.STREAK_WATCH - 1])
        self.accept(self.real[c.STREAK_WATCH])
        self.assertEqual(self.ctl.status("rev1"), c.ACTIVE)

    def test_fast_decisions_put_reviewer_on_watch(self):
        for p in self.real[:5]:
            self.accept(p, dwell_s=5)
        self.assertEqual(self.ctl.status("rev1"), c.WATCH)
        self.assertIn("median decision time", self.ctl.r["rev1"]["watch_reasons"][0])

    def test_watch_sends_spot_check_list(self):
        for p in self.real[:5]:
            self.accept(p, dwell_s=5)
        ev = [e for e in self.ctl.events if e["event"] == "reviewer_watch"][0]
        self.assertEqual(ev["spot_check"], ["S001", "S002", "S003", "S004", "S005"])

    def test_pool_used_up_needs_second_person_even_for_active_reviewer(self):
        self.ctl.set_supply("rev1", False)
        self.assertEqual(self.ctl.status("rev1"), c.ACTIVE)
        with self.assertRaises(c.ControlRefusal) as cm:
            self.accept(self.real[0])
        self.assertEqual(cm.exception.reason, "two_person_required")
        with self.assertRaises(c.ControlRefusal):
            self.accept(self.real[0], approved_by="rev1")            # not oneself
        rec = self.accept(self.real[0], approved_by="rev2")
        self.assertEqual(rec["second_approver"], "rev2")
        self.decline(self.real[1])                                 # staying in Fraud needs no one

    def test_suspended_person_cannot_be_second_approver(self):
        self.accept(self.can, rid="rev2")                          # rev2 suspended
        self.ctl.set_supply("rev1", False)
        with self.assertRaises(c.ControlRefusal) as cm:
            self.accept(self.real[0], approved_by="rev2")
        self.assertEqual(cm.exception.reason, "invalid_second_approver")

    def test_passing_a_canary_clears_watch(self):
        for p in self.real[:5]:
            self.accept(p, dwell_s=5)
        self.decline(self.can)
        self.assertEqual(self.ctl.status("rev1"), c.ACTIVE)

    def test_deescalation_refused_when_complaint_truncated_on_screen(self):
        with self.assertRaises(c.ControlRefusal):
            self.accept(self.real[0], full_text_shown=False)
        self.decline(self.real[1])                       # staying in Fraud is always allowed

    def test_full_text_must_be_confirmed_explicitly(self):
        with self.assertRaises(c.ControlRefusal) as cm:   # caller forgot the argument
            self.ctl.record_decision(self.real[0], "rev1", BILL, reason_code=CODE, attest=True)
        self.assertEqual(cm.exception.reason, "full_text_not_confirmed")

    def test_a7_rules_still_apply_through_the_control(self):
        with self.assertRaises(a7.AuthorityError):
            self.ctl.record_decision(self.real[0], "rev1", BILL, reason_code="other", attest=True,
                                     full_text_shown=True)
        with self.assertRaises(a7.AuthorityError):
            self.ctl.record_decision(self.real[0], "rev1", BILL, reason_code=CODE, attest=False,
                                     full_text_shown=True)


class QueueQualificationAndStop(Base):
    def test_queue_hides_canaries_and_seeds_one_per_block(self):
        q = c.ReviewQueue(self.real[:18], [self.can], self.ctl, "rev1", random.Random(1))
        items = list(q)
        self.assertEqual(sum(f for _, f in items), 1)
        first = [i for i, (_, f) in enumerate(items) if f][0]
        self.assertLess(first, c.CANARY_EVERY)
        self.assertEqual(set(items[first][0]), set(self.real[0]))   # same keys: nothing marks it
        self.assertNotIn("canary", json.dumps(items[first][0]))

    def test_last_canary_is_still_tested_and_supply_ends_after_it(self):
        q = c.ReviewQueue(self.real[:12], [self.can], self.ctl, "rev1", random.Random(3))
        seen_canary = False
        for pk, is_canary in q:
            if is_canary:
                self.assertTrue(self.ctl.supply["rev1"])          # the canary itself can be failed
                self.accept(pk)
                seen_canary = True
                break
        self.assertTrue(seen_canary)
        self.assertEqual(self.ctl.status("rev1"), c.SUSPENDED)

    def test_supply_ends_when_next_real_item_is_served(self):
        q = c.ReviewQueue(self.real[:12], [self.can], self.ctl, "rev1", random.Random(3))
        after = False
        for pk, is_canary in q:
            if after:
                self.assertFalse(self.ctl.supply["rev1"])
                break
            after = is_canary

    def test_qualify_drops_canary_the_model_no_longer_proposes(self):
        agree = a7.decide("The thief used my stolen card.", case_id="Q", model_text=reply(FRAUD, True), now="t")
        pool = {"canaries": [canary_spec(self.can), canary_spec(agree)]}
        q = c.qualify(pool, {self.can["case_ref"]["case_id"]: self.can, "Q": agree})
        self.assertEqual(list(q["qualified"]), [self.can["case_ref"]["case_id"]])

    def test_no_qualified_canary_stops_proposals(self):
        st = c.ReviewerControl({}).system_status()
        self.assertFalse(st["proposals_allowed"])

    def test_two_suspended_reviewers_stop_proposals(self):
        self.accept(self.can, rid="a")
        self.accept(self.can, rid="b")
        self.assertFalse(self.ctl.system_status()["proposals_allowed"])

    def test_feature_stop_is_enforced_at_view_and_decision(self):
        self.assertTrue(self.ctl.view(self.real[0], "rev3")["show_proposal"])
        self.accept(self.can, rid="a")
        self.accept(self.can, rid="b")                   # stop: 2 reviewers suspended
        v = self.ctl.view(self.real[0], "rev3")
        self.assertFalse(v["show_proposal"])
        self.assertIn("proposal_withheld", [e["event"] for e in self.ctl.events])
        with self.assertRaises(c.ControlRefusal) as cm:  # direct call to the decision entry
            self.accept(self.real[0], rid="rev3")
        self.assertEqual(cm.exception.reason, "two_person_required")
        self.accept(self.real[0], rid="rev3", approved_by="rev4")
        self.decline(self.real[1], rid="rev3")

    def test_audit_log_has_events_and_no_narrative(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.jsonl"
            self.ctl.audit_path = path
            p, text = proposal(950, "Unique sentence that must never reach the log.")
            self.accept(p); self.accept(self.can)
            body = path.read_text(encoding="utf-8")
            self.assertIn("reviewer_suspended", body)
            self.assertNotIn("Unique sentence", body)


class PoolAndMath(unittest.TestCase):
    def test_pool_fingerprints_match_committed_sample(self):
        import csv
        with open(c.REPO_ROOT / "data" / "dev_sample_81.csv", encoding="utf-8-sig") as fh:
            text = {r["case_id"]: r["consumer_complaint_narrative"] for r in csv.DictReader(fh)}
        for s in c.load_pool()["canaries"]:
            n = text[s["case_id"]]
            self.assertEqual(g.fingerprint(n), s["narrative_sha256"])
            self.assertEqual(n.find(s["evidence_quote"]), s["evidence_char_offset"])
            self.assertEqual(s["visible_on_tw7_screen"],
                             s["evidence_char_offset"] + len(s["evidence_quote"]) <= c.SCREEN_CHARS)

    def test_openwebui_export_of_committed_live_run(self):
        run = c.REPO_ROOT / "runs" / "20261102T145412Z_a8_drill_live.jsonl"
        with tempfile.TemporaryDirectory() as d:
            out = c.export_openwebui(run, Path(d) / "x.json")
            chats = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(chats), 6)
        logged = [json.loads(x) for x in run.read_text(encoding="utf-8").splitlines()]
        raw = {r["case_id"]: r["raw_text"] for r in logged if r["type"] == "model_reply"}
        for ch in chats:
            u, b = ch["chat"]["messages"]
            self.assertIn(b["content"], raw.values())          # reply unedited
            self.assertTrue(u["content"].startswith("Complaint:"))
        self.assertEqual(sum("| canary |" in ch["title"] for ch in chats), 2)

    def test_detection_math(self):
        m = c.detection_math(5 / 34, 10)
        self.assertEqual(m["streak_rule"]["accepts_in_a_row_needed"], 19)
        self.assertLessEqual(m["streak_rule"]["false_alarm_for_careful_reviewer"], 0.05)
        self.assertEqual(m["canary_rule"]["expected_real_proposals_before_first_canary"], 4.5)


if __name__ == "__main__":
    unittest.main()
