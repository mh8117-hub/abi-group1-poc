"""Build the small, reproducible development sample used for the Assignment 4 baseline run.

Source: Development set only (credit card / Billing disputes = 653 cases).
The Held-out Evaluation set is NOT read here: per team plan P7 it is used once, on a frozen
commit, after model/prompt choices are fixed.

Sample = 12 cases, 4 from each stratum that matters to the team's evidence design:
  A  fraud language present, reference owner = Card Billing Disputes   (rule-error subgroup, P3)
  B  fraud language present, reference owner = Card Fraud & Security
  C  no fraud language,      reference owner = Card Billing Disputes
Narratives that also appear verbatim in the held-out file are excluded (pass --exclude-ids).

Usage:
  python -m src.select_sample --dev path/to/ABI_Bank_Complaints_Development_8000.csv
"""
import argparse
import csv
import random
from pathlib import Path

KEEP = ["case_id", "sample_group", "word_count", "special_case_flags", "label_status",
        "consumer_complaint_narrative", "routing_destination", "fraud_indicator",
        "human_review_required", "regulatory_indicator"]

# 2 dev billing-dispute narratives duplicated verbatim in the held-out file (found in profiling)
DEFAULT_EXCLUDE = {'2312573','2254079','2123714','2254851','2124250'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", required=True)
    ap.add_argument("--out", default="data/dev_sample_cases.csv")
    ap.add_argument("--per-group", type=int, default=4)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--exclude-ids", default="", help="comma-separated case_ids to exclude")
    args = ap.parse_args()

    exclude = DEFAULT_EXCLUDE | {s.strip() for s in args.exclude_ids.split(",") if s.strip()}
    with open(args.dev, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r["product"] == "Credit card" and r["issue"] == "Billing disputes"
                and r["dataset_split"] == "Development" and r["case_id"] not in exclude]

    groups = {
        "A_fraudtext_billing_owner": [r for r in rows if r["fraud_indicator"] == "Yes"
                                      and r["routing_destination"] == "Card Billing Disputes"],
        "B_fraudtext_fraud_owner": [r for r in rows if r["fraud_indicator"] == "Yes"
                                    and r["routing_destination"] == "Card Fraud & Security"],
        "C_no_fraudtext_billing_owner": [r for r in rows if r["fraud_indicator"] == "No"],
    }
    rng = random.Random(args.seed)
    sample = []
    for name, members in groups.items():
        members = sorted(members, key=lambda r: int(r["case_id"]))
        for r in rng.sample(members, args.per_group):
            r = dict(r, sample_group=name)
            sample.append({k: r[k] for k in KEEP})
        print(f"{name}: pool={len(members)} sampled={args.per_group}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEEP)
        w.writeheader()
        w.writerows(sample)
    print(f"wrote {len(sample)} cases -> {args.out}")


if __name__ == "__main__":
    main()
