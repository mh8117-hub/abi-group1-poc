"""Assignment 6 candidate: frozen three-arm evaluation harness (rule / base / prompted / adapted).

Question it answers for Team Exercise 6: on the same frozen development cases, does an arm
(structured prompting, or a LoRA adapter once one is served) change the routing and review
decisions enough to justify its cost - against the incumbent arm AND the keyword rule?

Design (frozen in config/a6_frozen.json before any evaluation run):
  - Evaluation slice = the A4 81-case sample (27 per stratum A/B/C; all 27 rule-error cases).
  - Tuning slice = 12 cases from the remaining pool, for prompt debugging only.
  - Training pool = everything else in scope (input for a future adapter via export-sft).
    The three sets are disjoint by case_id and by narrative fingerprint.
  - Every arm goes through data_guard: narrative only, held-out refused, boundary asserted.
  - Primary reference = "team": the team-adjudicated route on stratum A (2 disputed cases
    excluded and reported separately), the course route on strata B and C.
  - Paired comparison per case: exact McNemar test, plus a population-weighted accuracy that
    undoes the deliberate over-sampling of stratum A.

Usage (repo root):
  python -m src.a6_eval manifest   --dev PATH/ABI_Bank_Complaints_Development_8000.csv
  python -m src.a6_eval rule       --dev PATH
  python -m src.a6_eval run        --dev PATH --arm base|prompted [--slice tuning|eval]
  python -m src.a6_eval run        --dev PATH --arm adapted --model <adapter model id>
  python -m src.a6_eval run        --dev PATH --arm prompted --resume runs/<file>.jsonl
  python -m src.a6_eval score      runs/<rule>.jsonl runs/<base>.jsonl runs/<prompted>.jsonl
  python -m src.a6_eval export-sft --dev PATH --out exports/a6_sft_train.jsonl
"""
import argparse
import csv
import hashlib
import json
import math
import platform
import random
import re
import statistics as st
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src import data_guard as g
from src.a6_prompts import SCHEMA_OF, build_messages, prompt_sha256
from src.config import REPO_ROOT

BILLING, FRAUD = g.DESTINATIONS
ARMS = {"base": "baseline-v1", "prompted": "prompted-v1", "adapted": "adapter-v1"}
FROZEN = REPO_ROOT / "config" / "a6_frozen.json"
MANIFEST = REPO_ROOT / "data" / "a6_manifest.json"
EVAL_SOURCE = REPO_ROOT / "data" / "dev_sample_81.csv"
ADJUDICATED = REPO_ROOT / "data" / "adjudication" / "p3_adjudicated_labels.csv"
FEE_RX = re.compile(r"\bfees?\b")
USABLE = ("ok", "repaired")


def sha256_file(path) -> str:
    """Content hash with line endings normalised, so Windows (CRLF) and Linux checkouts agree."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git_state() -> str:
    """Short commit, marked -dirty if code, config or manifest CONTENT differs from HEAD.

    Content-based (git diff), not stat-based (git status): on the Windows client with
    core.autocrlf=true, regenerating the manifest with LF endings made `git status` report it
    modified although `git diff` was empty, which would have blocked a frozen run."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    try:
        head = git("rev-parse", "--short", "HEAD")
        changed = git("diff", "--name-only", "HEAD", "--", "src", "config", "data/a6_manifest.json")
        untracked = git("ls-files", "--others", "--exclude-standard", "--", "src", "config")
        return head + ("-dirty" if changed or untracked else "")
    except Exception:
        return "unknown"


# ------------------------------------------------------------------ data
def load_scope(dev_path: str):
    rows, log = g.load_development(dev_path, g.load_heldout_hashes())
    return [dict(r, **g.annotate(r)) for r in g.in_scope(rows)], log


def _entry(r: dict, group: str) -> dict:
    return {
        "case_id": r["case_id"], "course_record_id": r["course_record_id"], "group": group,
        "stratum": r["stratum"], "narrative_sha256": g.fingerprint(r["consumer_complaint_narrative"]),
        "word_count": int(r["word_count"]), "label_status": r["label_status"],
        "ref": {"routing": r["routing_destination"], "review_course": r["human_review_required"],
                "review_intake": r["review_ref_intake"],
                "review_post_outcome_only": r["review_post_outcome_only"],
                "fraud_indicator": r["fraud_indicator"]},
    }


def build_manifest(dev_path: str, tuning_per_group: int = 4, seed: int = 6) -> dict:
    scope, log = load_scope(dev_path)
    by_id = {r["case_id"]: r for r in scope}
    with open(EVAL_SOURCE, encoding="utf-8-sig", newline="") as fh:
        eval_ids = [r["case_id"] for r in csv.DictReader(fh)]
    missing = [i for i in eval_ids if i not in by_id]
    if missing:
        raise g.DataGuardError(f"evaluation cases not in guarded scope: {missing}")
    eval_set = set(eval_ids)
    eval_fp = {g.fingerprint(by_id[i]["consumer_complaint_narrative"]) for i in eval_ids}
    pool = [r for r in scope if r["case_id"] not in eval_set
            and g.fingerprint(r["consumer_complaint_narrative"]) not in eval_fp]

    rng = random.Random(seed)
    groups = {
        "B_fraudtext_fraud_owner": [r for r in pool if r["stratum"].startswith("B")],
        "C_review_intake_yes": [r for r in pool if r["stratum"].startswith("C") and r["review_ref_intake"] == "Yes"],
        "C_review_intake_no": [r for r in pool if r["stratum"].startswith("C") and r["review_ref_intake"] == "No"],
    }
    tuning = []
    for name, members in groups.items():
        members.sort(key=lambda r: int(r["case_id"]))
        tuning += [_entry(m, name) for m in rng.sample(members, tuning_per_group)]
    tuning_ids = {t["case_id"] for t in tuning}
    train = [r for r in pool if r["case_id"] not in tuning_ids]

    return {
        "built_from": Path(dev_path).name,
        "dev_rows_read": log["rows_read"],
        "excluded_heldout_overlap": log["excluded_heldout_overlap"],
        "scope_n": len(scope),
        "scope_strata": dict(sorted(Counter(r["stratum"] for r in scope).items())),
        "eval_source": "data/dev_sample_81.csv (A4: select_sample.py --per-group 27 --seed 4)",
        "eval": [_entry(by_id[i], by_id[i]["stratum"]) for i in eval_ids],
        "tuning_seed": seed,
        "tuning": tuning,
        "train_pool": {
            "n": len(train),
            "strata": dict(sorted(Counter(r["stratum"] for r in train).items())),
            "routing": dict(sorted(Counter(r["routing_destination"] for r in train).items())),
            "case_ids": sorted((r["case_id"] for r in train), key=int),
        },
    }


def check_manifest(m: dict) -> list:
    """Disjointness checks; returns a list of problems (empty = clean)."""
    problems = []
    sets = {"eval": m["eval"], "tuning": m["tuning"]}
    ids = {k: {e["case_id"] for e in v} for k, v in sets.items()}
    ids["train"] = set(m["train_pool"]["case_ids"])
    fps = {k: {e["narrative_sha256"] for e in v} for k, v in sets.items()}
    for a, b in (("eval", "tuning"), ("eval", "train"), ("tuning", "train")):
        if ids[a] & ids[b]:
            problems.append(f"{a} and {b} share case_ids {sorted(ids[a] & ids[b])[:5]}")
    if fps["eval"] & fps["tuning"]:
        problems.append("eval and tuning share a narrative fingerprint")
    if len(ids["eval"]) + len(ids["tuning"]) + len(ids["train"]) != m["scope_n"]:
        problems.append("eval + tuning + train does not add up to the guarded scope")
    return problems


def load_adjudicated(*paths) -> dict:
    """course_record_id -> {'route': adjudicated route or None if disputed, 'review', 'source'}.

    Same CSV format as the team's p3_adjudicated_labels.csv. Earlier files take precedence, so
    the team's two-labeller adjudication is never overridden by a later single-labeller audit."""
    adj = {}
    for path in paths or (ADJUDICATED,):
        p = Path(path)
        if not p.exists():
            continue
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["course_record_id"] in adj or not r.get("route_status"):
                    continue
                adj[r["course_record_id"]] = {
                    "route": None if r["route_status"] == "disputed" else r["team_route"],
                    "review": r.get("team_review", ""), "source": p.name}
    return adj


def select_cases(manifest: dict, slice_name: str, dev_path: str) -> list:
    scope, _ = load_scope(dev_path)
    by_id = {r["case_id"]: r for r in scope}
    out = []
    for e in manifest[slice_name]:
        r = by_id.get(e["case_id"])
        if r is None or g.fingerprint(r["consumer_complaint_narrative"]) != e["narrative_sha256"]:
            raise g.DataGuardError(f"manifest and data disagree on case {e['case_id']}")
        out.append((e, r))
    return out


# ------------------------------------------------------------------ arms
def rule_decision(narrative: str) -> dict:
    """Deterministic comparator: the 7-keyword fraud rule plus a lexical version of the
    team review rule (fraud words, legal language or a fee). Narrative only."""
    t = narrative.lower()
    fraud = bool(g.FRAUD_RX.search(t))
    return {"fraud_or_security_concern": fraud,
            "recommended_routing": FRAUD if fraud else BILLING,
            "human_review_required": fraud or bool(g.LEGAL_RX.search(t)) or bool(FEE_RX.search(t))}


def keep_awake() -> None:
    """A4 lost 34 of 81 cases when the client laptop slept. Ask Windows to stay awake for the
    lifetime of this process only (no system setting is changed)."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def run_meta(arm: str, slice_name: str, cfg: dict, prompt_version) -> dict:
    return {
        "type": "meta", "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "arm": arm, "slice": slice_name, "git_commit": git_state(),
        "frozen_config_sha256": sha256_file(FROZEN), "manifest_sha256": sha256_file(MANIFEST),
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256(prompt_version) if prompt_version else None,
        "model_requested": cfg.get("MODEL_ID"), "endpoint": cfg.get("OPENWEBUI_BASE_URL"),
        "temperature": cfg.get("TEMPERATURE"), "seed": cfg.get("SEED"), "max_tokens": cfg.get("MAX_TOKENS"),
        "client_host": platform.node(), "client_platform": platform.platform(),
        "python": platform.python_version(),
    }


def cmd_rule(a) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = select_cases(manifest, a.slice, a.dev)
    meta = run_meta("rule", a.slice, {}, None)
    path = REPO_ROOT / "runs" / f"{meta['run_id']}_a6_rule_{a.slice}.jsonl"
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps(meta) + "\n")
        for e, r in cases:
            payload = g.build_model_input(r)
            g.assert_input_boundary(payload, r)
            out.write(json.dumps({"type": "case", "case_id": e["case_id"], "stratum": e["stratum"],
                                  "arm": "rule", "parse_status": "ok", "latency_s": 0.0,
                                  "record": rule_decision(payload["consumer_complaint_narrative"])}) + "\n")
    print(f"rule arm: {len(cases)} cases -> {path.relative_to(REPO_ROOT)}")
    return path


def cmd_run(a) -> Path:
    from src.client import ModelCallError, chat
    from src.config import load_config
    from src.parser import parse_model_output

    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    if a.slice == "eval":
        if frozen.get("status") != "frozen":
            sys.exit("Refusing: config/a6_frozen.json is not frozen; evaluation runs need a frozen design.")
        if git_state().endswith("-dirty"):
            sys.exit("Refusing: uncommitted changes in src/, config/ or the manifest; commit before an eval run.")
    if a.arm == "adapted" and not a.model:
        sys.exit("The adapted arm needs --model <model id under which the adapter is served>.")
    cfg = load_config()
    if a.model:
        cfg["MODEL_ID"] = a.model
    pv = ARMS[a.arm]
    pinned = frozen.get("prompt_versions", {}).get(pv)
    if a.slice == "eval" and pinned and pinned != prompt_sha256(pv):
        sys.exit(f"Refusing: prompt {pv} changed after the design was frozen (sha256 differs from config).")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = select_cases(manifest, a.slice, a.dev)

    done = set()
    if a.resume:
        path = Path(a.resume)
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("type") == "case" and rec["parse_status"] != "call_error":
                done.add(rec["case_id"])
    meta = run_meta(a.arm, a.slice, cfg, pv)
    label = a.arm
    if a.arm == "adapted" and cfg["MODEL_ID"] == load_config()["MODEL_ID"]:
        if a.slice == "eval":
            sys.exit("Refusing: the adapted arm on the eval slice needs the adapter's own model id.")
        meta["stand_in"] = "base model in the adapter slot: plumbing test of adapter-v1, not an adaptation result"
        label = "adapted-standin"
    if not a.resume:
        path = REPO_ROOT / "runs" / f"{meta['run_id']}_a6_{label}_{a.slice}.jsonl"
    else:
        meta["resumed_file"] = path.name
    keep_awake()
    print(json.dumps({k: v for k, v in meta.items() if k != "endpoint"}, indent=1))
    todo = [(e, r) for e, r in cases if e["case_id"] not in done]
    print(f"{len(todo)} to run, {len(done)} already done")
    with open(path, "a", encoding="utf-8") as out:
        out.write(json.dumps(meta) + "\n")
        for i, (e, r) in enumerate(todo, 1):
            payload = g.build_model_input(r)
            g.assert_input_boundary(payload, r)
            msgs = build_messages(pv, payload["consumer_complaint_narrative"])
            rec = {"type": "case", "case_id": e["case_id"], "stratum": e["stratum"], "arm": a.arm,
                   "payload_keys": sorted(payload),
                   "user_msg_sha256": hashlib.sha256(msgs[-1]["content"].encode("utf-8")).hexdigest()}
            try:
                resp = chat(cfg, msgs)
                parsed = parse_model_output(resp["text"], SCHEMA_OF[pv])
                rec.update(model_returned=resp["model"], latency_s=resp["latency_s"], usage=resp["usage"],
                           raw_text=resp["text"], **parsed.as_dict())
            except ModelCallError as exc:
                rec.update(parse_status="call_error", errors=[str(exc)])
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            route = (rec.get("record") or {}).get("recommended_routing") or "-"
            print(f"[{i:>2}/{len(todo)}] {e['case_id']:>8} {e['stratum'][:1]} {rec['parse_status']:<10} "
                  f"{rec.get('latency_s', '-')!s:>6}s {'F&S' if route == FRAUD else 'Bill' if route == BILLING else '-'}",
                  flush=True)
    print(f"-> {path.relative_to(REPO_ROOT)}")
    return path


# ------------------------------------------------------------------ scoring
def load_run(path) -> tuple:
    metas, cases = [], {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "meta":
            metas.append(rec)
        elif rec.get("type") == "case":
            if rec["parse_status"] == "call_error" and rec["case_id"] in cases:
                continue
            cases[rec["case_id"]] = rec
    if not metas:
        raise ValueError(f"{path}: no meta line")
    return metas, cases


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def team_route(e: dict, adj: dict):
    """Team reference. Returns (route or None if disputed/unavailable, source).

    An adjudicated label wins in any stratum; stratum A has no usable course label, so an
    unadjudicated A case is excluded; B and C fall back to the course label."""
    if e["course_record_id"] in adj:
        return adj[e["course_record_id"]]["route"], "adjudicated"
    if e["stratum"].startswith("A"):
        return None, "missing"
    return e["ref"]["routing"], "course"


def predictions(cases: dict, entries: list) -> dict:
    out = {}
    for e in entries:
        rec = cases.get(e["case_id"])
        usable = rec is not None and rec["parse_status"] in USABLE
        r = rec["record"] if usable else {}
        out[e["case_id"]] = {"route": r.get("recommended_routing"), "review": r.get("human_review_required"),
                             "status": rec["parse_status"] if rec else "missing",
                             "latency": rec.get("latency_s") if rec else None,
                             "usage": rec.get("usage") if rec else None}
    return out


def score_arm(pred: dict, entries: list, adj: dict, scope_strata: dict) -> dict:
    s = {"n": len(entries)}
    s["status"] = dict(Counter(pred[e["case_id"]]["status"] for e in entries))
    s["usable"] = sum(pred[e["case_id"]]["status"] in USABLE for e in entries)
    s["to_fraud"] = sum(pred[e["case_id"]]["route"] == FRAUD for e in entries)

    def ok(e, ref):
        return pred[e["case_id"]]["route"] == ref

    s["route_course"] = {k: [sum(ok(e, e["ref"]["routing"]) for e in entries if e["stratum"].startswith(k)),
                             sum(e["stratum"].startswith(k) for e in entries)] for k in "ABC"}
    team = [(e, team_route(e, adj)[0]) for e in entries]
    decided = [(e, t) for e, t in team if t]
    s["team_decided"] = len(decided)
    s["team_correct_ids"] = sorted(e["case_id"] for e, t in decided if ok(e, t))
    s["route_team"] = {k: [sum(ok(e, t) for e, t in decided if e["stratum"].startswith(k)),
                           sum(e["stratum"].startswith(k) for e, t in decided)] for k in "ABC"}
    fs = [(e, t) for e, t in decided if t == FRAUD]
    pf = [(e, t) for e, t in decided if pred[e["case_id"]]["route"] == FRAUD]
    s["fs_recall"] = [sum(ok(e, t) for e, t in fs), len(fs)]
    s["fs_precision"] = [sum(t == FRAUD for e, t in pf), len(pf)]
    total = sum(scope_strata.values())
    w = 0.0
    for k in "ABC":
        c, n = s["route_team"][k]
        weight = sum(v for name, v in scope_strata.items() if name.startswith(k)) / total
        w += weight * (c / n if n else 0)
    s["route_team_weighted"] = w
    s["disputed"] = {e["case_id"]: pred[e["case_id"]]["route"] for e, t in team if t is None}

    def review(ref_key):
        yes = [e for e in entries if e["ref"][ref_key] == "Yes"]
        no = [e for e in entries if e["ref"][ref_key] == "No"]
        return {"recall": [sum(pred[e["case_id"]]["review"] is True for e in yes), len(yes)],
                "specificity": [sum(pred[e["case_id"]]["review"] is False for e in no), len(no)]}

    s["review_course"] = review("review_course")
    s["review_intake"] = review("review_intake")
    a_adj = [e for e in entries if e["course_record_id"] in adj and e["stratum"].startswith("A")
             and adj[e["course_record_id"]]["review"] in ("Yes", "No")]
    s["review_team_A"] = [sum(pred[e["case_id"]]["review"] is (adj[e["course_record_id"]]["review"] == "Yes")
                              for e in a_adj), len(a_adj)]
    s["flag_all"] = sum(pred[e["case_id"]]["review"] is True for e in entries)
    lat = sorted(p["latency"] for p in (pred[e["case_id"]] for e in entries) if p["latency"])
    s["latency_median"] = st.median(lat) if lat else None
    s["latency_p95"] = lat[max(0, math.ceil(0.95 * len(lat)) - 1)] if lat else None
    pt = [p["usage"]["prompt_tokens"] for p in (pred[e["case_id"]] for e in entries) if p["usage"]]
    s["prompt_tokens_median"] = st.median(pt) if pt else None
    return s


def verdict(cand: str, inc: str, S: dict, entries: list, adj: dict, rule: dict) -> dict:
    """Apply the frozen decision rule: does arm `cand` earn replacing arm `inc`?"""
    ids = [e["case_id"] for e in entries if team_route(e, adj)[0]]
    cset, iset, rset = (set(S[x]["team_correct_ids"]) for x in (cand, inc, "rule"))
    b = sum(i in cset and i not in iset for i in ids)
    c = sum(i in iset and i not in cset for i in ids)
    p = mcnemar_exact(b, c)
    rec, n = S[cand]["review_intake"]["recall"]
    checks = {
        "R1_beats_incumbent": len(cset) > len(iset) and p < rule["R1_alpha"],
        "R2_beats_keyword_rule": len(cset) > len(rset),
        "R3_review_recall_floor": n > 0 and rec / n >= rule["R3_review_recall_floor"],
        "R4_usable_output_floor": S[cand]["usable"] / S[cand]["n"] >= rule["R4_usable_output_floor"],
        "R5_latency_ratio": (S[inc]["latency_median"] in (None, 0)) or
                            (S[cand]["latency_median"] or 0) <= rule["R5_latency_ratio_max"] * S[inc]["latency_median"],
    }
    return {"candidate": cand, "incumbent": inc, "b_cand_only": b, "c_inc_only": c, "p_mcnemar": p,
            "checks": checks, "adopt": all(checks.values())}


def frac(pair) -> str:
    k, n = pair
    if not n:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%; {100 * lo:.0f}-{100 * hi:.0f})"


def cmd_score(a) -> str:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    adj = load_adjudicated(*a.adjudicated)
    entries = manifest[a.slice]
    S, M = {}, {}
    for path in a.runs:
        metas, cases = load_run(path)
        arm = metas[0]["arm"] + ("-standin" if metas[0].get("stand_in") else "")
        S[arm] = score_arm(predictions(cases, entries), entries, adj, manifest["scope_strata"])
        M[arm] = metas
    order = [x for x in ("rule", "base", "prompted", "adapted", "adapted-standin") if x in S]
    L = [f"# A6 comparison on the frozen {a.slice} slice ({len(entries)} cases)", ""]
    for path in a.adjudicated:
        if Path(path).exists():
            src = [e for e in entries if adj.get(e["course_record_id"], {}).get("source") == Path(path).name]
            L.append(f"Adjudication file {Path(path).name} (sha256 {sha256_file(path)[:12]}): used for "
                     f"{len(src)} slice cases {dict(sorted(Counter(e['stratum'][:1] for e in src).items()))}")
        else:
            L.append(f"Adjudication file {Path(path).name}: NOT FOUND")
    L.append("")
    L.append("| run | arm | commit | prompt | model |")
    L.append("|---|---|---|---|---|")
    for arm in order:
        m = M[arm][0]
        L.append(f"| {m['run_id']}{' +resume' if len(M[arm]) > 1 else ''} | {arm} | {m['git_commit']} | "
                 f"{m['prompt_version'] or '-'} | {m['model_requested'] or '-'} |")
    L += ["", "Cells: correct/n (%; Wilson 95% CI). Team reference = adjudicated route where one exists "
          "(disputed excluded), otherwise the course route (B, C only).", ""]
    rows = [
        ("usable outputs (ok/repaired)", lambda s: frac([s["usable"], s["n"]])),
        ("routed to Fraud & Security", lambda s: str(s["to_fraud"])),
        ("routing vs course: A", lambda s: frac(s["route_course"]["A"])),
        ("routing vs course: B", lambda s: frac(s["route_course"]["B"])),
        ("routing vs course: C", lambda s: frac(s["route_course"]["C"])),
        ("routing vs team: A", lambda s: frac(s["route_team"]["A"])),
        ("routing vs team: B", lambda s: frac(s["route_team"]["B"])),
        ("routing vs team: C", lambda s: frac(s["route_team"]["C"])),
        ("routing vs team: all decided", lambda s: frac([len(s["team_correct_ids"]), s["team_decided"]])),
        ("routing vs team: population-weighted", lambda s: f"{100 * s['route_team_weighted']:.1f}%"),
        ("Fraud & Security recall (team)", lambda s: frac(s["fs_recall"])),
        ("Fraud & Security precision (team)", lambda s: frac(s["fs_precision"])),
        ("disputed cases routed to", lambda s: ", ".join(f"{k}:{'F&S' if v == FRAUD else 'Bill' if v else '-'}"
                                                         for k, v in s["disputed"].items()) or "-"),
        ("review recall vs intake ref", lambda s: frac(s["review_intake"]["recall"])),
        ("review specificity vs intake ref", lambda s: frac(s["review_intake"]["specificity"])),
        ("review recall vs course label", lambda s: frac(s["review_course"]["recall"])),
        ("review specificity vs course label", lambda s: frac(s["review_course"]["specificity"])),
        ("review agrees with team (A)", lambda s: frac(s["review_team_A"])),
        ("flagged for review (all)", lambda s: str(s["flag_all"])),
        ("latency median / p95 (s)", lambda s: f"{s['latency_median']:.1f} / {s['latency_p95']:.1f}" if s["latency_median"] else "-"),
        ("prompt tokens (median)", lambda s: f"{s['prompt_tokens_median']:.0f}" if s["prompt_tokens_median"] else "-"),
    ]
    L.append("| metric | " + " | ".join(order) + " |")
    L.append("|---|" + "---|" * len(order))
    for name, f in rows:
        L.append(f"| {name} | " + " | ".join(f(S[x]) for x in order) + " |")
    rule = frozen["decision_rule"]
    L += ["", "## Frozen decision rule", ""]
    for cand, inc in frozen["comparisons"]:
        if cand in S and inc in S and "rule" in S:
            v = verdict(cand, inc, S, entries, adj, rule)
            L.append(f"**{cand} vs {inc}:** {'ADOPT' if v['adopt'] else 'DO NOT ADOPT'} - discordant pairs "
                     f"{cand}-only {v['b_cand_only']}, {inc}-only {v['c_inc_only']}, exact McNemar p = {v['p_mcnemar']:.4f}")
            for k, val in v["checks"].items():
                L.append(f"- {k}: {'pass' if val else 'FAIL'}")
            L.append("")
    text = "\n".join(L)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        Path(a.out).with_suffix(".json").write_text(json.dumps(S, indent=1), encoding="utf-8")
    return text


# ------------------------------------------------------------------ adapter training data
# ------------------------------------------------------------------ discordant-case audit
AUDIT_GUIDE = """Decide from the narrative only. Model outputs and course labels are deliberately hidden.
Routing (team rule, TW5 section 6): ownership is decided by what happened to the account, not by the
words used. A charge, card or account the consumer did not authorise or open is Fraud & Security. A
charge the consumer made but disputes (merchant, fee, posting, payment, refund) is Billing, even when
the narrative says "scam", "stole" or "not mine". Use "Cannot decide" rather than guessing.
Review (team rule): Yes when the narrative itself shows possible fraud, a regulatory or legal claim
(statute, bankruptcy, attorney, "illegal", fee dispute), or too little information to route.
A later customer dispute is not an intake reason."""


def discordant_cases(manifest: dict, runs: list, adj: dict) -> list:
    """Eval cases with no adjudicated label where at least one model arm's usable route differs
    from the course route. Cases on which every arm agrees with the course label cannot change
    a paired comparison between arms, so adjudicating only these is enough for the verdict."""
    preds = []
    for path in runs:
        metas, cases = load_run(path)
        if metas[0]["arm"] != "rule":
            preds.append(predictions(cases, manifest["eval"]))
    out = []
    for e in manifest["eval"]:
        if e["course_record_id"] in adj:
            continue
        routes = {p[e["case_id"]]["route"] for p in preds if p[e["case_id"]]["status"] in USABLE}
        if routes - {e["ref"]["routing"]}:
            out.append(e)
    return out


def cmd_audit(a) -> list:
    import html
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    adj = load_adjudicated(ADJUDICATED)
    todo = discordant_cases(manifest, a.runs, adj)
    by_id = {r["case_id"]: r for r in load_scope(a.dev)[0]}
    random.Random(a.seed).shuffle(todo)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "a6_audit_case_ids.csv").write_text(
        "course_record_id,case_id\n" + "".join(f"{e['course_record_id']},{e['case_id']}\n" for e in todo),
        encoding="utf-8", newline="\n")
    items = []
    for i, e in enumerate(todo, 1):
        text = html.escape(by_id[e["case_id"]]["consumer_complaint_narrative"])
        cid = e["course_record_id"]
        items.append(f"""<section><h3>{i}. {cid}</h3><p class=n>{text}</p>
<label><input type=radio name="r_{cid}" value="Card Fraud &amp; Security"> Fraud &amp; Security</label>
<label><input type=radio name="r_{cid}" value="Card Billing Disputes"> Billing</label>
<label><input type=radio name="r_{cid}" value="DISPUTED"> Cannot decide</label> &nbsp;|&nbsp; Review:
<label><input type=radio name="v_{cid}" value="Yes"> Yes</label>
<label><input type=radio name="v_{cid}" value="No"> No</label>
<input class=note name="n_{cid}" placeholder="one-line reason"></section>""")
    ids = json.dumps([e["course_record_id"] for e in todo])
    guide = "".join(f"<p>{html.escape(par.replace(chr(10), ' '))}</p>" for par in AUDIT_GUIDE.split("Review (team rule)", 1)
                    ).replace("<p>: Yes", "<p><b>Review (team rule):</b> Yes")
    page = f"""<!doctype html><html><head><meta charset=utf-8><title>A6 blind audit</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px}}
section{{border-top:1px solid #ccc;padding:8px 0 14px}} .n{{white-space:pre-wrap;background:#f6f6f6;padding:10px}}
.note{{width:100%;margin-top:6px}} .g{{background:#eef4ff;padding:4px 12px}}
button{{font-size:16px;padding:8px 14px;margin:16px 0}}</style></head><body>
<h1>A6 blind audit: {len(todo)} development cases</h1><div class=g>{guide}</div>
Labeller name: <input id=who> {''.join(items)}
<button onclick="dl()">Download a6_audit_labels.csv</button>
<script>const ids={ids};
function v(n){{const x=document.querySelector('input[name="'+n+'"]:checked');return x?x.value:''}}
function q(s){{return '"'+String(s).replace(/"/g,'""')+'"'}}
function dl(){{const who=document.getElementById('who').value||'labeller';
let rows=['course_record_id,labeller,team_route,route_status,team_review,note'];
for(const id of ids){{const r=v('r_'+id);if(!r)continue;
rows.push([id,q(who),q(r),r==='DISPUTED'?'disputed':'single_labeller',v('v_'+id),
q(document.querySelector('input[name="n_'+id+'"]').value)].join(','))}}
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.join('\\n')+'\\n'],{{type:'text/csv'}}));
a.download='a6_audit_labels.csv';a.click()}}</script></body></html>"""
    (out_dir / "a6_audit_sheet.html").write_text(page, encoding="utf-8")
    print(f"{len(todo)} discordant unadjudicated cases {dict(sorted(Counter(e['stratum'][:1] for e in todo).items()))}"
          f" -> {out_dir / 'a6_audit_sheet.html'} (contains narratives: keep local)")
    return todo


def sft_target(row: dict) -> dict:
    """Assistant target for one training case (row must carry the data_guard annotations)."""
    return {"fraud_or_security_concern": row["fraud_indicator"] == "Yes",
            "recommended_routing": row["routing_destination"],
            "human_review_required": row["review_ref_intake"] == "Yes"}


def cmd_export_sft(a) -> dict:
    """Write the training pool as chat-format JSONL for a LoRA adapter (adapter-v1 prompt).

    Targets: course route, course fraud flag, and the INTAKE review reference - not
    human_review_required, whose Yes value partly records a dispute made after the bank
    responded (training on it would teach the adapter a post-outcome event)."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scope, _ = load_scope(a.dev)
    by_id = {r["case_id"]: r for r in scope}
    eval_ids = {e["case_id"] for e in manifest["eval"] + manifest["tuning"]}
    stats = Counter()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as out:
        for cid in manifest["train_pool"]["case_ids"]:
            if cid in eval_ids:
                raise g.DataGuardError(f"training pool contains evaluation case {cid}")
            r = by_id[cid]
            payload = g.build_model_input(r)
            g.assert_input_boundary(payload, r)
            target = sft_target(r)
            msgs = build_messages("adapter-v1", payload["consumer_complaint_narrative"])
            msgs.append({"role": "assistant", "content": json.dumps(target)})
            out.write(json.dumps({"case_id": cid, "messages": msgs}, ensure_ascii=False) + "\n")
            stats["n"] += 1
            stats["route=" + r["routing_destination"]] += 1
            stats["stratum=" + r["stratum"][:1]] += 1
            stats["review_intake=" + r["review_ref_intake"]] += 1
            stats["review_course_differs_from_intake"] += r["human_review_required"] != r["review_ref_intake"]
    for k in ("stratum=A", "stratum=B", "stratum=C"):
        stats.setdefault(k, 0)
    print(json.dumps(dict(sorted(stats.items())), indent=1))
    print(f"-> {a.out} (git-ignored: contains course narratives)")
    return dict(stats)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("manifest")
    p.add_argument("--dev", required=True)
    p = sub.add_parser("rule")
    p.add_argument("--dev", required=True)
    p.add_argument("--slice", default="eval", choices=("eval", "tuning"))
    p = sub.add_parser("run")
    p.add_argument("--dev", required=True)
    p.add_argument("--arm", required=True, choices=sorted(ARMS))
    p.add_argument("--slice", default="eval", choices=("eval", "tuning"))
    p.add_argument("--model")
    p.add_argument("--resume")
    p = sub.add_parser("score")
    p.add_argument("runs", nargs="+")
    p.add_argument("--slice", default="eval", choices=("eval", "tuning"))
    p.add_argument("--adjudicated", nargs="+", default=[str(ADJUDICATED)],
                   help="adjudication CSVs; earlier files take precedence")
    p.add_argument("--out")
    p = sub.add_parser("audit")
    p.add_argument("runs", nargs="+")
    p.add_argument("--dev", required=True)
    p.add_argument("--out-dir", default="exports")
    p.add_argument("--seed", type=int, default=13)
    p = sub.add_parser("export-sft")
    p.add_argument("--dev", required=True)
    p.add_argument("--out", default="exports/a6_sft_train.jsonl")
    a = ap.parse_args(argv)

    if a.cmd == "manifest":
        m = build_manifest(a.dev)
        problems = check_manifest(m)
        if problems:
            sys.exit("Manifest check failed: " + "; ".join(problems))
        MANIFEST.write_text(json.dumps(m, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"eval {len(m['eval'])} | tuning {len(m['tuning'])} | train pool {m['train_pool']['n']} "
              f"{m['train_pool']['strata']} | scope {m['scope_n']} {m['scope_strata']}")
        print(f"-> {MANIFEST.relative_to(REPO_ROOT)} (disjointness checks passed)")
    elif a.cmd == "rule":
        cmd_rule(a)
    elif a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "score":
        cmd_score(a)
    elif a.cmd == "audit":
        cmd_audit(a)
    elif a.cmd == "export-sft":
        cmd_export_sft(a)


if __name__ == "__main__":
    main()
