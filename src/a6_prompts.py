"""Prompt versions for the Assignment 6 three-arm comparison (base / prompted / adapted).

Every arm sends the complaint narrative and nothing else (data_guard boundary). Arms differ
only in the system prompt and, for the adapted arm, in the model id that serves the adapter.

  baseline-v1  unchanged A4 prompt (src/prompt.py): instruction + schema, no definitions.
  prompted-v1  baseline-v1 + the team's written routing and review rules (TW5 section 6),
               zero-shot. No few-shot examples: outside the 81-case evaluation slice the
               Development data contain no "fraud words but Billing owner" case, so any
               example drawn from the training pool could only teach the keyword shortcut.
  adapter-v1   decision-only schema for a LoRA adapter. The course data label only the
               three decision fields; the case-record fields (summary, facts, unknowns) have
               no reference, so an adapter cannot be trained to produce them from labels.
"""
import hashlib

from src import prompt as _base

ROUTING_OPTIONS = _base.ROUTING_OPTIONS

_COMMON_RULES = """You assist a bank employee who receives credit-card billing-dispute complaints.
You only recommend; the employee decides.

Rules:
- Use only facts stated in the complaint. Do not infer facts that are not written.
- If information needed to handle the case is missing, list it under "unknowns".
- Treat suspected fraud or duplication as a concern needing review, never as an established finding."""

_ROUTING_RULE = """
How to choose recommended_routing. Decide by what happened to the account, not by the words used:
1. "Card Fraud & Security" if the complaint says that a charge, card or account was made, used or
   opened WITHOUT the customer's authorisation: a lost or stolen card used by someone else, an
   account opened in the customer's name, a charge the customer says they never made or allowed,
   or card or account details taken over by someone else.
2. "Card Billing Disputes" if the customer made or allowed the transaction and disputes it: goods
   or services not received, refund not credited, wrong amount, duplicate posting, a fee, interest,
   a payment or a statement problem. This holds even when the customer calls the merchant or the
   bank a "scam" or a "fraud", or says money was "stolen".
3. If the complaint does not say whether the transaction was authorised, choose
   "Card Billing Disputes", set human_review_required to true and add the missing fact to "unknowns".

How to set human_review_required:
- true if the complaint itself shows a possible fraud or security problem, a legal or regulatory
  claim (a statute, bankruptcy, an attorney, a lawsuit, "illegal"), a dispute about a fee, or too
  little information to choose the routing.
- false otherwise. A customer who is upset, or who might dispute the outcome later, is not by
  itself a reason for review."""

_FULL_SCHEMA = """
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

_DECISION_SCHEMA = """
Return ONLY one JSON object, with no other text, using exactly these keys:
{
  "fraud_or_security_concern": true or false,
  "recommended_routing": "Card Billing Disputes" or "Card Fraud & Security",
  "human_review_required": true or false
}"""

PROMPTS = {
    "baseline-v1": _base.SYSTEM_PROMPT,
    "prompted-v1": _COMMON_RULES + "\n" + _ROUTING_RULE + "\n" + _FULL_SCHEMA,
    "adapter-v1": _COMMON_RULES + "\n" + _DECISION_SCHEMA,
}

FULL_FIELDS = ("case_summary", "stated_facts", "unknowns", "fraud_or_security_concern",
               "recommended_routing", "human_review_required", "rationale")
DECISION_FIELDS = ("fraud_or_security_concern", "recommended_routing", "human_review_required")
SCHEMA_OF = {"baseline-v1": FULL_FIELDS, "prompted-v1": FULL_FIELDS, "adapter-v1": DECISION_FIELDS}


def build_messages(prompt_version: str, narrative: str) -> list:
    """Same user-message wrapper as A4 for every arm, so only the system prompt varies."""
    return [
        {"role": "system", "content": PROMPTS[prompt_version]},
        {"role": "user", "content": f"Complaint:\n\"\"\"\n{narrative.strip()}\n\"\"\""},
    ]


def prompt_sha256(prompt_version: str) -> str:
    return hashlib.sha256(PROMPTS[prompt_version].encode("utf-8")).hexdigest()
