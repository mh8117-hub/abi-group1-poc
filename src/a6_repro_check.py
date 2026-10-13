"""A6 validation: does the harness's base arm reproduce the A4 baseline outputs?

The base arm uses the unchanged A4 prompt (baseline-v1) and settings (temperature 0, seed 42).
If the harness adds nothing to the call path, the raw model text should match the A4 logs of
2 October for the same 81 cases.

  python -m src.a6_repro_check runs/<a6 base eval>.jsonl runs/20261002T213121Z_baseline.jsonl runs/20261002T232853Z_baseline.jsonl
"""
import argparse
import json
from pathlib import Path


def load_cases(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("type", "case") == "case" and r.get("parse_status") != "call_error":
            out[r["case_id"]] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a6_run")
    ap.add_argument("a4_runs", nargs="+")
    a = ap.parse_args()
    new = load_cases(a.a6_run)
    old = {}
    for p in a.a4_runs:
        old.update(load_cases(p))
    both = sorted(set(new) & set(old), key=int)
    same_raw = [c for c in both if new[c]["raw_text"] == old[c]["raw_text"]]
    keys = ("recommended_routing", "human_review_required", "fraud_or_security_concern")
    same_dec = [c for c in both if all(new[c]["record"].get(k) == old[c]["record"].get(k) for k in keys)]
    print(f"cases in both: {len(both)}; raw text identical: {len(same_raw)}/{len(both)}; "
          f"decisions identical: {len(same_dec)}/{len(both)}")
    for c in both:
        if c not in same_dec:
            print("  decision differs:", c, {k: (old[c]["record"].get(k), new[c]["record"].get(k)) for k in keys})


if __name__ == "__main__":
    main()
