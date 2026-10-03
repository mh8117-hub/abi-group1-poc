"""Data guard for Group 1's PoC data pipeline and test harness (Assignment 5 candidate).

One small, reusable component that every run, sample builder or evaluation script can call
before touching the course data. Standard library only.

What it enforces
  1. Schema      - the Development file has the 29 documented columns and valid code values.
  2. Split       - refuses held-out data (file name, dataset_split, HEL- record id), and
                   excludes Development rows whose narrative also appears in the held-out
                   file (matched by SHA-256 of the normalised text, so the held-out text
                   itself is never stored in the repo or read during development).
  3. Scope       - Credit card / Billing disputes only (Gate 1 scope).
  4. Boundary    - the model sees the complaint narrative and nothing else. Target fields,
                   their surrogates and post-outcome fields are rejected if they reach the
                   model payload.
  5. Label risk  - annotates each case with audit columns so evaluations can be stratified:
                   review_post_outcome_only : the review label is Yes ONLY because the
                       consumer later disputed the company's response (post-outcome field).
                   review_ref_intake        : review label with that post-outcome component
                       removed (urgency High/Critical or escalation Yes).
                   fraud_keyword_hit        : narrative contains a fraud keyword; the
                       reference fraud flag (and so Fraud & Security routing) is close to this.
                   legal_language_hit       : narrative cites a statute, bankruptcy, attorney,
                       "illegal", etc. Compared against regulatory_indicator.
                   stratum                  : A/B/C evidence strata used since Assignment 4.

Usage (repo root):
  python -m src.data_guard --make-heldout-hashes --heldout PATH/ABI_..._Heldout_Evaluation_1500.csv
  python -m src.data_guard --dev PATH/ABI_Bank_Complaints_Development_8000.csv --report
  python -m src.data_guard --dev PATH/... --out data/dev_billing_guarded.csv
"""
import argparse
import csv
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HASHES = REPO_ROOT / "data" / "heldout_narrative_sha256.txt"

# ---------------------------------------------------------------- 1. schema
EXPECTED_COLUMNS = (
    "course_record_id", "dataset_split", "intended_use", "case_id", "date_received", "product",
    "sub_product", "issue", "sub_issue", "consumer_complaint_narrative", "submitted_via",
    "company_response_to_consumer", "timely_response", "consumer_disputed", "reference_summary",
    "urgency_level", "severity_level", "routing_destination", "escalation_required",
    "human_review_required", "recommended_next_action", "fraud_indicator",
    "regulatory_indicator", "customer_harm_level", "label_confidence", "label_status",
    "special_case_flags", "word_count", "label_provenance",
)
ALLOWED = {
    "dataset_split": {"Development"},
    "human_review_required": {"Yes", "No"},
    "escalation_required": {"Yes", "No"},
    "fraud_indicator": {"Yes", "No"},
    "regulatory_indicator": {"Potential", "No"},
    "consumer_disputed": {"Yes", "No", ""},   # blank is real missingness, never "No"
    "timely_response": {"Yes", "No"},
    "urgency_level": {"Low", "Medium", "High", "Critical"},
    "label_status": {"Clear", "Judgment Required", "Insufficient Evidence"},
}

# ---------------------------------------------------------------- 3. scope
SCOPE = {"product": "Credit card", "issue": "Billing disputes"}
DESTINATIONS = ("Card Billing Disputes", "Card Fraud & Security")

# ---------------------------------------------------------------- 4. boundary
MODEL_INPUT_FIELDS = ("consumer_complaint_narrative",)
SCOPE_ONLY_FIELDS = ("product", "issue")          # used to select cases, never sent to the model
TARGET_FIELDS = ("routing_destination", "human_review_required")
REFERENCE_FIELDS = ("fraud_indicator", "regulatory_indicator")   # evaluation only
SURROGATE_FIELDS = (                                # encode a target or the labelling rule
    "recommended_next_action",   # 1:1 with fraud_indicator in scope (486 / 167)
    "special_case_flags",        # contains 'fraud_security' == fraud_indicator
    "escalation_required", "urgency_level", "severity_level", "customer_harm_level",
    "label_status", "label_confidence", "reference_summary",
)
POST_OUTCOME_FIELDS = ("company_response_to_consumer", "timely_response", "consumer_disputed")
FORBIDDEN_IN_MODEL_INPUT = frozenset(
    TARGET_FIELDS + REFERENCE_FIELDS + SURROGATE_FIELDS + POST_OUTCOME_FIELDS + SCOPE_ONLY_FIELDS
    + ("dataset_split", "intended_use", "label_provenance", "word_count", "sub_issue",
       "sub_product", "submitted_via", "date_received")
)

# ---------------------------------------------------------------- 5. label-risk lexicons
FRAUD_RX = re.compile(r"fraud|scam|unauthori[sz]|stolen|identity theft|\btheft\b|hack|"
                      r"compromised|skimm|breach|\bsteal")
LEGAL_RX = re.compile(r"fair credit|\bfcba\b|\bfcra\b|truth in lending|\btila\b|regulation z|"
                      r"bankrupt|attorney|lawyer|lawsuit|\bsue\b|illegal|\bcfpb\b|"
                      r"consumer financial protection|violat")


class DataGuardError(RuntimeError):
    """Raised when data would break the schema, the split rule or the input boundary."""


# ---------------------------------------------------------------- helpers
def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def _looks_heldout(path: str) -> bool:
    key = re.sub(r"[^a-z]", "", Path(path).name.lower())
    return "heldout" in key or "evaluation1500" in key


def read_csv(path: str) -> list:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return rows, tuple(reader.fieldnames or ())


# ---------------------------------------------------------------- 1 + 2. load
def validate_schema(rows: list, columns: tuple) -> list:
    """Return a list of human-readable problems (empty list = valid)."""
    problems = []
    missing = [c for c in EXPECTED_COLUMNS if c not in columns]
    extra = [c for c in columns if c not in EXPECTED_COLUMNS]
    if missing:
        problems.append(f"missing columns: {missing}")
    if extra:
        problems.append(f"unexpected columns: {extra}")
    if missing:
        return problems
    seen = set()
    for i, r in enumerate(rows, 2):
        for col, allowed in ALLOWED.items():
            if r[col] not in allowed:
                problems.append(f"line {i}: {col}={r[col]!r} not in {sorted(allowed)}")
        if not r["consumer_complaint_narrative"].strip():
            problems.append(f"line {i}: empty narrative")
        if r["case_id"] in seen:
            problems.append(f"line {i}: duplicate case_id {r['case_id']}")
        seen.add(r["case_id"])
        if len(problems) > 50:
            problems.append("... stopped after 50 problems")
            break
    return problems


def refuse_heldout(path: str, rows: list) -> None:
    if _looks_heldout(path):
        raise DataGuardError(f"Refusing held-out file {Path(path).name}: reserved for the frozen "
                             "evaluation run (team rule P7).")
    bad = [r.get("course_record_id") for r in rows
           if r.get("dataset_split", "").replace(" ", "").lower().startswith("heldout")
           or r.get("course_record_id", "").startswith("HEL-")]
    if bad:
        raise DataGuardError(f"Refusing: {len(bad)} held-out record(s) present, e.g. {bad[:3]}")


def load_heldout_hashes(path=DEFAULT_HASHES) -> set:
    p = Path(path)
    if not p.exists():
        raise DataGuardError(f"Held-out fingerprint file not found: {p}. Run --make-heldout-hashes.")
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def make_heldout_hashes(heldout_path: str, out_path=DEFAULT_HASHES) -> int:
    """Read ONLY the narrative column of the held-out file and store one-way hashes."""
    rows, _ = read_csv(heldout_path)
    hashes = sorted({fingerprint(r["consumer_complaint_narrative"]) for r in rows})
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        "# SHA-256 of normalised held-out narratives (lowercase, non-alphanumerics -> space).\n"
        "# One-way: used only to exclude Development rows that duplicate held-out text.\n"
        + "\n".join(hashes) + "\n", encoding="utf-8")
    return len(hashes)


def load_development(path: str, heldout_hashes=None, strict: bool = True):
    """Load, validate and protect the Development file. Returns (rows, log)."""
    rows, columns = read_csv(path)
    refuse_heldout(path, rows)
    problems = validate_schema(rows, columns)
    if problems and strict:
        raise DataGuardError("Schema check failed:\n  " + "\n  ".join(problems[:10]))
    log = {"rows_read": len(rows), "schema_problems": problems}
    if heldout_hashes is not None:
        kept = [r for r in rows if fingerprint(r["consumer_complaint_narrative"]) not in heldout_hashes]
        log["excluded_heldout_overlap"] = sorted(
            r["case_id"] for r in rows
            if fingerprint(r["consumer_complaint_narrative"]) in heldout_hashes)
        rows = kept
    return rows, log


# ---------------------------------------------------------------- 3. scope
def in_scope(rows: list) -> list:
    out = [r for r in rows if all(r[k] == v for k, v in SCOPE.items())]
    off = {r["routing_destination"] for r in out} - set(DESTINATIONS)
    if off:
        raise DataGuardError(f"In-scope rows route outside the two destinations: {sorted(off)}")
    return out


# ---------------------------------------------------------------- 4. boundary
def build_model_input(row: dict) -> dict:
    """The only object the harness may pass to the prompt builder."""
    return {f: row[f] for f in MODEL_INPUT_FIELDS}


def assert_input_boundary(payload: dict, row: dict = None) -> None:
    """Raise if a model payload carries anything beyond the narrative.

    Checks keys, and (when the source row is given) that no target/surrogate VALUE has been
    pasted into the payload text, e.g. a reference_summary or a routing label string."""
    leaked = sorted(set(payload) & FORBIDDEN_IN_MODEL_INPUT)
    unknown = sorted(set(payload) - set(MODEL_INPUT_FIELDS) - FORBIDDEN_IN_MODEL_INPUT)
    if leaked:
        raise DataGuardError(f"Input boundary violated: forbidden field(s) in model input {leaked}")
    if unknown:
        raise DataGuardError(f"Input boundary violated: undeclared field(s) in model input {unknown}")
    if row is not None:
        text = " ".join(str(v) for v in payload.values())
        narrative = row.get("consumer_complaint_narrative", "")
        for f in ("reference_summary", "recommended_next_action", "routing_destination"):
            v = row.get(f, "")
            if v and v in text and v not in narrative:
                raise DataGuardError(f"Input boundary violated: value of {f} found in model input")


# ---------------------------------------------------------------- 5. annotate
def annotate(row: dict) -> dict:
    """Return audit columns for one in-scope row (does not modify the row)."""
    hi = row["urgency_level"] in ("High", "Critical")
    esc = row["escalation_required"] == "Yes"
    disputed = row["consumer_disputed"] == "Yes"
    text = row["consumer_complaint_narrative"].lower()
    fraud_ref = row["fraud_indicator"] == "Yes"
    billing = row["routing_destination"] == "Card Billing Disputes"
    return {
        "review_post_outcome_only": "Yes" if (row["human_review_required"] == "Yes"
                                              and disputed and not hi and not esc) else "No",
        "review_ref_intake": "Yes" if (hi or esc) else "No",
        "fraud_keyword_hit": "Yes" if FRAUD_RX.search(text) else "No",
        "legal_language_hit": "Yes" if LEGAL_RX.search(text) else "No",
        "stratum": ("A_fraudtext_billing_owner" if fraud_ref and billing else
                    "B_fraudtext_fraud_owner" if fraud_ref else "C_no_fraudtext_billing_owner"),
    }


def review_rule_check(rows: list) -> int:
    """Number of rows where human_review_required != (High/Critical OR disputed OR escalation)."""
    bad = 0
    for r in rows:
        rule = (r["urgency_level"] in ("High", "Critical") or r["consumer_disputed"] == "Yes"
                or r["escalation_required"] == "Yes")
        bad += (r["human_review_required"] == "Yes") != rule
    return bad


# ---------------------------------------------------------------- report
def audit_report(all_rows: list, scoped: list, log: dict) -> str:
    ann = [dict(r, **annotate(r)) for r in scoped]
    n = len(ann)
    c = lambda pred: sum(1 for a in ann if pred(a))
    rev_yes = c(lambda a: a["human_review_required"] == "Yes")
    po = c(lambda a: a["review_post_outcome_only"] == "Yes")
    lines = [
        "A5 data guard report (Development file only)",
        f"rows read {log['rows_read']}; schema problems {len(log['schema_problems'])}; "
        f"excluded for held-out overlap {len(log.get('excluded_heldout_overlap', []))} "
        f"{log.get('excluded_heldout_overlap', [])}",
        f"in scope (Credit card / Billing disputes): {n}",
        "",
        "Targets",
        f"  routing: {dict(Counter(a['routing_destination'] for a in ann))}",
        f"  human_review_required Yes: {rev_yes}/{n}",
        f"  strata: {dict(sorted(Counter(a['stratum'] for a in ann).items()))}",
        "",
        "Finding 1 - review label contains a post-outcome component",
        f"  rule review == High/Critical OR consumer_disputed OR escalation: mismatches "
        f"{review_rule_check(all_rows)}/{len(all_rows)} (all dev), {review_rule_check(scoped)}/{n} (scope)",
        f"  review Yes ONLY because consumer later disputed: {po}/{rev_yes} Yes labels",
        f"  review_ref_intake Yes: {c(lambda a: a['review_ref_intake'] == 'Yes')}/{n}",
        "",
        "Finding 2 - Fraud & Security routing follows a lexical fraud flag",
        f"  F&S with fraud_indicator=No: {c(lambda a: a['routing_destination'] == DESTINATIONS[1] and a['fraud_indicator'] == 'No')}",
        f"  fraud keyword regex agrees with fraud_indicator: "
        f"{c(lambda a: (a['fraud_keyword_hit'] == 'Yes') == (a['fraud_indicator'] == 'Yes'))}/{n}",
        "",
        "Finding 3 - regulatory flag misses explicit legal language",
        f"  legal language present: {c(lambda a: a['legal_language_hit'] == 'Yes')}; of these "
        f"regulatory=No: {c(lambda a: a['legal_language_hit'] == 'Yes' and a['regulatory_indicator'] == 'No')}"
        f" (review=No as well: {c(lambda a: a['legal_language_hit'] == 'Yes' and a['regulatory_indicator'] == 'No' and a['human_review_required'] == 'No')})",
        f"  regulatory=Potential but review=No: "
        f"{c(lambda a: a['regulatory_indicator'] == 'Potential' and a['human_review_required'] == 'No')}",
        f"  Insufficient Evidence but review=No: "
        f"{c(lambda a: a['label_status'] == 'Insufficient Evidence' and a['human_review_required'] == 'No')}"
        f"/{c(lambda a: a['label_status'] == 'Insufficient Evidence')}",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dev")
    ap.add_argument("--heldout", help="only with --make-heldout-hashes")
    ap.add_argument("--make-heldout-hashes", action="store_true")
    ap.add_argument("--hashes", default=str(DEFAULT_HASHES))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", help="write guarded in-scope cases + audit columns to this CSV")
    a = ap.parse_args(argv)

    if a.make_heldout_hashes:
        if not a.heldout:
            sys.exit("--make-heldout-hashes needs --heldout PATH")
        print(f"wrote {make_heldout_hashes(a.heldout, a.hashes)} fingerprints -> {a.hashes}")
        return
    if not a.dev:
        sys.exit("--dev PATH is required")
    rows, log = load_development(a.dev, load_heldout_hashes(a.hashes))
    all_rows, _ = read_csv(a.dev)
    scoped = in_scope(rows)
    for r in scoped:                                   # boundary self-test on every case
        assert_input_boundary(build_model_input(r), r)
    if a.report:
        print(audit_report(all_rows, scoped, log))
    if a.out:
        keep = ("course_record_id", "case_id", "date_received", "word_count",
                "consumer_complaint_narrative") + TARGET_FIELDS + REFERENCE_FIELDS + (
                "urgency_level", "escalation_required", "consumer_disputed", "label_status")
        audit_cols = ("review_post_outcome_only", "review_ref_intake", "fraud_keyword_hit",
                      "legal_language_hit", "stratum")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keep + audit_cols)
            w.writeheader()
            for r in scoped:
                w.writerow({**{k: r[k] for k in keep}, **annotate(r)})
        print(f"wrote {len(scoped)} guarded cases -> {a.out}")


if __name__ == "__main__":
    main()
