# Team Exercise 8 package: Controlled Prototype and Risk/Control Register (Group 1)

Consolidation for TE8 (workshop 4 Nov 2026; package prepared 5 Nov in the 36-hour window).
The controls themselves live in the **team repo** (`emycuadrosr/group1-abi-poc`, branch
`team/tw8-controlled`). This folder holds **no team code**: it holds the register, the evidence
index, a local re-run record and the already-defined DGX re-run. Nothing here was pushed to the team repo.

| File | What it is |
|---|---|
| `risk_control_register.csv` | The prioritised register (R1–R8): trigger, control, owners (PoC and production), classroom status, evidence |
| `interim_two_person_check.py` | Detectable trigger for team decision 4: lists every override from Card Fraud & Security to Card Billing Disputes and flags any without `second: <name>` in the note (exit 1). Read-only on the audit log; prints no narrative text |
| `test_interim_two_person_check.py` | 10 tests: `python -m unittest discover -s te8 -v` |
| `dgx_rerun_tw8.sh` | The DGX re-run of `a7193fd` (not done in class): team tests, Mingyu's 29 C1–C8 tests, Yufei's 14, the team control scenario, the interim check, optional live canary run |
| `evidence/local_rerun_a7193fd_20261105T221422Z.txt` | Local (not DGX) run of `dgx_rerun_tw8.sh` on a copy of `a7193fd`: every expected exit code reproduced |
| `evidence/local_rerun_a7193fd_audit_fields.jsonl` | That run's audit log reduced to decision fields (no model replies, no narratives) |
| `evidence/local_rerun_a7193fd_control_log.jsonl` | That run's control log (suspend, resume) |

## Evidence index (team repo unless stated)

| Evidence | Where | Status |
|---|---|---|
| Emy's reviewer vigilance + Yufei's durable audit merged | `team/tw8-controlled` 5cb28cd, 8965a63 | In class, 4 Nov |
| Integration defect found and fixed (blocked case refused by the durable-audit allow-list) | 92b0ff7 | In class |
| DGX run, both controls together, spark-1338 | 53345f3 `runs/20261104T203634Z_tw8_dgx_spark1338.txt` | DGX, 4 Nov 20:36 UTC |
| Yufei's durable audit on the DGX, job 303, spark-4694 | 03c6447 `candidates/yufei/a8/evidence/dgx/` | DGX, 4 Nov 12:18 UTC |
| Mingyu's C1–C8 control and tests in the team branch (not called by the app) | 70bac71; live canary tool ce7283b | In class |
| Reviewer-vigilance v2 after the independent audit | a7193fd | In class; DGX re-run pending |
| C1–C8 live DGX run, drill over 1,000 orders | this repo, `mingyu/a8-reviewer-control` 96fe4e4, `runs/20261102T145412Z_*` | DGX, 2 Nov |
| Local re-run of a7193fd + interim check | `te8/evidence/` (this branch) | Local, 5 Nov |

## To finish before TE9 freeze

1. Run `bash te8/dgx_rerun_tw8.sh <te8 path>` on the DGX from a copy of `team/tw8-controlled` at `a7193fd`; commit the transcript (Emy).
2. Re-run the three synthetic canaries live (`LIVE_ENV=...`) and record how many Llama routes to Billing (Mingyu).
3. Wire C1–C8 into `src/intake_app.py` as a separately versioned corrective iteration, reviewed by Yufei, then DGX (Mingyu).
