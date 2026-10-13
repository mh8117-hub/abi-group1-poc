# A6 comparison on the frozen eval slice (81 cases)

Adjudication file p3_adjudicated_labels.csv (sha256 d1ce0d5ba0e6): used for 27 slice cases {'A': 27}

| run | arm | commit | prompt | model |
|---|---|---|---|---|
| 20261013T115712Z | rule | 7e3aa0d | - | - |
| 20261013T115715Z | base | 7e3aa0d | baseline-v1 | llama-3.1-8b-instruct |
| 20261013T122014Z | prompted | 7e3aa0d | prompted-v1 | llama-3.1-8b-instruct |

Cells: correct/n (%; Wilson 95% CI). Team reference = adjudicated route where one exists (disputed excluded), otherwise the course route (B, C only).

| metric | rule | base | prompted |
|---|---|---|---|
| usable outputs (ok/repaired) | 81/81 (100%; 95-100) | 81/81 (100%; 95-100) | 81/81 (100%; 95-100) |
| routed to Fraud & Security | 54 | 5 | 21 |
| routing vs course: A | 0/27 (0%; 0-12) | 25/27 (93%; 77-98) | 20/27 (74%; 55-87) |
| routing vs course: B | 27/27 (100%; 88-100) | 3/27 (11%; 4-28) | 13/27 (48%; 31-66) |
| routing vs course: C | 27/27 (100%; 88-100) | 27/27 (100%; 88-100) | 26/27 (96%; 82-99) |
| routing vs team: A | 5/25 (20%; 9-39) | 21/25 (84%; 65-94) | 23/25 (92%; 75-98) |
| routing vs team: B | 27/27 (100%; 88-100) | 3/27 (11%; 4-28) | 13/27 (48%; 31-66) |
| routing vs team: C | 27/27 (100%; 88-100) | 27/27 (100%; 88-100) | 26/27 (96%; 82-99) |
| routing vs team: all decided | 59/79 (75%; 64-83) | 51/79 (65%; 54-74) | 62/79 (78%; 68-86) |
| routing vs team: population-weighted | 96.7% | 80.2% | 85.8% |
| Fraud & Security recall (team) | 32/32 (100%; 89-100) | 4/32 (12%; 5-28) | 17/32 (53%; 36-69) |
| Fraud & Security precision (team) | 32/52 (62%; 48-74) | 4/4 (100%; 51-100) | 17/19 (89%; 69-97) |
| disputed cases routed to | 1451462:F&S, 1801864:F&S | 1451462:F&S, 1801864:Bill | 1451462:F&S, 1801864:F&S |
| review recall vs intake ref | 58/63 (92%; 83-97) | 63/63 (100%; 94-100) | 60/63 (95%; 87-98) |
| review specificity vs intake ref | 11/18 (61%; 39-80) | 0/18 (0%; 0-18) | 5/18 (28%; 12-51) |
| review recall vs course label | 62/69 (90%; 81-95) | 69/69 (100%; 95-100) | 64/69 (93%; 84-97) |
| review specificity vs course label | 9/12 (75%; 47-91) | 0/12 (0%; 0-24) | 3/12 (25%; 9-53) |
| review agrees with team (A) | 16/27 (59%; 41-75) | 16/27 (59%; 41-75) | 15/27 (56%; 37-72) |
| flagged for review (all) | 65 | 81 | 73 |
| latency median / p95 (s) | - | 16.2 / 23.7 | 14.3 / 21.1 |
| prompt tokens (median) | - | 474 | 786 |

## Frozen decision rule

**prompted vs base:** ADOPT - discordant pairs prompted-only 13, base-only 2, exact McNemar p = 0.0074
- R1_beats_incumbent: pass
- R2_beats_keyword_rule: pass
- R3_review_recall_floor: pass
- R4_usable_output_floor: pass
- R5_latency_ratio: pass

