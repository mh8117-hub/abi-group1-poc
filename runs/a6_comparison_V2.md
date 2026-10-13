# A6 comparison on the frozen eval slice (81 cases)

Adjudication file p3_adjudicated_labels.csv (sha256 d1ce0d5ba0e6): used for 27 slice cases {'A': 27}
Adjudication file a6_audit_labels.csv (sha256 5f32fc834839): used for 25 slice cases {'B': 24, 'C': 1}

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
| routing vs team: B | 9/22 (41%; 23-61) | 16/22 (73%; 52-87) | 17/22 (77%; 57-90) |
| routing vs team: C | 26/27 (96%; 82-99) | 26/27 (96%; 82-99) | 27/27 (100%; 88-100) |
| routing vs team: all decided | 40/74 (54%; 43-65) | 63/74 (85%; 75-91) | 67/74 (91%; 82-95) |
| routing vs team: population-weighted | 81.2% | 90.7% | 94.8% |
| Fraud & Security recall (team) | 14/15 (93%; 70-99) | 4/15 (27%; 11-52) | 12/15 (80%; 55-93) |
| Fraud & Security precision (team) | 14/47 (30%; 19-44) | 4/4 (100%; 51-100) | 12/16 (75%; 51-90) |
| disputed cases routed to | 1451462:F&S, 1801864:F&S, 1658297:F&S, 1640901:F&S, 2305999:F&S, 2203650:F&S, 1611079:F&S | 1451462:F&S, 1801864:Bill, 1658297:Bill, 1640901:Bill, 2305999:Bill, 2203650:Bill, 1611079:Bill | 1451462:F&S, 1801864:F&S, 1658297:F&S, 1640901:Bill, 2305999:Bill, 2203650:F&S, 1611079:F&S |
| review recall vs intake ref | 58/63 (92%; 83-97) | 63/63 (100%; 94-100) | 60/63 (95%; 87-98) |
| review specificity vs intake ref | 11/18 (61%; 39-80) | 0/18 (0%; 0-18) | 5/18 (28%; 12-51) |
| review recall vs course label | 62/69 (90%; 81-95) | 69/69 (100%; 95-100) | 64/69 (93%; 84-97) |
| review specificity vs course label | 9/12 (75%; 47-91) | 0/12 (0%; 0-24) | 3/12 (25%; 9-53) |
| review agrees with team (A) | 16/27 (59%; 41-75) | 16/27 (59%; 41-75) | 15/27 (56%; 37-72) |
| flagged for review (all) | 65 | 81 | 73 |
| latency median / p95 (s) | - | 16.2 / 23.7 | 14.3 / 21.1 |
| prompt tokens (median) | - | 474 | 786 |

## Frozen decision rule

**prompted vs base:** DO NOT ADOPT - discordant pairs prompted-only 8, base-only 4, exact McNemar p = 0.3877
- R1_beats_incumbent: FAIL
- R2_beats_keyword_rule: pass
- R3_review_recall_floor: pass
- R4_usable_output_floor: pass
- R5_latency_ratio: pass

