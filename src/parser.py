"""Structured-output parser and validator for model responses.

Small open-weight models often wrap JSON in prose or code fences, use Python booleans,
leave trailing commas, or paraphrase enum values. This module turns a raw response into a
validated case record and reports exactly what it had to repair, so a repaired output is
never silently treated the same as a clean one.

parse_status:
  "ok"        valid JSON on first attempt, all fields valid
  "repaired"  usable after a documented repair (fences, prose, booleans, enum normalisation)
  "invalid"   JSON parsed but a required field is missing or out of range
  "failed"    no JSON object could be recovered
"""
import json
import re
from dataclasses import dataclass, field

ROUTING_OPTIONS = ("Card Billing Disputes", "Card Fraud & Security")

REQUIRED_FIELDS = {
    "case_summary": str,
    "stated_facts": list,
    "unknowns": list,
    "fraud_or_security_concern": bool,
    "recommended_routing": str,
    "human_review_required": bool,
    "rationale": str,
}


@dataclass
class ParseResult:
    status: str
    record: dict = field(default_factory=dict)
    repairs: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"parse_status": self.status, "record": self.record,
                "repairs": self.repairs, "errors": self.errors}


def _extract_json_object(text: str, repairs: list):
    """Return the first balanced {...} block in text, noting any wrapper removed."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        repairs.append("removed_code_fence")
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    if start == -1:
        return None
    if start > 0:
        repairs.append("removed_leading_text")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                if stripped[i + 1:].strip():
                    repairs.append("removed_trailing_text")
                return stripped[start:i + 1]
    return None  # unbalanced braces, e.g. truncated output


def _loads_with_repairs(blob: str, repairs: list):
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", blob)
    if fixed != blob:
        repairs.append("removed_trailing_comma")
    fixed2 = re.sub(r"(?<=[:\[,\s])(True|False|None)(?=\s*[,}\]])",
                    lambda m: {"True": "true", "False": "false", "None": "null"}[m.group(1)],
                    fixed)
    if fixed2 != fixed:
        repairs.append("python_literals_to_json")
    try:
        return json.loads(fixed2)
    except json.JSONDecodeError:
        return None


def _to_bool(value, key, repairs, errors):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("yes", "true", "y", "required"):
        repairs.append(f"{key}:string_to_bool")
        return True
    if isinstance(value, str) and value.strip().lower() in ("no", "false", "n", "not required"):
        repairs.append(f"{key}:string_to_bool")
        return False
    errors.append(f"{key}: expected boolean, got {value!r}")
    return None


def _normalise_routing(value, repairs, errors):
    if not isinstance(value, str):
        errors.append(f"recommended_routing: expected string, got {value!r}")
        return None
    if value in ROUTING_OPTIONS:
        return value
    low = value.lower()
    has_fraud = "fraud" in low or "security" in low
    has_billing = "billing" in low or "dispute" in low
    if has_fraud and not has_billing:
        repairs.append("recommended_routing:normalised")
        return "Card Fraud & Security"
    if has_billing and not has_fraud:
        repairs.append("recommended_routing:normalised")
        return "Card Billing Disputes"
    errors.append(f"recommended_routing: not one of {ROUTING_OPTIONS}: {value!r}")
    return None


def parse_model_output(text: str) -> ParseResult:
    repairs, errors = [], []
    if not text or not text.strip():
        return ParseResult("failed", errors=["empty response"])
    blob = _extract_json_object(text, repairs)
    if blob is None:
        return ParseResult("failed", repairs=repairs,
                           errors=["no complete JSON object found (possibly truncated)"])
    data = _loads_with_repairs(blob, repairs)
    if not isinstance(data, dict):
        return ParseResult("failed", repairs=repairs, errors=["JSON could not be decoded"])

    record = {}
    for key, expected in REQUIRED_FIELDS.items():
        if key not in data:
            errors.append(f"{key}: missing")
            continue
        value = data[key]
        if key == "recommended_routing":
            record[key] = _normalise_routing(value, repairs, errors)
        elif expected is bool:
            record[key] = _to_bool(value, key, repairs, errors)
        elif expected is list:
            if isinstance(value, str):
                repairs.append(f"{key}:string_to_list")
                value = [value] if value.strip() else []
            if not isinstance(value, list):
                errors.append(f"{key}: expected list, got {type(value).__name__}")
                value = None
            record[key] = value
        else:
            if not isinstance(value, str):
                errors.append(f"{key}: expected string, got {type(value).__name__}")
            record[key] = value
    extra = sorted(set(data) - set(REQUIRED_FIELDS))
    if extra:
        repairs.append("ignored_extra_keys:" + ",".join(extra))

    if errors:
        status = "invalid"
    elif repairs:
        status = "repaired"
    else:
        status = "ok"
    return ParseResult(status, record, repairs, errors)
