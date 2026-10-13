"""Offline tests for the A6 three-arm evaluation harness (no network, no course CSV needed).

Run: python -m unittest discover -s tests -v
"""
import copy
import json
import unittest

from src import a6_eval as E
from src import prompt as a4_prompt
from src.a6_prompts import DECISION_FIELDS, PROMPTS, build_messages
from src.parser import parse_model_output

BILLING, FRAUD = E.BILLING, E.FRAUD


def entry(cid, stratum, route, review_intake="Yes", review_course="Yes", crid=None):
    return {"case_id": cid, "course_record_id": crid or f"DEV-{cid}", "stratum": stratum,
            "narrative_sha256": "h" + cid,
            "ref": {"routing": route, "review_course": review_course, "review_intake": review_intake,
                    "review_post_outcome_only": "No", "fraud_indicator": "Yes"}}


def pred(route, review=True, status="ok", latency=10.0):
    return {"route": route, "review": review, "status": status, "latency": latency,
            "usage": {"prompt_tokens": 100}}


class PromptTests(unittest.TestCase):
    def test_base_arm_reproduces_a4_messages_exactly(self):
        text = "  I was charged twice for one purchase.  "
        self.assertEqual(build_messages("baseline-v1", text), a4_prompt.build_messages(text))

    def test_prompted_keeps_baseline_rules_and_schema(self):
        p = PROMPTS["prompted-v1"]
        self.assertIn("Do not infer facts", p)
        self.assertIn('"case_summary"', p)
        self.assertIn("not by the words used", p)

    def test_adapter_schema_is_decision_only(self):
        p = PROMPTS["adapter-v1"]
        for f in DECISION_FIELDS:
            self.assertIn(f'"{f}"', p)
        self.assertNotIn('"case_summary"', p)

    def test_parser_decision_subset(self):
        text = json.dumps({"fraud_or_security_concern": False, "recommended_routing": BILLING,
                           "human_review_required": True})
        self.assertEqual(parse_model_output(text, DECISION_FIELDS).status, "ok")
        self.assertEqual(parse_model_output(text).status, "invalid")


class StatsTests(unittest.TestCase):
    def test_mcnemar_exact_known_values(self):
        self.assertEqual(E.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(E.mcnemar_exact(5, 0), 0.0625)
        self.assertAlmostEqual(E.mcnemar_exact(10, 2), 2 * 79 / 4096)
        self.assertEqual(E.mcnemar_exact(3, 3), 1.0)

    def test_wilson_bounds(self):
        lo, hi = E.wilson(0, 10)
        self.assertAlmostEqual(lo, 0.0)
        lo, hi = E.wilson(10, 10)
        self.assertAlmostEqual(hi, 1.0)
        lo, hi = E.wilson(5, 10)
        self.assertTrue(0.2 < lo < 0.5 < hi < 0.8)


class RuleArmTests(unittest.TestCase):
    def test_keyword_rule(self):
        r = E.rule_decision("My card was stolen and used.")
        self.assertEqual(r["recommended_routing"], FRAUD)
        self.assertTrue(r["human_review_required"])

    def test_keyword_rule_lexicon_gap_is_documented(self):
        # The course rule matches "stolen" but not "stole": the comparator is the course
        # rule as found, not an improved one.
        self.assertEqual(E.rule_decision("Someone stole my card.")["recommended_routing"], BILLING)

    def test_fee_and_legal_trigger_review_only(self):
        r = E.rule_decision("They charged me a late fee I do not owe.")
        self.assertEqual((r["recommended_routing"], r["human_review_required"]), (BILLING, True))
        r = E.rule_decision("My attorney says this violates the law.")
        self.assertEqual((r["recommended_routing"], r["human_review_required"]), (BILLING, True))

    def test_plain_billing(self):
        r = E.rule_decision("I was charged twice for the same purchase.")
        self.assertEqual((r["recommended_routing"], r["human_review_required"]), (BILLING, False))


class ReferenceAndScoringTests(unittest.TestCase):
    ADJ = {"DEV-1": {"route": FRAUD, "review": "Yes"}, "DEV-2": {"route": None, "review": "No"}}

    def test_team_reference(self):
        self.assertEqual(E.team_route(entry("1", "A_x", BILLING), self.ADJ), (FRAUD, "adjudicated"))
        self.assertEqual(E.team_route(entry("2", "A_x", BILLING), self.ADJ), (None, "adjudicated"))
        self.assertEqual(E.team_route(entry("3", "A_x", BILLING), self.ADJ), (None, "missing"))
        self.assertEqual(E.team_route(entry("4", "B_x", FRAUD), self.ADJ), (FRAUD, "course"))

    def test_unusable_output_counts_as_wrong(self):
        es = [entry("4", "B_x", FRAUD), entry("5", "C_x", BILLING, "No", "No")]
        p = E.predictions({"4": {"parse_status": "invalid", "record": {"recommended_routing": FRAUD}},
                           "5": {"parse_status": "ok", "record": {"recommended_routing": BILLING,
                                                                  "human_review_required": False}}}, es)
        s = E.score_arm(p, es, {}, {"B_x": 1, "C_x": 1})
        self.assertEqual(s["usable"], 1)
        self.assertEqual(s["team_correct_ids"], ["5"])
        self.assertEqual(s["review_intake"]["specificity"], [1, 1])

    def test_disputed_cases_are_excluded_and_reported(self):
        es = [entry("1", "A_x", BILLING), entry("2", "A_x", BILLING)]
        p = {"1": pred(FRAUD), "2": pred(BILLING)}
        s = E.score_arm(p, es, self.ADJ, {"A_x": 2})
        self.assertEqual(s["team_decided"], 1)
        self.assertEqual(s["route_team"]["A"], [1, 1])
        self.assertEqual(s["disputed"], {"2": BILLING})

    def test_verdict_requires_significance_and_rule(self):
        rule = {"R1_alpha": 0.05, "R3_review_recall_floor": 0.95, "R4_usable_output_floor": 0.95,
                "R5_latency_ratio_max": 1.25}
        es = [entry(str(i), "B_x", FRAUD) for i in range(20)]
        ids = [e["case_id"] for e in es]

        def S(correct, latency=10.0):
            return {"team_correct_ids": correct, "review_intake": {"recall": [20, 20]},
                    "usable": 20, "n": 20, "latency_median": latency}
        arms = {"base": S(ids[:5]), "prompted": S(ids[:15], 11.0), "rule": S(ids[:10])}
        v = E.verdict("prompted", "base", arms, es, {}, rule)
        self.assertEqual((v["b_cand_only"], v["c_inc_only"]), (10, 0))
        self.assertTrue(v["adopt"])
        arms["rule"] = S(ids[:15])                    # ties the keyword rule -> rule is sufficient
        self.assertFalse(E.verdict("prompted", "base", arms, es, {}, rule)["checks"]["R2_beats_keyword_rule"])
        arms["prompted"] = S(ids[:7])                 # +2 cases only -> not significant
        self.assertFalse(E.verdict("prompted", "base", arms, es, {}, rule)["checks"]["R1_beats_incumbent"])
        arms["prompted"] = S(ids[:15], 20.0)          # twice as slow
        self.assertFalse(E.verdict("prompted", "base", arms, es, {}, rule)["checks"]["R5_latency_ratio"])


class AdjudicationAndAuditTests(unittest.TestCase):
    def test_earlier_file_takes_precedence_and_any_stratum_is_used(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        head = "course_record_id,team_route,route_status,team_review\n"
        team = os.path.join(d, "team.csv")
        audit = os.path.join(d, "audit.csv")
        open(team, "w").write(head + f"DEV-1,{BILLING},agreed,Yes\n")
        open(audit, "w").write(head + f"DEV-1,{FRAUD},single_labeller,No\nDEV-4,{BILLING},single_labeller,No\n")
        adj = E.load_adjudicated(team, audit)
        self.assertEqual(adj["DEV-1"]["route"], BILLING)              # team file wins
        self.assertEqual(E.team_route(entry("4", "B_x", FRAUD), adj), (BILLING, "adjudicated"))

    def test_discordant_cases_only(self):
        import os, tempfile
        es = [entry("1", "B_x", FRAUD), entry("2", "C_x", BILLING), entry("3", "A_x", BILLING)]
        m = {"eval": es}
        d = tempfile.mkdtemp()
        def write(arm, routes):
            path = os.path.join(d, arm + ".jsonl")
            with open(path, "w") as fh:
                fh.write(json.dumps({"type": "meta", "arm": arm}) + "\n")
                for cid, r in routes.items():
                    fh.write(json.dumps({"type": "case", "case_id": cid, "parse_status": "ok",
                                         "record": {"recommended_routing": r}}) + "\n")
            return path
        runs = [write("rule", {"1": FRAUD, "2": FRAUD, "3": FRAUD}),      # rule arm is ignored
                write("base", {"1": FRAUD, "2": BILLING, "3": BILLING}),
                write("prompted", {"1": BILLING, "2": BILLING, "3": FRAUD})]
        adj = {"DEV-3": {"route": BILLING, "review": "Yes", "source": "t"}}
        self.assertEqual([e["case_id"] for e in E.discordant_cases(m, runs, adj)], ["1"])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.m = json.loads(E.MANIFEST.read_text(encoding="utf-8"))

    def test_committed_manifest_is_disjoint_and_complete(self):
        self.assertEqual(E.check_manifest(self.m), [])
        self.assertEqual(len(self.m["eval"]), 81)
        self.assertEqual(len(self.m["tuning"]), 12)

    def test_training_pool_has_no_rule_error_case(self):
        # Every stratum-A case is in the evaluation slice, so neither few-shot examples nor an
        # adapter trained on this pool can see a "fraud words but Billing owner" case.
        self.assertEqual(sum(1 for e in self.m["eval"] if e["stratum"].startswith("A")),
                         self.m["scope_strata"]["A_fraudtext_billing_owner"])
        self.assertNotIn("A_fraudtext_billing_owner", self.m["train_pool"]["strata"])

    def test_overlap_is_detected(self):
        bad = copy.deepcopy(self.m)
        bad["train_pool"]["case_ids"].append(bad["eval"][0]["case_id"])
        self.assertTrue(any("eval and train" in p for p in E.check_manifest(bad)))

    def test_sft_target_uses_intake_review_not_course_review(self):
        row = {"fraud_indicator": "No", "routing_destination": BILLING,
               "human_review_required": "Yes", "review_ref_intake": "No"}
        self.assertEqual(E.sft_target(row)["human_review_required"], False)


if __name__ == "__main__":
    unittest.main()
