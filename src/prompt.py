"""Baseline (unadapted) prompt for Group 1's bounded capability.

Capability (Gate 1/Gate 2 scope): employee-facing intake support for credit-card billing
disputes. From the narrative alone, produce a structured case record, a routing
recommendation between two destinations, and a human-review flag. The employee decides.

This is deliberately a *minimal* prompt: an instruction plus an output schema, with no
few-shot examples and no tuned definitions. It measures what the unadapted model already
does. Structured prompting (examples, definitions) is the next experiment, not this one.
"""

PROMPT_VERSION = "baseline-v1"

ROUTING_OPTIONS = ("Card Billing Disputes", "Card Fraud & Security")

SYSTEM_PROMPT = """You assist a bank employee who receives credit-card billing-dispute complaints.
You only recommend; the employee decides.

Rules:
- Use only facts stated in the complaint. Do not infer facts that are not written.
- If information needed to handle the case is missing, list it under "unknowns".
- Treat suspected fraud or duplication as a concern needing review, never as an established finding.

Return ONLY one JSON object, with no other text, using exactly these keys:
{
  "case_summary": "<= 60 words, facts stated by the customer",
  "stated_facts": ["short fact", "..."],
  "unknowns": ["missing information", "..."],
  "fraud_or_security_concern": true or false,
  "recommended_routing": "Card Billing Disputes" or "Card Fraud & Security",
  "human_review_required": true or false,
  "rationale": "<= 40 words"
}"""


def build_messages(narrative: str) -> list:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Complaint:\n\"\"\"\n{narrative.strip()}\n\"\"\""},
    ]
