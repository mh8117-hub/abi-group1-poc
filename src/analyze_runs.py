"""Compute every figure reported in the Assignment 4 record directly from run logs.

  python -m src.analyze_runs runs/<run>_baseline.jsonl [more runs...] [--compare runs/<other>_baseline.jsonl]
                             [--dev path/to/Development_8000.csv]

Reference rule = the team's deterministic baseline: route to Card Fraud & Security when the
reference fraud_indicator is Yes, else Card Billing Disputes (it uses a label the model never sees).
"""
import argparse
import csv
import json
import statistics as st

FRAUD, BILLING = "Card Fraud & Security", "Card Billing Disputes"


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def pct(a, b):
    return f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def pearson(x, y):
    mx, my = st.mean(x), st.mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    return num / den if den else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="+", help="one or more run logs; later runs override earlier ones per case_id")
    ap.add_argument("--compare")
    ap.add_argument("--dev")
    a = ap.parse_args()
    merged = {}
    for path in a.run:
        for r in load(path):
            if r.get("parse_status") != "call_error":
                merged[r["case_id"]] = r
    R = list(merged.values())
    n = len(R)
    print(f"runs {', '.join(a.run)}: {n} distinct cases with a model response")
    print("parse status:", {s: sum(r["parse_status"] == s for r in R) for s in ("ok", "repaired", "invalid", "failed")})

    rule = lambda r: FRAUD if r["reference"]["fraud_indicator"] == "Yes" else BILLING
    model = lambda r: r["record"].get("recommended_routing")
    print("\nstratum | n | model routing | reference rule | review label Yes | model review Yes | review correct | fraud flag agrees")
    for g in sorted({r["sample_group"] for r in R}) + ["ALL"]:
        G = R if g == "ALL" else [r for r in R if r["sample_group"] == g]
        m = sum(model(r) == r["reference"]["routing"] for r in G)
        rl = sum(rule(r) == r["reference"]["routing"] for r in G)
        ly = sum(r["reference"]["human_review_required"] == "Yes" for r in G)
        my = sum(r["record"].get("human_review_required") is True for r in G)
        rc = sum(r.get("review_match") is True for r in G)
        ff = sum(r.get("fraud_flag_match") is True for r in G)
        print(f"{g} | {len(G)} | {pct(m, len(G))} | {pct(rl, len(G))} | {ly} | {my} | {pct(rc, len(G))} | {pct(ff, len(G))}")

    to_billing = sum(model(r) == BILLING for r in R)
    print(f"\nmodel routed to Billing: {pct(to_billing, n)}")
    yes = [r for r in R if r["reference"]["human_review_required"] == "Yes"]
    no = [r for r in R if r["reference"]["human_review_required"] == "No"]
    print(f"review recall {pct(sum(r['record'].get('human_review_required') is True for r in yes), len(yes))}; "
          f"review specificity {pct(sum(r['record'].get('human_review_required') is False for r in no), len(no))}")
    ft = [r for r in R if r["record"].get("fraud_or_security_concern") is True]
    print(f"model fraud=true: {len(ft)}; of those routed to Fraud & Security: {sum(model(r) == FRAUD for r in ft)}")
    dis = [r for r in R if model(r) != rule(r)]
    print(f"model-vs-rule disagreements: {len(dis)}; model right {sum(model(r) == r['reference']['routing'] for r in dis)}, "
          f"rule right {sum(rule(r) == r['reference']['routing'] for r in dis)}")

    lat = sorted(r["latency_s"] for r in R)
    ct = [r["usage"]["completion_tokens"] for r in R]
    pt = [r["usage"]["prompt_tokens"] for r in R]
    p95 = lat[max(0, -(-95 * n // 100) - 1)]
    tps = [r["usage"]["completion_tokens"] / r["latency_s"] for r in R]
    print(f"\nlatency s: median {st.median(lat):.1f}, p95 {p95:.1f}, max {lat[-1]:.1f}; over 10 s: {sum(x > 10 for x in lat)}/{n}")
    print(f"r(latency, completion tokens) = {pearson([r['latency_s'] for r in R], ct):.3f}; "
          f"r(latency, prompt tokens) = {pearson([r['latency_s'] for r in R], pt):.3f}")
    print(f"completion tokens {min(ct)}-{max(ct)} (median {st.median(ct):.0f}); prompt tokens {min(pt)}-{max(pt)}; "
          f"throughput {min(tps):.1f}-{max(tps):.1f} tokens/s")

    if a.compare:
        C = {r["case_id"]: r for r in load(a.compare) if r.get("parse_status") != "call_error"}
        both = [r for r in R if r["case_id"] in C]
        same_raw = sum(r["raw_text"] == C[r["case_id"]]["raw_text"] for r in both)
        same_dec = sum(all(r["record"].get(k) == C[r["case_id"]]["record"].get(k) for k in
                           ("recommended_routing", "human_review_required", "fraud_or_security_concern")) for r in both)
        print(f"\nreproducibility vs {a.compare}: raw text identical {same_raw}/{len(both)}; decisions identical {same_dec}/{len(both)}")

    if a.dev:
        rows = [r for r in csv.DictReader(open(a.dev, encoding="utf-8-sig"))
                if r["product"] == "Credit card" and r["issue"] == "Billing disputes"]
        pot = [r for r in rows if r["regulatory_indicator"] == "Potential"]
        print(f"\ndev billing disputes: {len(rows)}; potential-regulatory: {len(pot)}; "
              f"of those labelled human_review = No: {sum(r['human_review_required'] == 'No' for r in pot)}")


if __name__ == "__main__":
    main()
