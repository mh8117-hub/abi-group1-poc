# A6 comparison on the frozen tuning slice (12 cases)

Adjudication file p3_adjudicated_labels.csv (sha256 d1ce0d5ba0e6): used for 0 slice cases {}

| run | arm | commit | prompt | model |
|---|---|---|---|---|
| 20261013T115255Z | base | 9d7173a | baseline-v1 | llama-3.1-8b-instruct |
| 20261013T115000Z | prompted | 9d7173a | prompted-v1 | llama-3.1-8b-instruct |
| 20261013T124600Z | adapted-standin | 7e11d18 | adapter-v1 | llama-3.1-8b-instruct |

Cells: correct/n (%; Wilson 95% CI). Team reference = adjudicated route where one exists (disputed excluded), otherwise the course route (B, C only).

| metric | base | prompted | adapted-standin |
|---|---|---|---|
| usable outputs (ok/repaired) | 12/12 (100%; 76-100) | 12/12 (100%; 76-100) | 12/12 (100%; 76-100) |
| routed to Fraud & Security | 0 | 2 | 0 |
| routing vs course: A | - | - | - |
| routing vs course: B | 0/4 (0%; 0-49) | 1/4 (25%; 5-70) | 0/4 (0%; 0-49) |
| routing vs course: C | 8/8 (100%; 68-100) | 7/8 (88%; 53-98) | 8/8 (100%; 68-100) |
| routing vs team: A | - | - | - |
| routing vs team: B | 0/4 (0%; 0-49) | 1/4 (25%; 5-70) | 0/4 (0%; 0-49) |
| routing vs team: C | 8/8 (100%; 68-100) | 7/8 (88%; 53-98) | 8/8 (100%; 68-100) |
| routing vs team: all decided | 8/12 (67%; 39-86) | 8/12 (67%; 39-86) | 8/12 (67%; 39-86) |
| routing vs team: population-weighted | 77.6% | 73.5% | 77.6% |
| Fraud & Security recall (team) | 0/4 (0%; 0-49) | 1/4 (25%; 5-70) | 0/4 (0%; 0-49) |
| Fraud & Security precision (team) | - | 1/2 (50%; 9-91) | - |
| disputed cases routed to | - | - | - |
| review recall vs intake ref | 8/8 (100%; 68-100) | 7/8 (88%; 53-98) | 7/8 (88%; 53-98) |
| review specificity vs intake ref | 0/4 (0%; 0-49) | 0/4 (0%; 0-49) | 0/4 (0%; 0-49) |
| review recall vs course label | 8/8 (100%; 68-100) | 7/8 (88%; 53-98) | 7/8 (88%; 53-98) |
| review specificity vs course label | 0/4 (0%; 0-49) | 0/4 (0%; 0-49) | 0/4 (0%; 0-49) |
| review agrees with team (A) | - | - | - |
| flagged for review (all) | 12 | 11 | 11 |
| latency median / p95 (s) | 17.0 / 31.9 | 14.1 / 19.7 | 2.5 / 2.8 |
| prompt tokens (median) | 474 | 786 | 422 |

## Frozen decision rule

