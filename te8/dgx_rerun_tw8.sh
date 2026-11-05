#!/usr/bin/env bash
# TE8 consolidation: the already-defined DGX re-run of team/tw8-controlled at a7193fd.
#
# Why: the a7193fd fixes (reviewer-vigilance v2: whole complaint on screen, fsynced control log
# with exit 6, T1 over every canary, recertification on distinct canaries) pass locally but were
# not re-run on the DGX in class. This script changes no team code; it only runs it and records
# the result, plus the TE8 interim two-person check on the scenario's audit log.
#
# Usage, on the DGX, from the root of a copy of team/tw8-controlled at a7193fd:
#   bash /path/to/te8/dgx_rerun_tw8.sh /path/to/te8
# Optional live canary run (NYU network + Open WebUI token; the 4 Nov run did not complete):
#   LIVE_ENV=/path/to/.env bash /path/to/te8/dgx_rerun_tw8.sh /path/to/te8
#
# Expected (identical to the local re-run in te8/evidence/local_rerun_a7193fd_20261105T221422Z.txt):
#   decision-path suites 55 OK; tests.test_a8_reviewer_control 29 OK; Yufei a8 14 OK
#   scenario exits 0 0 0 5 4 | self-resume 2 | resume-before-practice 2 | 0 0 | resume 0 | 0
#   2121887: junk reason exit 2; listed code exit 0  (the known R1 gap)
#   interim check: 1 de-escalation, 1 BREACH, exit 1 (the trigger fires as intended)
set -u
PKG=${1:?usage: dgx_rerun_tw8.sh /path/to/te8}
PY=${PY:-/opt/sps-ai-lab/venv-train/bin/python}; [ -x "$PY" ] || PY=python3
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=runs/te8_dgx_rerun_$STAMP.txt
mkdir -p runs
{
echo "TE8 DGX re-run of team/tw8-controlled (consolidation, already-defined run)"
echo "host=$(hostname) user=$(whoami) utc=$(date -u +%FT%TZ) python=$($PY -V 2>&1)"
echo "commit=$(git rev-parse --short HEAD 2>/dev/null || cat A8_COMMIT 2>/dev/null || echo unknown)"
echo "== sha256 of control files (a7193fd locally: reviewer_vigilance b2796cb0..., intake_app 28528165...)"
sha256sum src/intake_app.py src/reviewer_vigilance.py candidates/yufei/a8/durable_audit.py \
          src/a8_reviewer_control.py data/a8_canary_cases.jsonl config/a8_canary_pool.json 2>&1
echo "== tests"
echo "-- decision path (expect 55 OK)"
$PY -m unittest tests.test_authority_gate tests.test_parser tests.test_intake_app tests.test_reviewer_vigilance 2>&1 | tail -3
echo "-- Mingyu C1-C8 in the team suite (expect 29 OK; in branch, not called by the app)"
$PY -m unittest tests.test_a8_reviewer_control 2>&1 | tail -3
echo "-- Yufei durable audit (expect 14 OK)"
$PY -m unittest discover -s candidates/yufei/a8 2>&1 | tail -3
echo "-- TE8 interim two-person check (expect 10 OK)"
$PY -m unittest discover -s "$PKG" 2>&1 | tail -3
echo "== team control scenario: tools/a8/run_a8_dgx.sh"
PY=$PY bash tools/a8/run_a8_dgx.sh 2>&1 | sed -n '/== scenario/,$p'
LAST=$(ls -d runs/a8_dgx/*/ 2>/dev/null | sort | tail -1)
echo "== TE8 interim two-person check on ${LAST}audit.jsonl (expect 1 BREACH, exit 1)"
$PY "$PKG/interim_two_person_check.py" "${LAST}audit.jsonl"; echo "interim-check exit=$?"
if [ -n "${LIVE_ENV:-}" ]; then
  echo "== live canary run: 3 synthetic canaries through prompted-v1 (narrative only)"
  $PY tools/a8/run_canaries_live.py --env "$LIVE_ENV" --timeout 180 2>&1 | tail -20
else
  echo "== live canary run: skipped (set LIVE_ENV to run it)"
fi
} 2>&1 | tee "$OUT"
echo "transcript -> $OUT"
