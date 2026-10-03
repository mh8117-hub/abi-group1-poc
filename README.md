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
| `src/client.py` | Stdlib-only client for the DGX Spark Open WebUI endpoint (`POST /api/chat/completions`). Bypasses desktop proxies for the private campus IP. |
| `src/prompt.py` | Minimal baseline prompt (`baseline-v1`): instruction + JSON schema, no examples. |
| `src/parser.py` | Structured-output parser/validator. Labels every output `ok` / `repaired` / `invalid` / `failed` and records each repair. |
| `src/run_baseline.py` | Runs cases, saves raw + parsed outputs, latency, token usage, reference labels and run metadata (git commit, model, settings). Refuses held-out data (team plan P7). |
| `src/select_sample.py` | Reproducible stratified development sample (seed 4): `--per-group 4` → 12 cases, `--per-group 27` → 81 cases. |
| `src/analyze_runs.py` | Computes every reported figure from one or more run logs (per-stratum routing vs the reference rule, review recall/specificity, fraud-flag agreement, latency, reproducibility; `--dev` for the dataset check). |
| `src/export_openwebui.py` | Converts a run log, unedited, into an Open WebUI chat-import file (API calls are not saved as chats). |
| `src/login.py` | Signs in with your own Open WebUI account (password via getpass, never stored) and writes the session token to the git-ignored `.env`. Used because my account has no API Keys section. |
| `tests/test_parser.py` | 11 offline parser tests (no network). |
| `data/` | `dev_sample_cases.csv` (12), `dev_sample_81.csv` (81), `dev_sample_81_remaining.csv` (the 34 re-run after an interruption). Development set only. |
| `runs/` | Execution evidence (below) and `analysis_*.txt` outputs. |

## Runs on DGX Spark (llama-3.1-8b-instruct, temperature 0, seed 42, prompt baseline-v1)
| Run id | Cases | Purpose |
|---|---|---|
| `20261002T151238Z` | 12 | First baseline run |
| `20261002T151649Z` | 3 | Short reproducibility rerun |
| `20261002T211336Z` (`_offnetwork_call_errors`) | 3 attempted | Off the NYU network: every call timed out (WinError 10060) |
| `20261002T212716Z` | 12 | Full rerun of the 12-case sample (12/12 byte-identical to 151238Z) |
| `20261002T213121Z` | 47 of 81 | 81-case run; stopped when the client laptop slept |
| `20261002T232853Z` | 34 | The remaining 34 of the 81-case sample |

## Reproduce
```bash
git clone https://github.com/mh8117-hub/abi-group1-poc.git && cd abi-group1-poc
git checkout mingyu/a4-baseline-client
python -m src.login                              # writes your session token to .env (or copy .env.example and paste an API key)
python -m unittest discover -s tests -v          # offline parser tests
python -m src.run_baseline --list-models         # confirms endpoint + model id
python -m src.run_baseline --cases data/dev_sample_81.csv
python -m src.analyze_runs runs/20261002T213121Z_baseline.jsonl runs/20261002T232853Z_baseline.jsonl
python -m src.analyze_runs runs/20261002T212716Z_baseline.jsonl --compare runs/20261002T151238Z_baseline.jsonl
```
On Windows use `py` instead of `python`. Requires Python 3.9+ and network access to `172.22.42.174:8080` (NYU network/VPN). No third-party packages.

## Data discipline
- Development set only. The held-out file is never read by this code; `run_baseline.py` refuses it.
- 5 development case_ids whose narratives also appear verbatim in the held-out file are excluded.
- Labels are instructor-created, rule-generated (see `label_provenance`); agreement with them is not bank operating performance.
