# ABI Group 1 — Complaint Intake PoC: candidate contributions (A4 baseline, A5 data guard, A6 evaluation harness, A7 authority layer)

## Assignment 7 candidate: deterministic authority layer (`mingyu/a7-authority-layer`)

Candidate contribution by Mingyu He for Team Exercise 7 (end-to-end architecture). **Not merged.**
It is the component between the parser and the employee screen that holds the architecture's
authority decisions in code, so they can be tested instead of asserted:

| Decision | Rule in code |
|---|---|
| Default queue | Asymmetric fraud floor. Keyword rule **or** model can put a case in Card Fraud & Security; the model can **not** take a keyword-flagged case out. On conflict (rule Fraud, model Billing) the default stays Fraud and the packet carries a de-escalation *proposal*. |
| Leaving Fraud & Security | Employee only: `apply_employee_decision` refuses a de-escalation without a reason code from a fixed list and an authorisation attestation. Escalation needs only an attributable reason code. |
| Human review | Deterministic triggers only (fraud floor, routing conflict, legal/regulatory language, fee, ≤ 20-word narrative, model failure). The model's review flag is shown as advisory and never sets or clears review. |
| Degradation | Kill switch (`MODEL_ENABLED=false`), timeout (default 45 s), call error or unparseable reply → rule-only default, review forced, `mode = degraded`. Never blocks a case. |
| Audit | Packet stores the narrative fingerprint, rule-set SHA-256, prompt SHA-256, model id, latency and a content hash — never the narrative. |

| File | Role |
|---|---|
| `src/a7_authority.py` | `decide()` → recommendation packet · `apply_employee_decision()` → audit record · `validate_packet()` (contract + authority invariants) · `run_case()` (live call with timeout/kill switch) · CLI `replay` and `smoke` |
| `schemas/a7_recommendation.schema.json` | JSON Schema (2020-12) for the packet: the contract for the employee screen and the audit log |
| `tests/test_a7_authority.py` | 19 offline tests (synthetic narratives): floor, escalation, advisory-only review, degradation, kill switch, employee authority, invariants, hash determinism, no narrative stored, JSON Schema |
| `runs/a7_replay_20261013T122014Z.{json,txt}` | Replay of the logged A6 DGX replies through the layer |

```bash
git checkout mingyu/a7-authority-layer
python -m unittest discover -s tests -v                      # 65 offline tests (46 A4–A6 + 19 A7)
python -m src.a7_authority replay --run runs/20261013T122014Z_a6_prompted_eval.jsonl \
       --rule-run runs/20261013T115712Z_a6_rule_eval.jsonl   # no network
# add  --adjudicated <team p3_adjudicated_labels.csv> data/a6_audit_labels.csv  for the V2 table
python -m src.a7_authority smoke                             # DGX: NYU network + .env token
```

**Replay of logged DGX replies** (prompted-v1, llama-3.1-8b-instruct, run `20261013T122014Z` on `7e3aa0d`, 81 development
cases; no new model calls): 0 contract or invariant problems; the layer's rule half reproduces the logged rule arm on 81/81;
81/81 packets validate against the JSON Schema. 34 cases are `fraud_floor_held` (de-escalation proposals; about 14% of the
in-scope population after stratum weighting), 1 `model_escalation`, 46 agree. Review is required on 67/81 (about 61% weighted);
intake-reference recall 58/63, specificity 9/18; the model's advisory flag would add review on 11 more cases.
Latency median 14.3 s, p95 21.1 s, max 43.2 s: a 10 s timeout would degrade 78/81 cases, 30 s 1/81, 45 s 0/81.

| Policy, V2 reference (74 decided) | Correct | F&S recall | F&S precision |
|---|---|---|---|
| Keyword rule alone | 40/74 | 14/15 | 14/47 |
| Model alone | 67/74 | 12/15 | 12/16 |
| Layer default queue, no employee action | 41/74 | 15/15 | 15/48 |
| Layer + employee accepts every proposal | 67/74 | 12/15 | 12/16 |
| Layer + employee right on every proposal (upper bound) | 70/74 | 15/15 | 15/19 |

The 34 proposals split 29 Billing / 3 Fraud & Security / 2 disputed on the V2 reference: the employee decides where the
system lands between 67/74 (rubber-stamping, 3 frauds missed) and 70/74 (no fraud missed). V2 is provisional (single-labeller
audit beyond the 27 team-adjudicated P3 cases); development cases only; held-out never read.

**DGX status (live, 22 Oct 2026, commit `f2c4b28`, Windows client on NYU VPN, `llama-3.1-8b-instruct`).**
Run `20261022T154501Z`: 3/3 live calls `ok` (13.4 s, 11.2 s, 20.7 s) on the three replay paths (`1487055` fraud_floor_held,
`1834081` agree-Fraud, `2308225` agree-Billing); the forced 0.001 s timeout degraded to rule-only with review forced; 0 packet problems.
Run `20261022T154151Z`, three minutes earlier while the server was slow: all 3 live calls hit the 45 s timeout and degraded
as designed (rule-only default, `model_unavailable` review, 0 packet problems). Both logs are in `runs/`.

## Assignment 6 candidate: three-arm evaluation harness (`mingyu/a6-eval-harness`)

Candidate contribution by Mingyu He for Team Exercise 6 (base vs prompted vs adapted). **Not merged.**
Question it answers: on the same frozen development cases, does structured prompting or a LoRA adapter
change routing and review decisions enough to justify its cost, against the incumbent arm **and** the
7-keyword rule?

| File | Role |
|---|---|
| `src/a6_eval.py` | `manifest` (frozen eval / tuning / training-pool split, disjoint by id and narrative hash) · `rule` (keyword comparator) · `run` (base / prompted / adapted arms through data_guard on DGX; resume; keeps Windows awake) · `score` (stratified accuracy with Wilson CIs, team vs course reference, population-weighted accuracy, exact McNemar, frozen decision rule) · `audit` (blind sheet of discordant cases) · `export-sft` (training pool as chat JSONL for an adapter) |
| `src/a6_prompts.py` | `baseline-v1` (A4, unchanged), `prompted-v1` (team routing + review rules, zero-shot), `adapter-v1` (decision-only schema for a LoRA adapter) |
| `config/a6_frozen.json` | Design, references and decision rule R1–R5, frozen at `7e3aa0d` before any evaluation run; prompt SHA-256 pins |
| `data/a6_manifest.json` | 81 eval / 12 tuning / 558 training-pool case ids with narrative fingerprints and course references (no narratives) |
| `src/a6_repro_check.py` | Checks that the harness's base arm reproduces the A4 outputs of 2 October |
| `tests/test_a6_eval.py` | 20 offline tests (prompt parity with A4, statistics, rule arm, references, decision rule, audit selection, manifest disjointness, SFT target) |

```bash
git checkout mingyu/a6-eval-harness
python -m unittest discover -s tests -v                      # 46 offline tests
DEV=PATH/ABI_Bank_Complaints_Development_8000.csv
python -m src.a6_eval manifest --dev "$DEV"                  # must reproduce data/a6_manifest.json exactly
# Team P3 labels (stratum A): NOT in this repo. See "Reference labels and where they live" below.
python -m src.a6_eval rule --dev "$DEV"
python -m src.a6_eval run  --dev "$DEV" --arm base           # NYU network + .env token
python -m src.a6_eval run  --dev "$DEV" --arm prompted
python -m src.a6_eval run  --dev "$DEV" --arm adapted --model <adapter model id>   # once an adapter is served
python -m src.a6_eval score runs/*_a6_rule_eval.jsonl runs/*_a6_base_eval.jsonl runs/*_a6_prompted_eval.jsonl
python -m src.a6_eval export-sft --dev "$DEV"                 # exports/ is git-ignored
```

### Results on DGX Spark (development cases only; `runs/a6_comparison_V1.md`)

Runs on frozen commit `7e3aa0d`, `llama-3.1-8b-instruct`, temperature 0, seed 42: rule `20261013T115712Z`,
base `20261013T115715Z`, prompted `20261013T122014Z` (81 cases each, 81/81 parsed `ok`). Tuning pilot
`20261013T115000Z` / `115255Z`; 12-case rerun `124047Z` about 50 minutes later (12/12 byte-identical: short-interval repeatability for that sample only); adapter-arm plumbing with the
base model standing in `124600Z` (12/12 parsed, decision-only schema, median 2.5 s).

| Routing correct (V1 reference) | rule | base | prompted |
|---|---|---|---|
| A rule-error stratum, team-adjudicated (25) | 5 | 21 | 23 |
| B course label Fraud & Security (27) | 27 | 3 | 13 |
| C course label Billing (27) | 27 | 27 | 26 |
| All decided (79) | 59 | 51 | 62 |
| Population-weighted | 96.7% | 80.2% | 85.8% |
| Review recall / specificity vs intake reference | 58/63 · 11/18 | 63/63 · 0/18 | 60/63 · 5/18 |
| Median latency / output tokens | – | 16.2 s / 234 | 14.3 s / 206 |

V1 verdict (frozen rule): **adopt prompted over base** (13 vs 2 discordant pairs, exact McNemar p = 0.0074; R1–R5 pass).
The keyword rule's lead on the population-weighted figure comes from strata B and C, whose course labels the same
keyword rule generated; the pre-registered V2 verdict re-scores after a blind audit of the 25 discordant cases.

**V2 — provisional reference (`runs/a6_comparison_V2.md`; blind single-labeller audit `data/a6_audit_labels.csv`, Mingyu He, 13 Oct).**
Only the 25 cases on which a model arm disagreed with the course label were audited. That is enough for the paired
comparisons between arms (a case where every arm agrees with the label scores the same for every arm), but it does not
confirm the 29 B/C cases that were not audited: absolute and population-weighted figures below still assume those course
labels are right, and V2 is not ground truth.
Of the 24 audited stratum-B cases that the course labels Fraud & Security, the audit judged 13 Billing, 6 Fraud &
Security and 5 undecidable; the one audited C case ("took the card and charged it without permission", no keyword)
was judged Fraud & Security.

| Routing correct (V2 reference, 74 decided) | rule | base | prompted |
|---|---|---|---|
| All decided | 40 (54%) | 63 (85%) | 67 (91%) |
| Population-weighted | 81.2% | 90.7% | 94.8% |
| Fraud & Security recall · precision | 14/15 · 14/47 | 4/15 · 4/4 | 12/15 · 12/16 |

V2 verdict (frozen rule): **do not adopt prompted over base** — 8 vs 4 discordant pairs, exact McNemar p = 0.39 (R1
fails; R2–R5 pass). Exploratory, not pre-registered: all 8 prompted-only wins are true Fraud & Security cases (8 vs 0,
p = 0.008) and all 4 base-only wins are billing cases prompted sent to Fraud & Security (0 vs 4). The symmetric rule
cannot see that trade-off; Team Exercise 6 should pre-register a consequence-weighted criterion before its next run.
V2 rests on one labeller (5 of 25 undecidable) and stays provisional until a second team member labels the same 25 cases
and a random sample of the unaudited cases is checked.

Other findings: the training pool (558) contains **no** stratum-A case, so an adapter trained on course labels can
see no "fraud words but Billing" example, so its training target is effectively the keyword rule, the weakest arm on
the provisional V2 reference (40/74; Fraud & Security precision 14/47). No adapter was trained: this is a risk to the
training target, not a measured adapter result; the base arm reproduces the A4 messages exactly but only 20/81 raw outputs (79/81
decisions) match the 2 October logs, while a 12-case rerun 50 minutes later was byte-identical. Run the arms being
compared in one session.

### Reference labels and where they live

| Reference | Cases | Location | How to obtain |
|---|---|---|---|
| Course labels (`routing_destination`, review fields) | all 81 | course Development CSV (not committed) | Brightspace course file |
| Team P3 adjudication (two labellers, TW5) | 27 stratum-A cases | **private team repo** `github.com/emycuadrosr/group1-abi-poc`, branch `team/tw5-data-pipeline`, commit `e7eb32d`, `data/adjudication/p3_adjudicated_labels.csv`, SHA-256 `d1ce0d5ba0e6ddb0228124e43ef659d1af212cad60285af2f35824610959505e` | needs team-repo access (below) |
| A6 blind audit (one labeller) | 25 discordant B/C cases | this repo, `data/a6_audit_labels.csv` | committed |

```bash
# with read access to the team repo (ask Emy Cuadros Rayme, repo owner)
git fetch https://github.com/emycuadrosr/group1-abi-poc team/tw5-data-pipeline
mkdir -p data/adjudication
git show e7eb32d:data/adjudication/p3_adjudicated_labels.csv > data/adjudication/p3_adjudicated_labels.csv
sha256sum data/adjudication/p3_adjudicated_labels.csv     # must match the SHA-256 above
```
`e7eb32d` does not exist in this public repo. The file is kept out of it because it is a team artifact in a private
repository; without it, `score` prints `Adjudication file ... NOT FOUND`, stratum A is excluded from the team-reference
rows, and only the course-label rows reproduce.

---

## Assignment 4 candidate: baseline client (`mingyu/a4-baseline-client`)

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

---

## Assignment 5 candidate: data guard (`mingyu/a5-data-guard`)

A small reusable component for the shared **Data Pipeline / Test Harness**: every sample builder,
run or evaluation script calls it before touching course data. Standard library only.

| File | Role |
|---|---|
| `src/data_guard.py` | Schema check (29 columns, code lists); held-out refusal (file name, `dataset_split`, `HEL-` id); exclusion of dev rows whose narrative duplicates held-out text; Gate 1 scope filter; **input/target boundary** (`build_model_input` returns the narrative only, `assert_input_boundary` rejects targets, surrogates, post-outcome fields and pasted reference text); **label-risk annotations** (`review_post_outcome_only`, `review_ref_intake`, `fraud_keyword_hit`, `legal_language_hit`, `stratum`); `--report` prints the audit figures. |
| `data/heldout_narrative_sha256.txt` | 1,500 one-way SHA-256 fingerprints of normalised held-out narratives. Lets the guard exclude overlaps without storing or reading held-out text during development. |
| `tests/test_data_guard.py` | 15 offline tests (split protection, schema, boundary, label-risk logic). |
| `src/dgx_guard_smoke.py` | Runs 8 guarded dev cases through the existing client/prompt/parser on DGX Spark and logs the payload keys actually sent. |
| `runs/a5_data_guard_report.txt` | Audit figures reproduced from the Development CSV by the guard. |

```bash
python -m unittest discover -s tests -v                       # 26 offline tests
python -m src.data_guard --dev PATH/ABI_Bank_Complaints_Development_8000.csv --report
python -m src.data_guard --dev PATH/... --out data/dev_billing_guarded.csv   # git-ignored
python -m src.dgx_guard_smoke --dev PATH/...                  # NYU network + .env token
```

**Why the boundary matters here.** In the course data `human_review_required` equals
`urgency High/Critical OR consumer_disputed = Yes OR escalation_required = Yes` on 8,000/8,000 rows.
`consumer_disputed` is recorded after the company responds, so 82 of the 368 in-scope review = Yes
labels cannot be known at intake. Report review recall against both the course label and
`review_ref_intake`, and treat the 82 `review_post_outcome_only` cases as a separate stratum.
