# ABI Group 1 — Complaint Intake PoC: baseline candidate contribution (Assignment 4)

Candidate contribution by Mingyu He for Team Exercise 4 (Baseline Benchmark and Prototype Scaffold).
**Not merged into any team main branch** — the team decides what to adopt.

## What this is
A small, reproducible component that answers the Assignment 4 question
*"What can an unadapted locally hosted model already do?"* for Group 1's authorized scope:
credit-card billing disputes → structured case record + routing recommendation
(Card Billing Disputes vs Card Fraud & Security) + human-review flag. The employee decides.

| File | Role |
|---|---|
| `src/client.py` | Stdlib-only client for the DGX Spark Open WebUI OpenAI-compatible endpoint (`/api/chat/completions`). Bypasses desktop proxies for the private campus IP. |
| `src/prompt.py` | Minimal baseline prompt (`baseline-v1`): instruction + JSON schema, no examples. |
| `src/parser.py` | Structured-output parser/validator. Labels every output `ok` / `repaired` / `invalid` / `failed` and records each repair. |
| `src/run_baseline.py` | Runs cases, saves raw + parsed outputs, latency, reference comparison, run metadata (git commit, model, settings). Refuses held-out data (team plan P7). |
| `src/select_sample.py` | Reproducible 12-case development sample (seed 4), stratified by the P3 subgroup. |
| `src/login.py` | Signs in with your own Open WebUI account (password via getpass, never stored) and writes the session token to the git-ignored `.env`. Needed because API keys are not available to student accounts. |
| `tests/test_parser.py` | 11 offline parser tests (no network). |
| `data/dev_sample_cases.csv` | The 12 development cases used (Development set only). |
| `runs/` | Execution evidence from DGX runs. |

## Reproduce
```bash
git clone <repo-url> && cd abi-complaint-poc
git checkout mingyu/a4-baseline-client
python -m src.login             # writes your session token to .env (or copy .env.example and paste an API key)
python -m unittest discover -s tests -v          # offline parser tests
python -m src.run_baseline --list-models          # confirms endpoint + model id
python -m src.run_baseline --cases data/dev_sample_cases.csv
```
Requires Python 3.9+ and network access to `172.22.42.174:8080` (NYU network/VPN). No third-party packages.

## Data discipline
- Development set only. The held-out file is never read by this code; `run_baseline.py` refuses it.
- 5 development case_ids whose narratives also appear verbatim in the held-out file are excluded.
- Labels are instructor-created, rule-generated (see `label_provenance`); agreement with them is not bank operating performance.
