"""TE8 interim two-person rule: detection after a review session (read-only).

Team decision 4 (TE8 register, R1): until Mingyu's C1-C8 control is called by the
team app, any override from Card Fraud & Security to Card Billing Disputes needs a
second named person, written in the override note as ``second: <name>``.
The rule is procedural; this script is its detectable trigger. It reads the
team app's audit log (JSONL written by src.intake_app) and never writes to it.

    python te8/interim_two_person_check.py runs/a8_dgx/<run>/audit.jsonl

Exit 0: no breach. Exit 1: at least one de-escalation without a valid second
person (list it for lookback and suspend de-escalation testing). Exit 2: the log
cannot be read or a line is not valid JSON (reconcile the log first).

Standard library only. Prints case IDs, reviewer IDs and reason codes, never
narrative text.
"""
import argparse
import json
import re
import sys

FRAUD_QUEUE = "Card Fraud & Security"
BILLING_QUEUE = "Card Billing Disputes"
SECOND_RX = re.compile(r"\bsecond:\s*([^;,\n]+)", re.IGNORECASE)


def second_person(note, reviewer_id):
    """The named second person in an override note, or None.
    A name equal to the reviewer (case-insensitive) does not count."""
    m = SECOND_RX.search(note or "")
    if not m:
        return None
    name = m.group(1).strip()
    if not name or name.casefold() == (reviewer_id or "").strip().casefold():
        return None
    return name


def deescalations(records):
    """Yield (record, override) for every move out of Fraud & Security into Billing."""
    for r in records:
        for o in r.get("overrides") or []:
            if o.get("from_queue") == FRAUD_QUEUE and o.get("to_queue") == BILLING_QUEUE:
                yield r, o


def check(records):
    """Return a list of findings, one per de-escalation, with breach flag."""
    out = []
    for r, o in deescalations(records):
        if r.get("canary"):
            continue  # canaries are scored, never routed; the vigilance control judges them
        reviewer = o.get("reviewer_id") or r.get("reviewer_id")
        second = second_person(o.get("note"), reviewer)
        out.append({"case_id": r.get("case_id"), "reviewer_id": reviewer,
                    "reason_code": o.get("reason_code"), "second": second,
                    "breach": second is None})
    return out


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("audit_log")
    args = ap.parse_args(argv)
    try:
        records = load(args.audit_log)
    except (OSError, ValueError) as exc:
        print(f"CANNOT READ AUDIT LOG ({exc}); reconcile before judging the session", file=sys.stderr)
        return 2
    findings = check(records)
    breaches = [f for f in findings if f["breach"]]
    print(f"de-escalations out of {FRAUD_QUEUE}: {len(findings)}; without a second person: {len(breaches)}")
    for f in findings:
        tag = "BREACH -> lookback" if f["breach"] else f"ok (second: {f['second']})"
        print(f"  case {f['case_id']} reviewer {f['reviewer_id']} reason {f['reason_code']}: {tag}")
    return 1 if breaches else 0


if __name__ == "__main__":
    sys.exit(main())
