"""Assignment 7 candidate: deterministic authority layer (Mingyu He). Not merged.

Where it sits in the Group 1 architecture (patent flow, not complaint -> LLM -> answer):

    complaint --data_guard (narrative only)--+--> rules: 7-keyword fraud, legal language, fee ----+
                                             +--> model: prompted-v1 on DGX --> parser -----------+
                                                                                                  v
                                   AUTHORITY LAYER (this file) --> employee screen --> audit log

What the layer decides, and what it never decides
  * Default queue - asymmetric fraud floor. The keyword rule OR the model can put a case in
    Card Fraud & Security. The model can NOT take a keyword-flagged case out of it: when the
    rule says Fraud and the model says Billing, the default stays Fraud and the packet carries a
    de-escalation PROPOSAL that only an employee can accept, with a reason code from a fixed
    list and an authorisation attestation (apply_employee_decision).
  * Human review - deterministic triggers only: fraud floor, routing conflict, legal or
    regulatory language, fee, very short narrative, model failure. The model's own review flag
    and fraud concern are carried as advisory text; they never set or clear review.
  * Degradation - model disabled, timeout, call error or unusable output gives a rule-only
    default with review forced and mode = "degraded". A case is never blocked on the model.
  * Authority - the packet is a recommendation. The system never assigns, closes, grants
    relief or contacts a customer, and the packet stores a narrative fingerprint, not the text.

Standard library only. Uses the existing data_guard lexicons, parser and prompts unchanged.

  python -m unittest tests.test_a7_authority -v
  python -m src.a7_authority replay --run runs/20261013T122014Z_a6_prompted_eval.jsonl \
         --rule-run runs/20261013T115712Z_a6_rule_eval.jsonl [--adjudicated PATH ...]
  python -m src.a7_authority smoke          # DGX: NYU network + .env token
"""
import argparse
import csv
import hashlib
import json
import math
import re
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src import data_guard as g
from src.parser import parse_model_output

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "a7-recommendation-v1"
LAYER_VERSION = "a7-authority-v1"
BILLING, FRAUD = g.DESTINATIONS
USABLE = ("ok", "repaired")

# Same lexicons as the A6 rule arm (data_guard FRAUD_RX / LEGAL_RX, a6_eval FEE_RX), so the
# layer's rule half reproduces the logged rule arm exactly (checked by `replay`).
FEE_RX = re.compile(r"\bfees?\b")
RULES_SHA256 = hashlib.sha256("|".join(
    (g.FRAUD_RX.pattern, g.LEGAL_RX.pattern, FEE_RX.pattern)).encode("utf-8")).hexdigest()

# Unvalidated proxy for the team review rule "too little information to route" (TW5 s.6).
# 15 of 653 in-scope Development narratives have at most 20 words (data card, TW5 s.7).
SHORT_NARRATIVE_MAX_WORDS = 20

# Max observed prompted-v1 latency on DGX was 43.2 s (A6 run 20261013T122014Z, 81 cases);
# 45 s keeps every observed case. TE3's p95 <= 10 s target is NOT met (p95 21.1 s), so the
# employee screen should show the rule result at once and the model readout when it arrives.
DEFAULT_MODEL_TIMEOUT_S = 45.0

REVIEW_REASONS = (
    "fraud_floor",                   # default queue is Card Fraud & Security
    "routing_conflict",              # rule and model recommend different queues
    "legal_or_regulatory_language",  # statute, bankruptcy, attorney, "illegal", CFPB ...
    "fee_dispute",
    "short_narrative",
    "model_disabled",
    "model_unavailable",             # timeout or call error
    "model_output_unusable",         # parser status invalid / failed
)
ROUTE_BASES = ("rule_and_model_agree", "model_escalation", "fraud_floor_held", "rule_only_degraded")

# Moving a case OUT of Card Fraud & Security: employee only, one of these, plus attestation.
DEESCALATION_REASONS = (
    "customer_made_or_allowed_transaction",   # merchant, goods/services, refund, wrong amount
    "fee_interest_or_statement_dispute",
    "posting_or_duplicate_error",
    "keyword_used_figuratively",              # "this fee is a scam", "they stole my money"
)
ATTESTATION_TEXT = ("I read the complaint. The customer does not report a charge, card or account "
                    "made, used or opened without their authorisation.")
# Moving a case INTO Card Fraud & Security stays cheap (asymmetric by design).
ESCALATION_REASONS = (
    "unauthorized_use_stated", "card_lost_or_stolen", "identity_or_account_takeover",
    "other_security_concern",
)
SYSTEM_MAY = ("recommend_default_queue", "require_human_review", "propose_deescalation")
SYSTEM_MAY_NOT = ("assign_case", "close_case", "grant_relief", "contact_customer",
                  "clear_human_review", "deescalate_from_fraud_and_security")


class AuthorityError(ValueError):
    """An employee decision that the authority rules do not allow."""


# --------------------------------------------------------------------------- rules
def rule_signals(narrative: str) -> dict:
    t = narrative.lower()
    terms = sorted({m.group(0) for m in g.FRAUD_RX.finditer(t)})
    return {
        "fraud_keyword_hit": bool(terms), "matched_fraud_terms": terms,
        "legal_language_hit": bool(g.LEGAL_RX.search(t)), "fee_hit": bool(FEE_RX.search(t)),
        "word_count": len(narrative.split()),
        "route": FRAUD if terms else BILLING,
        "rules_sha256": RULES_SHA256,
    }


def _model_block(model_text, model_error, model_meta) -> dict:
    meta = dict(model_meta or {})
    block = {"status": None, "route": None, "fraud_or_security_concern": None,
             "review_advisory": None, "rationale": None, "case_record": None,
             "label": "model-generated, unverified: the employee checks it against the complaint",
             "model_id": meta.get("model_id"), "prompt_version": meta.get("prompt_version"),
             "prompt_sha256": meta.get("prompt_sha256"), "latency_s": meta.get("latency_s"),
             "repairs": [], "errors": []}
    if model_error is not None:
        block["status"] = "disabled" if model_error == "model_disabled" else "unavailable"
        block["errors"] = [str(model_error)[:300]]
        return block
    parsed = parse_model_output(model_text or "")
    rec = parsed.record or {}
    block.update(status=parsed.status, repairs=list(parsed.repairs), errors=list(parsed.errors))
    if parsed.status in USABLE:
        block.update(route=rec.get("recommended_routing"),
                     fraud_or_security_concern=rec.get("fraud_or_security_concern"),
                     review_advisory=rec.get("human_review_required"),
                     rationale=rec.get("rationale"),
                     case_record={k: rec.get(k) for k in ("case_summary", "stated_facts", "unknowns")})
    return block


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def decide(narrative: str, *, case_id: str = None, model_text: str = None,
           model_error: str = None, model_meta: dict = None, now: str = None) -> dict:
    """Combine the rule signals and one model reply into a recommendation packet.

    Pass model_text (the raw reply) OR model_error (why there is no usable reply). The packet
    never contains the narrative itself, only its data_guard fingerprint."""
    if not isinstance(narrative, str) or not narrative.strip():
        raise ValueError("empty or missing complaint narrative")
    rule = rule_signals(narrative)
    model = _model_block(model_text, model_error, model_meta)
    model_route = model["route"] if model["status"] in USABLE else None
    degraded = model_route is None

    proposal = None
    if degraded:
        default, basis = rule["route"], "rule_only_degraded"
    elif rule["route"] == model_route:
        default, basis = model_route, "rule_and_model_agree"
    elif model_route == FRAUD:                       # rule Billing, model Fraud: may escalate alone
        default, basis = FRAUD, "model_escalation"
    else:                                            # rule Fraud, model Billing: floor holds
        default, basis = FRAUD, "fraud_floor_held"
        proposal = {"proposed_queue": BILLING, "proposed_by": "model",
                    "model_rationale": model["rationale"],
                    "requires": ["employee_decision", "deescalation_reason_code",
                                 "authorisation_attestation"],
                    "allowed_reason_codes": list(DEESCALATION_REASONS),
                    "attestation_text": ATTESTATION_TEXT}

    reasons = []
    if default == FRAUD:
        reasons.append("fraud_floor")
    if not degraded and rule["route"] != model_route:
        reasons.append("routing_conflict")
    if rule["legal_language_hit"]:
        reasons.append("legal_or_regulatory_language")
    if rule["fee_hit"]:
        reasons.append("fee_dispute")
    if rule["word_count"] <= SHORT_NARRATIVE_MAX_WORDS:
        reasons.append("short_narrative")
    if degraded:
        reasons.append({"disabled": "model_disabled", "unavailable": "model_unavailable"}
                       .get(model["status"], "model_output_unusable"))

    packet = {
        "schema_version": SCHEMA_VERSION, "layer_version": LAYER_VERSION,
        "case_ref": {"case_id": case_id, "narrative_sha256": g.fingerprint(narrative)},
        "mode": "degraded" if degraded else "normal",
        "default_queue": default, "route_basis": basis, "deescalation_proposal": proposal,
        "human_review": {"required": bool(reasons), "reasons": reasons,
                         "set_by": "deterministic_rules"},
        "rule": rule, "model": model,
        "authority": {"system_may": list(SYSTEM_MAY), "system_may_not": list(SYSTEM_MAY_NOT),
                      "final_decision_by": "employee"},
    }
    packet["packet_sha256"] = _sha(packet)           # deterministic: excludes the timestamp
    packet["created_utc"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return packet


# --------------------------------------------------------------------------- employee
def apply_employee_decision(packet: dict, final_queue: str, employee_id: str, *,
                            reason_code: str = None, attest_customer_authorised: bool = False,
                            note: str = "", now: str = None) -> dict:
    """Validate an employee's final queue against the authority rules; return the audit record."""
    if final_queue not in g.DESTINATIONS:
        raise AuthorityError(f"final_queue must be one of {g.DESTINATIONS}: {final_queue!r}")
    if not str(employee_id or "").strip():
        raise AuthorityError("employee_id is required: every decision is attributable")
    default = packet["default_queue"]
    if final_queue == default:
        action = "accepted_default"
    elif default == FRAUD:                           # de-escalation: the guarded direction
        if reason_code not in DEESCALATION_REASONS:
            raise AuthorityError("moving a case out of Card Fraud & Security needs a reason code "
                                 f"from {DEESCALATION_REASONS}")
        if attest_customer_authorised is not True:
            raise AuthorityError("moving a case out of Card Fraud & Security needs the "
                                 "authorisation attestation")
        action = "deescalated"
    else:
        if reason_code not in ESCALATION_REASONS:
            raise AuthorityError(f"escalation needs a reason code from {ESCALATION_REASONS}")
        action = "escalated"
    prop = packet.get("deescalation_proposal")
    return {
        "type": "employee_decision", "schema_version": SCHEMA_VERSION,
        "packet_sha256": packet["packet_sha256"], "case_ref": packet["case_ref"],
        "mode": packet["mode"], "default_queue": default, "final_queue": final_queue,
        "action": action, "reason_code": reason_code,
        "attestation": ATTESTATION_TEXT if action == "deescalated" else None,
        "review_reasons": packet["human_review"]["reasons"],
        "model_proposal_followed": None if prop is None else final_queue == prop["proposed_queue"],
        "employee_id": employee_id, "note": note[:500],
        "decided_utc": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def append_audit(path, record: dict) -> None:
    """One JSON line per packet or decision. Narrative text is never written."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- validation
def validate_packet(p: dict) -> list:
    """Stdlib mirror of schemas/a7_recommendation.schema.json; returns a list of problems."""
    errs = []
    need = ("schema_version", "layer_version", "case_ref", "mode", "default_queue",
            "route_basis", "deescalation_proposal", "human_review", "rule", "model",
            "authority", "packet_sha256", "created_utc")
    errs += [f"missing {k}" for k in need if k not in p]
    if errs:
        return errs
    if p["schema_version"] != SCHEMA_VERSION:
        errs.append("schema_version")
    if p["mode"] not in ("normal", "degraded"):
        errs.append("mode")
    if p["default_queue"] not in g.DESTINATIONS:
        errs.append("default_queue")
    if p["route_basis"] not in ROUTE_BASES:
        errs.append("route_basis")
    hr = p["human_review"]
    if any(r not in REVIEW_REASONS for r in hr["reasons"]) or hr["required"] != bool(hr["reasons"]):
        errs.append("human_review")
    if hr.get("set_by") != "deterministic_rules":
        errs.append("human_review.set_by")
    # invariants that carry the authority decisions
    if p["rule"]["fraud_keyword_hit"] and p["default_queue"] != FRAUD:
        errs.append("INVARIANT: a keyword-flagged case left Card Fraud & Security without an employee")
    if p["model"]["route"] == FRAUD and p["mode"] == "normal" and p["default_queue"] != FRAUD:
        errs.append("INVARIANT: model escalation was dropped")
    if p["mode"] == "degraded" and not hr["required"]:
        errs.append("INVARIANT: degraded packet without review")
    if (p["deescalation_proposal"] is not None) != (p["route_basis"] == "fraud_floor_held"):
        errs.append("INVARIANT: proposal and route_basis disagree")
    if p["default_queue"] == FRAUD and not hr["required"]:
        errs.append("INVARIANT: Fraud & Security default without review")
    body = {k: v for k, v in p.items() if k not in ("packet_sha256", "created_utc")}
    if _sha(body) != p["packet_sha256"]:
        errs.append("packet_sha256 does not match content")
    return errs


# --------------------------------------------------------------------------- live path
def run_case(cfg: dict, narrative: str, case_id: str = None, prompt_version: str = "prompted-v1",
             call=None) -> dict:
    """Call the model once (with a hard timeout) and return the packet. Any failure degrades."""
    from src.a6_prompts import build_messages, prompt_sha256
    meta = {"model_id": cfg.get("MODEL_ID"), "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256(prompt_version)}
    if str(cfg.get("MODEL_ENABLED", "true")).lower() in ("0", "false", "no", "off"):
        return decide(narrative, case_id=case_id, model_error="model_disabled", model_meta=meta)
    if call is None:
        from src.client import chat as call
    c = dict(cfg)
    c["TIMEOUT_S"] = str(cfg.get("A7_MODEL_TIMEOUT_S", DEFAULT_MODEL_TIMEOUT_S))
    try:
        reply = call(c, build_messages(prompt_version, narrative))
    except Exception as exc:          # timeout, network, HTTP, bad body: all degrade, never block
        return decide(narrative, case_id=case_id, model_meta=meta,
                      model_error=f"{type(exc).__name__}: {exc}")
    meta.update(model_id=reply.get("model", meta["model_id"]), latency_s=reply.get("latency_s"))
    return decide(narrative, case_id=case_id, model_text=reply.get("text"), model_meta=meta)


# --------------------------------------------------------------------------- replay
def _load_jsonl(path):
    meta, cases = None, {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "meta" and meta is None:
            meta = rec
        elif rec.get("type") == "case":
            cases[rec["case_id"]] = rec
    return meta, cases


def _frac(k, n):
    return f"{k}/{n}" if n else "-"


def replay(run_path, cases_csv, manifest_path, rule_run=None, adjudicated=(), timeouts=(10, 30, 45)):
    """Feed logged DGX replies through decide(). No network; narratives come from the
    committed 81-case development sample and are checked against the manifest fingerprints."""
    from src import a6_eval as ev
    meta, logged = _load_jsonl(run_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = {e["case_id"]: e for e in manifest["eval"]}
    with open(cases_csv, encoding="utf-8-sig", newline="") as fh:
        text = {r["case_id"]: r["consumer_complaint_narrative"] for r in csv.DictReader(fh)}
    rule_log = _load_jsonl(rule_run)[1] if rule_run else {}
    adj = ev.load_adjudicated(*adjudicated) if adjudicated else {}

    mmeta = {"model_id": meta.get("model_requested"), "prompt_version": meta.get("prompt_version"),
             "prompt_sha256": meta.get("prompt_sha256")}
    rows, problems = [], []
    for cid, rec in logged.items():
        e, narrative = entries[cid], text[cid]
        if g.fingerprint(narrative) != e["narrative_sha256"]:
            problems.append(f"{cid}: narrative fingerprint differs from manifest")
        p = decide(narrative, case_id=cid, model_text=rec.get("raw_text"),
                   model_meta=dict(mmeta, latency_s=rec.get("latency_s")), now="replay")
        problems += [f"{cid}: {x}" for x in validate_packet(p)]
        if narrative.strip() in json.dumps(p, ensure_ascii=False):
            problems.append(f"{cid}: narrative text found in packet")
        if p["model"]["status"] != rec.get("parse_status"):
            problems.append(f"{cid}: parser status {p['model']['status']} != logged {rec.get('parse_status')}")
        if cid in rule_log:
            lr = rule_log[cid]["record"]
            r = p["rule"]
            if r["route"] != lr["recommended_routing"]:
                problems.append(f"{cid}: rule route differs from logged rule arm")
            if (r["fraud_keyword_hit"] or r["legal_language_hit"] or r["fee_hit"]) != lr["human_review_required"]:
                problems.append(f"{cid}: rule review differs from logged rule arm")
        ref = ev.team_route(e, adj)[0] if adj else None
        rows.append({"cid": cid, "rec": e["course_record_id"], "s": e["stratum"][0], "p": p, "ref": ref,
                     "rev_intake": e["ref"]["review_intake"], "lat": rec.get("latency_s")})

    W = {k[0]: v for k, v in manifest["scope_strata"].items()}

    def pw(pred):          # population-weighted share over the in-scope strata
        tot = sum(W.values())
        return sum(W[s] / tot * sum(pred(r) for r in rows if r["s"] == s)
                   / max(1, sum(r["s"] == s for r in rows)) for s in "ABC")

    out = {"run": Path(run_path).name, "run_commit": meta.get("git_commit"), "model": mmeta,
           "layer_version": LAYER_VERSION, "rules_sha256": RULES_SHA256, "n": len(rows),
           "checks": {"problems": problems,
                      "rule_half_matches_logged_rule_arm": _frac(
                          sum(1 for r in rows if r["cid"] in rule_log) - sum(
                              "logged rule arm" in x for x in problems), len(rule_log))},
           "route_basis": dict(Counter(r["p"]["route_basis"] for r in rows)),
           "mode": dict(Counter(r["p"]["mode"] for r in rows)),
           "deescalation_proposals": sum(r["p"]["deescalation_proposal"] is not None for r in rows),
           "deescalation_proposals_pop_weighted": round(pw(lambda r: r["p"]["deescalation_proposal"] is not None), 3),
           "review_reasons": dict(Counter(x for r in rows for x in r["p"]["human_review"]["reasons"])),
           "review_required": sum(r["p"]["human_review"]["required"] for r in rows),
           "review_required_pop_weighted": round(pw(lambda r: r["p"]["human_review"]["required"]), 3),
           "model_advisory_would_add": sum((r["p"]["model"]["review_advisory"] is True)
                                           and not r["p"]["human_review"]["required"] for r in rows)}
    yes = [r for r in rows if r["rev_intake"] == "Yes"]
    no = [r for r in rows if r["rev_intake"] == "No"]
    out["review_vs_intake_reference"] = {
        "recall": _frac(sum(r["p"]["human_review"]["required"] for r in yes), len(yes)),
        "specificity": _frac(sum(not r["p"]["human_review"]["required"] for r in no), len(no)),
        "missed_case_ids": sorted(r["rec"] for r in yes if not r["p"]["human_review"]["required"])}
    lat = sorted(r["lat"] for r in rows if r["lat"] is not None)
    if lat:
        out["latency_s"] = {"median": st.median(lat), "p95": lat[math.ceil(0.95 * len(lat)) - 1],
                            "max": lat[-1],
                            "would_degrade_at_timeout": {str(t): sum(x > t for x in lat) for t in timeouts}}
    if adj:
        dec = [r for r in rows if r["ref"]]
        fs = [r for r in dec if r["ref"] == FRAUD]

        def score(route):
            pf = [r for r in dec if route(r) == FRAUD]
            return {"correct": _frac(sum(route(r) == r["ref"] for r in dec), len(dec)),
                    "fs_recall": _frac(sum(route(r) == FRAUD for r in fs), len(fs)),
                    "fs_precision": _frac(sum(r["ref"] == FRAUD for r in pf), len(pf))}
        mr = lambda r: r["p"]["model"]["route"]
        out["reference"] = "team V2 (adjudicated where available, course label otherwise)"
        out["policies"] = {
            "keyword_rule_alone": score(lambda r: r["p"]["rule"]["route"]),
            "model_alone": score(mr),
            "layer_default_queue (no employee action)": score(lambda r: r["p"]["default_queue"]),
            "layer + employee accepts every proposal": score(
                lambda r: BILLING if r["p"]["deescalation_proposal"] else r["p"]["default_queue"]),
            "layer + employee right on every proposal (upper bound)": score(
                lambda r: r["ref"] if r["p"]["deescalation_proposal"] else r["p"]["default_queue"]),
        }
        props = [r for r in rows if r["p"]["deescalation_proposal"]]
        out["proposals_by_reference"] = {
            "billing": sum(r["ref"] == BILLING for r in props),
            "fraud_and_security": sum(r["ref"] == FRAUD for r in props),
            "disputed_or_missing": sum(r["ref"] is None for r in props),
            "fraud_case_ids": sorted(r["rec"] for r in props if r["ref"] == FRAUD)}
    return out


def _print_replay(o):
    print(f"A7 replay of {o['run']} (commit {o['run_commit']}), {o['n']} cases, layer {o['layer_version']}")
    print(f"checks: {len(o['checks']['problems'])} problems; rule half matches logged rule arm "
          f"{o['checks']['rule_half_matches_logged_rule_arm']}")
    for p in o["checks"]["problems"][:10]:
        print("  !", p)
    print("route basis:", o["route_basis"], "| mode:", o["mode"])
    print(f"de-escalation proposals: {o['deescalation_proposals']} "
          f"(population-weighted share {o['deescalation_proposals_pop_weighted']})")
    print(f"review required: {o['review_required']}/{o['n']} (population-weighted "
          f"{o['review_required_pop_weighted']}); reasons {o['review_reasons']}")
    print("review vs intake reference:", o["review_vs_intake_reference"])
    print("model advisory would add review on", o["model_advisory_would_add"], "cases")
    if "latency_s" in o:
        print("latency:", o["latency_s"])
    if "policies" in o:
        print(f"\n| policy ({o['reference']}) | correct | F&S recall | F&S precision |\n|---|---|---|---|")
        for k, v in o["policies"].items():
            print(f"| {k} | {v['correct']} | {v['fs_recall']} | {v['fs_precision']} |")
        print("\nproposals by reference:", o["proposals_by_reference"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="feed logged DGX replies through the layer (no network)")
    r.add_argument("--run", required=True)
    r.add_argument("--rule-run")
    r.add_argument("--cases", default=str(REPO_ROOT / "data" / "dev_sample_81.csv"))
    r.add_argument("--manifest", default=str(REPO_ROOT / "data" / "a6_manifest.json"))
    r.add_argument("--adjudicated", nargs="*", default=[],
                   help="team P3 labels first, then data/a6_audit_labels.csv (V2 reference)")
    r.add_argument("--out")
    s = sub.add_parser("smoke", help="live DGX check: 3 cases through client + layer, 1 forced timeout")
    s.add_argument("--cases", default=str(REPO_ROOT / "data" / "dev_sample_81.csv"))
    s.add_argument("--ids", nargs="*", default=["1487055", "1834081", "2308225"],
                   help="case_ids from the 81-case development sample. Defaults cover the three "
                        "paths seen in the A6 replay: A fraud_floor_held, B agree-Fraud, C agree-Billing")
    a = ap.parse_args(argv)

    if a.cmd == "replay":
        o = replay(a.run, a.cases, a.manifest, a.rule_run, a.adjudicated)
        _print_replay(o)
        if a.out:
            Path(a.out).write_text(json.dumps(o, indent=1), encoding="utf-8")
            print("wrote", a.out)
        return 1 if o["checks"]["problems"] else 0

    from src.config import load_config
    from src.a6_eval import git_state
    cfg = load_config()
    with open(a.cases, encoding="utf-8-sig", newline="") as fh:
        rows = {x["case_id"]: x for x in csv.DictReader(fh)}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPO_ROOT / "runs" / f"{run_id}_a7_smoke.jsonl"
    append_audit(path, {"type": "meta", "run_id": run_id, "git_commit": git_state(),
                        "endpoint": cfg["OPENWEBUI_BASE_URL"], "model_requested": cfg["MODEL_ID"],
                        "layer_version": LAYER_VERSION, "rules_sha256": RULES_SHA256})
    plan = [(cid, cfg) for cid in a.ids] + [(a.ids[0], dict(cfg, A7_MODEL_TIMEOUT_S="0.001"))]
    bad = 0
    for cid, c in plan:
        row = rows[cid]
        payload = g.build_model_input(row)
        g.assert_input_boundary(payload, row)
        pk = run_case(c, payload["consumer_complaint_narrative"], case_id=cid)
        probs = validate_packet(pk)
        bad += bool(probs)
        append_audit(path, dict(pk, type="packet", problems=probs))
        print(f"{cid} timeout={c.get('A7_MODEL_TIMEOUT_S', DEFAULT_MODEL_TIMEOUT_S)}s mode={pk['mode']} "
              f"model={pk['model']['status']} latency={pk['model']['latency_s']} "
              f"default={pk['default_queue']} basis={pk['route_basis']} "
              f"review={pk['human_review']['reasons']} problems={probs}")
    print("wrote", path)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
