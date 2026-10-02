"""Run the unadapted baseline over a set of development cases and save execution evidence.

Usage (from the repo root):
  python -m src.run_baseline --list-models
  python -m src.run_baseline --cases data/dev_sample_cases.csv

Writes runs/<timestamp>_baseline.jsonl (one record per case: raw model text, parsed record,
parse status, latency, reference labels) and runs/<timestamp>_summary.json.
"""
import argparse
import csv
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.client import ModelCallError, chat, list_models
from src.config import REPO_ROOT, load_config
from src.parser import parse_model_output
from src.prompt import PROMPT_VERSION, build_messages


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=REPO_ROOT,
                               capture_output=True, text=True).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def yes(v: str) -> bool:
    return str(v).strip().lower() == "yes"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="data/dev_sample_cases.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    if args.list_models:
        print("\n".join(list_models(cfg)))
        return

    with open(args.cases, encoding="utf-8-sig", newline="") as fh:
        cases = list(csv.DictReader(fh))
    # Control for team plan P7: refuse to run on held-out data during development.
    if any("heldout" in (c.get("dataset_split", "") + args.cases).lower().replace("-", "")
           .replace("_", "").replace(" ", "") for c in cases):
        sys.exit("Refusing to run: held-out evaluation data is reserved for the frozen run (P7).")
    if args.limit:
        cases = cases[: args.limit]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    jsonl_path = out_dir / f"{stamp}_baseline.jsonl"
    meta = {
        "run_id": stamp,
        "git_commit": git_commit(),
        "endpoint": cfg["OPENWEBUI_BASE_URL"] + "/api/chat/completions",
        "model_requested": cfg["MODEL_ID"],
        "temperature": float(cfg["TEMPERATURE"]),
        "seed": int(cfg["SEED"]),
        "max_tokens": int(cfg["MAX_TOKENS"]),
        "prompt_version": PROMPT_VERSION,
        "cases_file": args.cases,
        "client_host": platform.node(),
        "client_platform": platform.platform(),
        "python": platform.python_version(),
    }
    print(json.dumps(meta, indent=2))

    results = []
    with open(jsonl_path, "w", encoding="utf-8") as out:
        for i, case in enumerate(cases, 1):
            rec = {"case_id": case["case_id"], "sample_group": case.get("sample_group"),
                   "word_count": case.get("word_count"),
                   "reference": {"routing": case["routing_destination"],
                                 "fraud_indicator": case["fraud_indicator"],
                                 "human_review_required": case["human_review_required"]}}
            try:
                resp = chat(cfg, build_messages(case["consumer_complaint_narrative"]))
                parsed = parse_model_output(resp["text"])
                rec.update(model_returned=resp["model"], latency_s=resp["latency_s"],
                           usage=resp["usage"], raw_text=resp["text"], **parsed.as_dict())
                r = parsed.record
                rec["routing_match"] = (r.get("recommended_routing") == case["routing_destination"])
                rec["review_match"] = (r.get("human_review_required") == yes(case["human_review_required"]))
                rec["fraud_flag_match"] = (r.get("fraud_or_security_concern") == yes(case["fraud_indicator"]))
            except ModelCallError as exc:
                rec.update(parse_status="call_error", errors=[str(exc)])
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            results.append(rec)
            print(f"[{i:>2}/{len(cases)}] case {rec['case_id']:>8} {rec.get('sample_group','')[:12]:<12} "
                  f"{rec['parse_status']:<10} {rec.get('latency_s','-'):>6}s "
                  f"route={'OK ' if rec.get('routing_match') else 'X  '}"
                  f"review={'OK ' if rec.get('review_match') else 'X  '}"
                  f"fraud={'OK' if rec.get('fraud_flag_match') else 'X '}")

    def count(key):
        return sum(1 for r in results if r.get(key))

    lat = sorted(r["latency_s"] for r in results if "latency_s" in r)
    summary = dict(meta, n_cases=len(results),
                   parse_status={s: sum(1 for r in results if r["parse_status"] == s)
                                 for s in ("ok", "repaired", "invalid", "failed", "call_error")},
                   routing_correct=count("routing_match"),
                   review_flag_correct=count("review_match"),
                   fraud_flag_correct=count("fraud_flag_match"),
                   by_group={g: {"n": sum(1 for r in results if r.get("sample_group") == g),
                                 "routing_correct": sum(1 for r in results
                                                        if r.get("sample_group") == g and r.get("routing_match"))}
                             for g in sorted({r.get("sample_group") for r in results})},
                   latency_s={"min": lat[0] if lat else None, "max": lat[-1] if lat else None,
                              "median": lat[len(lat) // 2] if lat else None},
                   output_file=str(jsonl_path.relative_to(REPO_ROOT)))
    (out_dir / f"{stamp}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("n_cases", "parse_status", "routing_correct",
                                              "review_flag_correct", "fraud_flag_correct",
                                              "by_group", "latency_s")}, indent=2))


if __name__ == "__main__":
    main()
