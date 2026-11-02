"""Assignment 8 candidate: reviewer de-escalation control (Mingyu He). Not merged.

Failure it targets (A8 worksheet)
  Under the TW7 policy every case reaches a named reviewer, and on about 14% of in-scope cases
  (stratum-weighted; 34 of the 81 oversampled A7 replay cases, 42%) the fraud floor holds a case in
  Card Fraud & Security while the model proposes Billing. A reviewer who accepts those proposals by habit ("rubber-stamping")
  moves genuine unauthorised-use cases out of Fraud & Security. The A7 controls (fixed reason code
  + attestation) prove that a person clicked, not that the person evaluated. On the A7 replay the
  model's proposal is wrong on 5 of 34 (3 Fraud & Security; 2 the audit could not decide because
  the narrative does not say whether the charges were authorised, which the floor keeps).

What this module adds on top of src.a7_authority (it never weakens an A7 rule)
  C1 Seeded known-answer proposals (canaries). Adjudicated Fraud & Security cases on which the
     LIVE model proposes Billing are mixed into each reviewer's proposals, one per block of
     CANARY_EVERY. The reviewer sees the live packet; which items are canaries is held here only.
  C2 Suspension (authority boundary). A reviewer who de-escalates a canary loses de-escalation
     authority at once. Their later de-escalations are refused and the case stays in Card Fraud &
     Security for a second reviewer. Declining a proposal is always allowed.
  C3 Lookback. On suspension, every real de-escalation by that reviewer since their last passed
     canary is listed for re-review by a second person.
  C4 Resumption. Only a named supervisor (not the reviewer) can reinstate, and only after the
     lookback is closed and the reviewer has declined RECERT_CANARIES canaries since suspension.
  C5 Leading indicators -> WATCH, never suspension on their own: STREAK_WATCH accepted proposals
     in a row, or a median decision time under MIN_DWELL_S. WATCH doubles the canary rate and sends
     the supervisor the de-escalations since the last passed canary for a spot check. Passing a
     canary clears WATCH.
  C6 Preventive check. A de-escalation is refused unless the caller states explicitly that the whole
     complaint was on screen (full_text_shown=True; default refuses). The TW7 screen prints
     narrative[:700]; 27 of the 34 A7 proposals are longer.
  C7 Untested -> two people. When no canary is left to test a reviewer (pool used up for their
     queue, or none qualified), ANY reviewer's de-escalation needs approved_by = a second named,
     non-suspended person; a single-reviewer de-escalation is refused.
  C8 Feature stop, enforced. When no canary is qualified for the current model + prompt, >= 2
     reviewers are suspended, or >= 25% of canaries were failed (at least 4 seen): view() withholds
     the model's proposal from the screen (strict floor, A7: 41/74) and record_decision() refuses
     any single-reviewer de-escalation, until the owner resumes.

A suspension is a precaution, not a verdict: one accepted canary shows one wrong de-escalation under
the current reference label (DEV-06595's is single-labeller), not that the reviewer rubber-stamps.

Every control event is appended to a JSONL audit log. No narrative text is written.

  python -m unittest tests.test_a8_reviewer_control -v
  python -m src.a8_reviewer_control math                       # detection arithmetic, no data
  python -m src.a8_reviewer_control drill --adjudicated P3.csv data/a6_audit_labels.csv   # replay
  python -m src.a8_reviewer_control drill --live --adjudicated ...                        # DGX
  python -m src.a8_reviewer_control export-openwebui runs/<run_id>_a8_drill_live.jsonl     # WebUI import
"""
import argparse
import csv
import json
import math
import random
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src import a7_authority as a7
from src import data_guard as g

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_VERSION = "a8-reviewer-control-v2"
BILLING, FRAUD = a7.BILLING, a7.FRAUD
POOL_PATH = REPO_ROOT / "config" / "a8_canary_pool.json"
A6_RUN = REPO_ROOT / "runs" / "20261013T122014Z_a6_prompted_eval.jsonl"

# Policy values: candidates for Team Exercise 8, not validated on real reviewers.
CANARY_EVERY = 10        # one canary per block of 10 proposals shown to a reviewer
WATCH_EVERY = 5          # block size while a reviewer is on WATCH
STREAK_WATCH = 19        # P(careful reviewer accepts 19 in a row) = 0.853^19 < 0.05 (A7 replay: 29/34 right)
MIN_DWELL_S = 30.0       # median seconds per de-escalation, over the last 10 (needs >= 5)
RECERT_CANARIES = 2
SCREEN_CHARS = 700       # TW7 intake_app.show() prints narrative[:700]
SUSPENDED_STOP, FAIL_RATE_STOP, FAIL_RATE_MIN_SEEN = 2, 0.25, 4

ACTIVE, WATCH, SUSPENDED = "active", "watch", "suspended"


class ControlRefusal(a7.AuthorityError):
    """A decision the reviewer control does not allow. The case stays where it is."""

    def __init__(self, msg, reason="refused"):
        super().__init__(msg)
        self.reason = reason


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_deescalation(packet: dict, final_queue: str) -> bool:
    return packet["default_queue"] == FRAUD and final_queue == BILLING


# --------------------------------------------------------------------------- canary pool (C1)
def load_pool(path=POOL_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def qualify(pool: dict, packets: dict) -> dict:
    """A canary is usable only if the CURRENT model proposes de-escalation on it.

    packets: case_id -> live (or replayed) packet. Returns case_id -> spec for qualified canaries,
    plus a report. A canary the model now routes to Fraud shows the reviewer no wrong proposal,
    so it tests nothing and is dropped until the next qualification run."""
    qualified, report = {}, []
    for c in pool["canaries"]:
        p = packets.get(c["case_id"])
        ok = (p is not None and p["case_ref"]["narrative_sha256"] == c["narrative_sha256"]
              and p["route_basis"] == "fraud_floor_held")
        report.append({"case_id": c["case_id"], "course_record_id": c.get("course_record_id"),
                       "qualified": ok, "route_basis": p and p["route_basis"],
                       "model_status": p and p["model"]["status"],
                       "model_id": p and p["model"]["model_id"],
                       "prompt_sha256": p and p["model"]["prompt_sha256"]})
        if ok:
            qualified[c["case_id"]] = c
    return {"qualified": qualified, "report": report}


class ReviewQueue:
    """Proposals for ONE reviewer, with canaries mixed in: exactly one canary per block, at a
    uniformly random position. Block size shrinks to WATCH_EVERY while the reviewer is on WATCH.
    Yields (packet, is_canary). The packet is unchanged: nothing in it marks a canary."""

    def __init__(self, real_packets, canary_packets, control, reviewer_id, rng):
        self.real, self.canaries = list(real_packets), list(canary_packets)
        self.control, self.rid, self.rng = control, reviewer_id, rng
        self.served_canaries = 0
        self.exhausted_after = None      # real proposals served when the pool ran out

    def __iter__(self):
        real_i = 0
        self.control.set_supply(self.rid, bool(self.canaries))
        if not self.canaries:
            self.exhausted_after = 0
        while real_i < len(self.real):
            size = WATCH_EVERY if self.control.status(self.rid) == WATCH else self.control.every
            pos = self.rng.randrange(size)
            for k in range(size):
                if k == pos and self.canaries:
                    self.served_canaries += 1
                    yield self.canaries.pop(0), True
                elif real_i < len(self.real):
                    if not self.canaries and self.exhausted_after is None:
                        # the last canary has been decided: from here no canary can test this reviewer
                        self.exhausted_after = real_i
                        self.control.set_supply(self.rid, False)
                    yield self.real[real_i], False
                    real_i += 1
                if real_i >= len(self.real):
                    break


# --------------------------------------------------------------------------- the control (C2-C8)
class ReviewerControl:
    def __init__(self, canaries: dict, audit_path=None, every=CANARY_EVERY, clock=_now):
        self.canaries = dict(canaries)   # case_id -> spec (qualified for this model + prompt)
        self.audit_path, self.every, self.clock = audit_path, every, clock
        self.r = {}
        self.events = []
        self.supply = {}                 # reviewer -> a canary is still available to their queue

    def set_supply(self, rid, available: bool):
        self.supply[rid] = bool(available)

    # ---- state
    def _st(self, rid):
        if rid not in self.r:
            self.r[rid] = {"status": ACTIVE, "proposals": 0, "accepted": 0, "declined": 0,
                           "streak": 0, "dwell": [], "canaries_seen": 0, "canaries_failed": 0,
                           "canaries_passed": 0, "deesc": [], "last_pass": 0, "lookback": None,
                           "lookback_closed": False, "recert_passes": 0, "watch_reasons": [],
                           "spot_check": []}
        return self.r[rid]

    def status(self, rid):
        return self._st(rid)["status"]

    def _event(self, kind, rid, **kw):
        ev = {"type": "control_event", "control_version": CONTROL_VERSION, "event": kind,
              "reviewer_id": rid, "at": self.clock(), **kw}
        self.events.append(ev)
        if self.audit_path:
            a7.append_audit(self.audit_path, ev)
        return ev

    def _is_canary(self, packet):
        c = self.canaries.get(packet["case_ref"]["case_id"])
        return c is not None and c["narrative_sha256"] == packet["case_ref"]["narrative_sha256"]

    # ---- C8: the proposal entry point. The screen asks before it shows a model proposal.
    def view(self, packet, reviewer_id=None):
        st_ = self.system_status()
        show = packet.get("deescalation_proposal") is not None and st_["proposals_allowed"]
        if packet.get("deescalation_proposal") is not None and not show:
            self._event("proposal_withheld", reviewer_id, case_id=packet["case_ref"]["case_id"],
                        stop_reasons=st_["stop_reasons"])
        return {"packet": packet, "show_proposal": show,
                "notice": None if show or packet.get("deescalation_proposal") is None else
                "model proposal withheld (feature stop): strict fraud floor"}

    def needs_two_people(self, rid):
        """C7/C8: True when this reviewer's de-escalation cannot be tested by a canary."""
        return (not self.supply.get(rid, bool(self.canaries))) or not self.system_status()["proposals_allowed"]

    def _refuse(self, rid, cref, canary, reason, msg):
        self._event("deescalation_refused", rid, case_id=cref, canary=canary, why=reason)
        raise ControlRefusal(msg, reason)

    # ---- C2/C5/C6/C7/C8: every reviewer decision on a packet goes through here
    def record_decision(self, packet, reviewer_id, final_queue, *, reason_code=None,
                        attest=False, dwell_s=None, full_text_shown=None, approved_by=None,
                        note=""):
        rid = str(reviewer_id or "").strip()
        s = self._st(rid)
        canary = self._is_canary(packet)
        proposal = packet.get("deescalation_proposal") is not None
        cref = packet["case_ref"]["case_id"]
        deesc = is_deescalation(packet, final_queue)
        second = str(approved_by or "").strip() or None

        if deesc and s["status"] == SUSPENDED:
            if canary:
                s["recert_passes"] = 0
            self._refuse(rid, cref, canary, "suspended",
                         f"de-escalation authority of {rid} is suspended: the case stays in "
                         f"{FRAUD} for a second reviewer")
        if deesc and full_text_shown is not True:
            self._refuse(rid, cref, canary, "full_text_not_confirmed",
                         "de-escalation needs the caller to confirm the whole complaint was on screen "
                         f"(full_text_shown=True; the TW7 screen shows the first {SCREEN_CHARS} characters)")
        if deesc and self.needs_two_people(rid):
            if second is None:
                self._refuse(rid, cref, canary, "two_person_required",
                             f"no canary can test {rid}'s decision (pool used up or feature stopped): "
                             "a second named person must approve this de-escalation")
            if second == rid or self._st(second)["status"] == SUSPENDED:
                self._refuse(rid, cref, canary, "invalid_second_approver",
                             "the second approver must be another named person with de-escalation authority")
            self._event("two_person_deescalation", rid, case_id=cref, approved_by=second)

        record = a7.apply_employee_decision(packet, final_queue, rid, reason_code=reason_code,
                                            attest_customer_authorised=attest, note=note)
        record.update(control_version=CONTROL_VERSION, dwell_s=dwell_s, canary=canary,
                      second_approver=second if deesc else None, executed=False)
        if not proposal:
            return record

        s["proposals"] += 1
        if canary:
            s["canaries_seen"] += 1
            if deesc:
                s["canaries_failed"] += 1
                self._suspend(rid, cref)
                record["routed"] = False        # a canary never reaches a real queue
            else:
                s["canaries_passed"] += 1
                s["last_pass"] = len(s["deesc"])
                s["streak"] = 0
                if s["status"] == SUSPENDED:
                    s["recert_passes"] += 1
                elif s["status"] == WATCH:
                    s.update(status=ACTIVE, watch_reasons=[])
                self._event("canary_passed", rid, case_id=cref, status=s["status"],
                            recert_passes=s["recert_passes"])
            return record

        if deesc:
            s["accepted"] += 1
            s["streak"] += 1
            if dwell_s is not None:
                s["dwell"].append(float(dwell_s))
            s["deesc"].append({"case_id": cref, "packet_sha256": packet["packet_sha256"],
                               "at": record["decided_utc"]})
        else:
            s["declined"] += 1
            s["streak"] = 0
        self._check_watch(rid)
        return record

    def _check_watch(self, rid):
        s = self._st(rid)
        if s["status"] != ACTIVE:
            return
        why = []
        if s["streak"] >= STREAK_WATCH:
            why.append(f"{s['streak']} proposals accepted in a row")
        last = s["dwell"][-10:]
        if len(last) >= 5 and st.median(last) < MIN_DWELL_S:
            why.append(f"median decision time {st.median(last):.0f}s < {MIN_DWELL_S:.0f}s")
        if why:
            s["status"], s["watch_reasons"] = WATCH, why
            s["spot_check"] = [d["case_id"] for d in s["deesc"][s["last_pass"]:]]
            self._event("reviewer_watch", rid, reasons=why, canary_block=WATCH_EVERY,
                        spot_check=s["spot_check"], notify="fraud-operations supervisor")

    def _suspend(self, rid, canary_case):
        s = self._st(rid)
        s["status"], s["recert_passes"], s["lookback_closed"] = SUSPENDED, 0, False
        s["lookback"] = [d["case_id"] for d in s["deesc"][s["last_pass"]:]]
        self._event("canary_failed", rid, case_id=canary_case)
        self._event("reviewer_suspended", rid, lookback=s["lookback"],
                    action="cases in lookback go back to Card Fraud & Security review by a second person")

    # ---- C3/C4
    def lookback(self, rid):
        return list(self._st(rid)["lookback"] or [])

    def close_lookback(self, rid, supervisor_id, outcomes: dict):
        s = self._st(rid)
        if not str(supervisor_id or "").strip() or supervisor_id == rid:
            raise ControlRefusal("the lookback is closed by a named second person, not the reviewer")
        missing = sorted(set(s["lookback"] or []) - set(outcomes))
        if missing:
            raise ControlRefusal(f"lookback incomplete: {missing}")
        s["lookback_closed"] = True
        moved_back = sorted(c for c, q in outcomes.items() if q == FRAUD)
        return self._event("lookback_closed", rid, supervisor_id=supervisor_id,
                           reviewed=len(outcomes), returned_to_fraud=moved_back)

    def reinstate(self, rid, supervisor_id):
        s = self._st(rid)
        if s["status"] != SUSPENDED:
            raise ControlRefusal(f"{rid} is not suspended")
        if not str(supervisor_id or "").strip() or supervisor_id == rid:
            raise ControlRefusal("only a named supervisor other than the reviewer can reinstate")
        if not s["lookback_closed"]:
            raise ControlRefusal("lookback not closed")
        if s["recert_passes"] < RECERT_CANARIES:
            raise ControlRefusal(f"needs {RECERT_CANARIES} canaries declined since suspension "
                                 f"(has {s['recert_passes']})")
        s.update(status=ACTIVE, streak=0, watch_reasons=[], last_pass=len(s["deesc"]))
        return self._event("reviewer_reinstated", rid, supervisor_id=supervisor_id,
                           evidence={"recert_passes": s["recert_passes"], "lookback_closed": True})

    # ---- C8
    def system_status(self):
        seen = sum(s["canaries_seen"] for s in self.r.values())
        failed = sum(s["canaries_failed"] for s in self.r.values())
        suspended = sorted(k for k, s in self.r.items() if s["status"] == SUSPENDED)
        why = []
        if not self.canaries:
            why.append("no canary qualified for the current model and prompt")
        if len(suspended) >= SUSPENDED_STOP:
            why.append(f"{len(suspended)} reviewers suspended")
        if seen >= FAIL_RATE_MIN_SEEN and failed / seen >= FAIL_RATE_STOP:
            why.append(f"{failed}/{seen} canaries failed")
        return {"proposals_allowed": not why, "stop_reasons": why, "suspended": suspended,
                "canaries_seen": seen, "canaries_failed": failed,
                "if_stopped": "view() withholds model proposals and single-reviewer de-escalation is "
                              "refused (strict fraud floor) until the owner resumes"}

    def summary(self, rid):
        s = self._st(rid)
        return {k: s[k] for k in ("status", "proposals", "accepted", "declined", "canaries_seen",
                                  "canaries_failed", "canaries_passed", "lookback", "watch_reasons",
                                  "spot_check")}


# --------------------------------------------------------------------------- detection arithmetic
def detection_math(p_decline=5 / 34, every=CANARY_EVERY, alpha=0.05):
    """Why a canary, not an acceptance-rate threshold. p_decline: share of the model's proposals a
    careful reviewer should decline (A7 replay: 3 Fraud & Security + 2 disputed of 34)."""
    n_star = math.ceil(math.log(alpha) / math.log(1 - p_decline))
    real_before = (every - 1) / 2
    return {
        "p_decline": round(p_decline, 3),
        "streak_rule": {"accepts_in_a_row_needed": n_star,
                        "false_alarm_for_careful_reviewer": round((1 - p_decline) ** n_star, 3),
                        "wrong_deescalations_before_flag (total rubber-stamper)": round(n_star * p_decline, 2),
                        "detects_partial_rubber_stamping": False},
        "canary_rule": {"canary_every": every, "expected_real_proposals_before_first_canary": real_before,
                        "expected_wrong_deescalations_before_flag": round(real_before * p_decline, 2),
                        "false_alarm": "an accepted canary is a wrong de-escalation under the current reference "
                                       "label; it shows one error, not a habit, so suspension is a precaution",
                        "added_review_load_per_100_cases": round(100 * 0.142 / (every - 1), 2)}}


# --------------------------------------------------------------------------- drill (simulated reviewers)
def careful(packet, ref, i=0):
    """Simulated reviewer who reads the whole complaint: right on every proposal (A7 upper bound).
    Disputed or unknown references stay in Fraud & Security, as the floor requires."""
    if ref == BILLING:
        return BILLING, "customer_made_or_allowed_transaction", True, 95.0
    return FRAUD, None, False, 110.0


def rubber_stamp(packet, ref, i=0):
    """Simulated reviewer who accepts every proposal with the first reason code."""
    return BILLING, "customer_made_or_allowed_transaction", True, 6.0


LATE_ONSET_AFTER = 15


def late_rubber_stamp(packet, ref, i=0):
    """Careful for the first LATE_ONSET_AFTER items of the session, then rubber-stamps (fatigue)."""
    return careful(packet, ref) if i <= LATE_ONSET_AFTER else rubber_stamp(packet, ref)


REVIEWERS = {"careful": careful, "rubber_stamp": rubber_stamp, "late_rubber_stamp": late_rubber_stamp}


def run_session(real, canaries, refs, policy, rid, seed, control_on=True, audit_path=None):
    """One reviewer works through their queue. real/canaries: lists of packets; refs: case_id ->
    reference queue (None = disputed). Returns the outcome; nothing is routed (executed=False)."""
    rng = random.Random(seed)
    order = list(real)
    rng.shuffle(order)
    pool = [p for p, _ in canaries]
    rng.shuffle(pool)
    ctl = ReviewerControl({p["case_ref"]["case_id"]: c for p, c in canaries} if control_on else {},
                          audit_path=audit_path)
    q = ReviewQueue(order, pool if control_on else [], ctl, rid, rng)
    out = {"reviewer": policy, "control": control_on, "seed": seed, "items": 0, "real_deesc": [],
           "wrong_real_deesc": [], "refused": [], "refused_reasons": Counter(), "canaries": [],
           "suspended_at_item": None, "watch_at_item": None}
    for pk, is_canary in q:
        out["items"] += 1
        cid = pk["case_ref"]["case_id"]
        ref = FRAUD if is_canary else refs.get(cid)
        fq, code, attest, dwell = REVIEWERS[policy](pk, ref, out["items"])
        attempted = fq == BILLING
        try:
            if control_on:
                ctl.record_decision(pk, rid, fq, reason_code=code, attest=attest, dwell_s=dwell,
                                    full_text_shown=True)
            else:                                   # TW7/A7 as built: reason code + attestation only
                a7.apply_employee_decision(pk, fq, rid, reason_code=code, attest_customer_authorised=attest)
        except ControlRefusal as exc:
            out["refused"].append(cid)
            out["refused_reasons"][exc.reason] += 1
            fq = FRAUD                              # stays in Fraud & Security for a second person
        if is_canary:
            out["canaries"].append({"case_id": cid, "outcome": "failed" if fq == BILLING else
                                    "refused_attempt" if attempted else "passed"})
        elif fq == BILLING:
            out["real_deesc"].append(cid)
            if ref != BILLING:
                out["wrong_real_deesc"].append(cid)
        if out["suspended_at_item"] is None and ctl.status(rid) == SUSPENDED:
            out["suspended_at_item"] = out["items"]
        if out["watch_at_item"] is None and ctl.status(rid) == WATCH:
            out["watch_at_item"] = out["items"]
    out["lookback"] = ctl.lookback(rid)
    out["spot_check"] = list(ctl._st(rid)["spot_check"])
    out["wrong_not_in_lookback"] = sorted(set(out["wrong_real_deesc"]) - set(out["lookback"])
                                          - set(out["spot_check"]))
    out["pool_exhausted_after_real"] = q.exhausted_after
    out["refused_reasons"] = dict(out["refused_reasons"])
    out["status"] = ctl.status(rid)
    out["system"] = ctl.system_status()
    return out


def _refs(adjudicated):
    from src import a6_eval as ev
    manifest = json.loads((REPO_ROOT / "data" / "a6_manifest.json").read_text(encoding="utf-8"))
    adj = ev.load_adjudicated(*adjudicated)
    refs, src = {}, {}
    for e in manifest["eval"]:
        refs[e["case_id"]], src[e["case_id"]] = ev.team_route(e, adj)
    if not any(v == "adjudicated" and e["stratum"].startswith("A")
               for e in manifest["eval"] for v in [src[e["case_id"]]]):
        print("WARNING: no team P3 labels given: stratum-A references are missing and the simulated "
              "careful reviewer will keep those cases in Fraud & Security", file=sys.stderr)
    return refs, {e["case_id"]: e["course_record_id"] for e in manifest["eval"]}


def _narratives():
    with open(REPO_ROOT / "data" / "dev_sample_81.csv", encoding="utf-8-sig", newline="") as fh:
        return {r["case_id"]: r for r in csv.DictReader(fh)}


def replay_packets(run=A6_RUN):
    """Logged DGX replies (A6 run) through a7.decide: no network."""
    rows = _narratives()
    lines = [json.loads(x) for x in open(run, encoding="utf-8")]
    meta = next((x for x in lines if x.get("type") == "meta"), {})
    mm = {"model_id": f"replay:{meta.get('model_requested')}:{meta.get('run_id')}",
          "prompt_version": meta.get("prompt_version"), "prompt_sha256": meta.get("prompt_sha256")}
    out = {}
    for r in lines:
        if r.get("type") != "meta" and "case_id" in r:
            out[r["case_id"]] = a7.decide(rows[r["case_id"]]["consumer_complaint_narrative"],
                                          case_id=r["case_id"], model_text=r.get("raw_text"),
                                          model_meta=dict(mm, latency_s=r.get("latency_s")),
                                          now="replay")
    return out


def live_packets(case_ids, log_path, timeout=None):
    """DGX: one live call per case through a7.run_case (timeout, degradation, boundary unchanged)."""
    from src.config import load_config
    from src.a6_eval import git_state
    from src.a6_prompts import prompt_sha256
    cfg = load_config()
    if timeout:
        cfg["A7_MODEL_TIMEOUT_S"] = str(timeout)
    rows = _narratives()
    a7.append_audit(log_path, {"type": "meta", "control_version": CONTROL_VERSION,
                               "git_commit": git_state(), "endpoint": cfg["OPENWEBUI_BASE_URL"],
                               "model_requested": cfg["MODEL_ID"], "temperature": cfg["TEMPERATURE"],
                               "seed": cfg["SEED"], "prompt_version": "prompted-v1",
                               "prompt_sha256": prompt_sha256("prompted-v1"),
                               "layer_version": a7.LAYER_VERSION,
                               "model_timeout_s": float(cfg.get("A7_MODEL_TIMEOUT_S", a7.DEFAULT_MODEL_TIMEOUT_S)),
                               "started_utc": _now()})
    out = {}
    for i, cid in enumerate(case_ids, 1):
        payload = g.build_model_input(rows[cid])
        g.assert_input_boundary(payload, rows[cid])
        got = {}

        def keep(reply, messages):
            got.update(raw_text=reply.get("text"), model=reply.get("model"),
                       latency_s=reply.get("latency_s"), usage=reply.get("usage"),
                       user_msg_sha256=a7._text_sha(messages[1]["content"]))
        pk = a7.run_case(cfg, payload["consumer_complaint_narrative"], case_id=cid, on_reply=keep)
        a7.append_audit(log_path, dict(pk, type="packet", problems=a7.validate_packet(pk)))
        if got:
            a7.append_audit(log_path, dict(got, type="model_reply", case_id=cid,
                                           packet_sha256=pk["packet_sha256"]))
        print(f"[{i}/{len(case_ids)}] {cid} mode={pk['mode']} latency={pk['model']['latency_s']} "
              f"basis={pk['route_basis']}", flush=True)
        out[cid] = pk
    return out


def drill(packets, refs, rec_ids, seeds=1000, show_seed=8, log_path=None):
    pool = load_pool()
    qual = qualify(pool, packets)
    canary_ids = set(qual["qualified"])
    real = [p for cid, p in packets.items()
            if p["deescalation_proposal"] is not None and cid not in {c["case_id"] for c in pool["canaries"]}]
    canaries = [(packets[cid], c) for cid, c in qual["qualified"].items()]
    res = {"control_version": CONTROL_VERSION, "qualification": qual["report"],
           "real_proposals": len(real),
           "real_proposals_wrong": sorted(rec_ids[p["case_ref"]["case_id"]] for p in real
                                          if refs.get(p["case_ref"]["case_id"]) != BILLING),
           "math": detection_math()}
    shown = {}
    for policy in REVIEWERS:
        for on in (False, True):
            o = run_session(real, canaries, refs, policy, f"sim-{policy}", show_seed, on,
                            audit_path=log_path if on else None)
            for k in ("real_deesc", "wrong_real_deesc", "refused", "lookback", "spot_check",
                      "wrong_not_in_lookback"):
                o[k] = [rec_ids.get(c, c) for c in o[k]]
            for c in o["canaries"]:
                c["case_id"] = rec_ids.get(c["case_id"], c["case_id"])
            shown[f"{policy}|control={'on' if on else 'off'}"] = o
    res["session_seed"] = show_seed
    res["sessions"] = shown
    agg = {}
    for policy in REVIEWERS:
        runs = [run_session(real, canaries, refs, policy, "r", s, True) for s in range(seeds)]
        off = [run_session(real, canaries, refs, policy, "r", s, False) for s in range(seeds)]
        susp = [r for r in runs if r["suspended_at_item"]]
        agg[policy] = {
            "seeds": seeds,
            "suspended": f"{len(susp)}/{seeds}",
            "median_item_at_suspension": st.median([r["suspended_at_item"] for r in susp]) if susp else None,
            "wrong_real_deesc_mean_control_off": round(st.mean(len(r["wrong_real_deesc"]) for r in off), 2),
            "wrong_real_deesc_mean_control_on": round(st.mean(len(r["wrong_real_deesc"]) for r in runs), 2),
            "refused_mean": round(st.mean(len(r["refused"]) for r in runs), 2),
            "refused_mean_by_reason": {k: round(sum(r["refused_reasons"].get(k, 0) for r in runs) / seeds, 2)
                                       for k in ("suspended", "two_person_required")},
            "wrong_deesc_not_listed_for_review_total": sum(len(r["wrong_not_in_lookback"]) for r in runs),
            "watch_sessions": sum(1 for r in runs if r["watch_at_item"]),
            "watch_before_suspension": sum(1 for r in runs if r["watch_at_item"]
                                           and (not r["suspended_at_item"] or r["watch_at_item"] < r["suspended_at_item"])),
        }
    res["random_orders"] = agg
    return res


def _print(res):
    print(f"{CONTROL_VERSION} drill ({res.get('mode', '?')})")
    for x in res.get("live_cases", []):
        print(f"  live {x['course_record_id']} ({x['case_id']}): mode={x['mode']} basis={x['route_basis']} "
              f"latency={x['latency_s']} same_as_logged={x['same_route_basis_as_logged']} {x['errors'] or ''}")
    for q in res["qualification"]:
        print(f"  canary {q['course_record_id']} ({q['case_id']}): qualified={q['qualified']} "
              f"basis={q['route_basis']} model={q['model_id']}")
    print(f"  real proposals in queue: {res['real_proposals']}; reference says decline on "
          f"{len(res['real_proposals_wrong'])}: {res['real_proposals_wrong']}")
    print(f"\nSessions (seed {res['session_seed']}):")
    for k, o in res["sessions"].items():
        print(f"  {k:30s} items={o['items']:2d} real_deesc={len(o['real_deesc']):2d} "
              f"wrong={o['wrong_real_deesc']} refused={len(o['refused'])} {o['refused_reasons'] or ''} "
              f"suspended_at={o['suspended_at_item']} watch_at={o['watch_at_item']} "
              f"canaries={[(c['case_id'], c['outcome']) for c in o['canaries']]} "
              f"lookback={o['lookback']} spot_check={o['spot_check']} "
              f"wrong_not_listed_for_review={o['wrong_not_in_lookback']} "
              f"pool_exhausted_after_real={o['pool_exhausted_after_real']}")
    print(f"\nRandom queue orders:")
    for k, v in res["random_orders"].items():
        print(f"  {k}: {v}")
    print("\nDetection arithmetic:", json.dumps(res["math"], indent=1))


def export_openwebui(run_path, out_path=None) -> Path:
    """Live drill log -> Open WebUI chat-import JSON (Settings > Data > Import chats).

    One chat per live DGX call: system prompt in the chat params, the complaint message rebuilt
    from the committed sample and checked against the logged SHA-256 of the message actually sent,
    and the model's reply exactly as the DGX returned it (logged raw text, unedited)."""
    import hashlib
    from src.a6_prompts import PROMPTS, build_messages
    run_path = Path(run_path)
    recs = [json.loads(x) for x in run_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    meta = next(r for r in recs if r.get("type") == "meta")
    run_id = run_path.name.split("_")[0]
    packets = {r["packet_sha256"]: r for r in recs if r.get("type") == "packet"}
    rows = _narratives()
    pool = {c["case_id"]: c for c in load_pool()["canaries"]}
    manifest = json.loads((REPO_ROOT / "data" / "a6_manifest.json").read_text(encoding="utf-8"))
    rec_ids = {e["case_id"]: e["course_record_id"] for e in manifest["eval"]}
    pv = meta.get("prompt_version", "prompted-v1")
    if a7._text_sha(PROMPTS[pv]) != meta.get("prompt_sha256"):
        raise ValueError("system prompt differs from the one logged for this run")
    chats = []
    for r in (x for x in recs if x.get("type") == "model_reply"):
        p, case = packets[r["packet_sha256"]], r["case_id"]
        narrative = rows[case]["consumer_complaint_narrative"]
        msgs = build_messages(pv, narrative)
        if g.fingerprint(narrative) != p["case_ref"]["narrative_sha256"]:
            raise ValueError(f"{case}: narrative differs from the logged fingerprint")
        if a7._text_sha(msgs[1]["content"]) != r["user_msg_sha256"]:
            raise ValueError(f"{case}: rebuilt user message differs from the message sent")
        model = r.get("model") or meta.get("model_requested")
        t_reply = int(datetime.fromisoformat(p["created_utc"]).timestamp())
        t_user = int(t_reply - (r.get("latency_s") or 0))
        uid, aid, cid = (hashlib.sha256(f"{run_id}|{case}|{k}".encode()).hexdigest()[:32]
                         for k in ("user", "assistant", "chat"))
        user_msg = {"id": uid, "parentId": None, "childrenIds": [aid], "role": "user",
                    "content": msgs[1]["content"], "timestamp": t_user, "models": [model]}
        bot_msg = {"id": aid, "parentId": uid, "childrenIds": [], "role": "assistant",
                   "content": r["raw_text"], "model": model, "modelName": model,
                   "timestamp": t_reply, "done": True, "usage": r.get("usage") or {}}
        role = "canary" if case in pool else "real proposal"
        title = (f"A8 DGX live {run_id} | {rec_ids.get(case, case)} ({case}) | {role} | layer "
                 f"{p['route_basis']} ({r.get('latency_s')} s; imported API log, raw reply, commit "
                 f"{meta.get('git_commit')})")
        chat = {"id": cid, "title": title, "models": [model],
                "params": {"system": PROMPTS[pv], "temperature": meta.get("temperature"),
                           "seed": meta.get("seed")},
                "history": {"messages": {uid: user_msg, aid: bot_msg}, "currentId": aid},
                "messages": [user_msg, bot_msg], "tags": [], "timestamp": t_user * 1000}
        chats.append({"id": cid, "title": title, "chat": chat, "meta": {"tags": []},
                      "created_at": t_user, "updated_at": t_reply, "archived": False,
                      "pinned": False, "share_id": None, "folder_id": None})
    out = Path(out_path) if out_path else run_path.with_name(f"{run_id}_a8_openwebui_import.json")
    out.write_text(json.dumps(chats, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("math", help="detection arithmetic: canary vs acceptance-streak rule")
    d = sub.add_parser("drill", help="canary qualification + simulated reviewers through the control")
    d.add_argument("--live", action="store_true", help="DGX: live calls (NYU network + .env token)")
    d.add_argument("--live-ids", nargs="*", default=None,
                   help="with --live: call the model only on these cases (DEV-ids or case_ids); the "
                        "other proposals come from the logged replies. Default: all 34")
    d.add_argument("--timeout", type=float, default=a7.DEFAULT_MODEL_TIMEOUT_S,
                   help="per-call model timeout in seconds for --live (A7 default 45)")
    d.add_argument("--run", default=str(A6_RUN), help="logged DGX replies for replay mode")
    d.add_argument("--adjudicated", nargs="*", default=[],
                   help="team P3 labels first, then data/a6_audit_labels.csv (V2 reference)")
    d.add_argument("--seeds", type=int, default=1000)
    d.add_argument("--seed", type=int, default=8, help="seed of the printed example sessions")
    e = sub.add_parser("export-openwebui", help="live drill log -> Open WebUI chat-import JSON (raw replies)")
    e.add_argument("run")
    e.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "export-openwebui":
        out = export_openwebui(a.run, a.out)
        print(f"wrote {len(json.loads(out.read_text(encoding='utf-8')))} chats -> {out}")
        return 0
    if a.cmd == "math":
        print(json.dumps(detection_math(), indent=1))
        return 0
    refs, rec_ids = _refs(a.adjudicated)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = REPO_ROOT / "runs" / f"{run_id}_a8_drill_{'live' if a.live else 'replay'}.jsonl"
    replayed = replay_packets(Path(a.run))
    ids = sorted(cid for cid, p in replayed.items() if p["deescalation_proposal"] is not None)
    if a.live:
        by_rec = {v: k for k, v in rec_ids.items()}
        want = ids if a.live_ids is None else [by_rec.get(x, x) for x in a.live_ids]
        unknown = [x for x in want if x not in ids]
        if unknown:
            raise SystemExit(f"--live-ids not among the 34 proposal cases: {unknown}")
        live = live_packets(want, log, a.timeout)
        packets = {c: live.get(c, replayed[c]) for c in ids}
        drift = sorted(rec_ids[c] for c in want if live[c]["route_basis"] != replayed[c]["route_basis"])
        print(f"live vs logged route_basis differs on {len(drift)}/{len(want)}: {drift}")
    else:
        packets = {c: replayed[c] for c in ids}
        from src.a6_eval import git_state
        a7.append_audit(log, {"type": "meta", "control_version": CONTROL_VERSION, "mode": "replay",
                              "git_commit": git_state(), "run": Path(a.run).name,
                              "started_utc": _now()})
    res = drill(packets, refs, rec_ids, a.seeds, a.seed, log_path=log)
    res["mode"] = "live" if a.live else "replay"
    if a.live:
        res["model_timeout_s"] = a.timeout
        res["live_cases"] = [{"course_record_id": rec_ids[c], "case_id": c, "mode": live[c]["mode"],
                              "route_basis": live[c]["route_basis"], "latency_s": live[c]["model"]["latency_s"],
                              "errors": live[c]["model"]["errors"],
                              "same_route_basis_as_logged": live[c]["route_basis"] == replayed[c]["route_basis"]}
                             for c in want]
        res["replayed_cases"] = len(ids) - len(want)
        res["live_vs_logged_route_basis_differs"] = drift
        lat = sorted(live[c]["model"]["latency_s"] for c in want if live[c]["model"]["latency_s"])
        res["live_latency_s"] = {"n": len(lat), "median": st.median(lat) if lat else None,
                                 "max": lat[-1] if lat else None,
                                 "degraded": sum(live[c]["mode"] == "degraded" for c in want)}
    a7.append_audit(log, dict(res, type="summary"))
    _print(res)
    print("wrote", log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
