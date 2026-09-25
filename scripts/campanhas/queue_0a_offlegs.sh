#!/usr/bin/env bash
# Completes probe 0a: the two plan=OFF legs that NEVER RAN.
#
# WHY THEY ARE MISSING (18/08): the _noplan suffix was applied before the engine block, which reset
# SUFFIX to "" for postgres and erased it. The plan=OFF legs therefore pointed at the plan=ON store,
# --resume found 5/5 already there, skipped every query, and the chain reported "DONE (exit 0)"
# having run nothing. No data was corrupted — nothing was written at all. Fixed in final_battery.sh;
# these two legs just need to happen.
#
# WHAT THEY ANSWER
#   plan ON, measured today in the FINAL composition: q69 5/5 lands (10.3-12.7%), q28 4/5 (44.8-48.6%).
#   Whether the PLAN is what unlocks them is unknown until these OFF legs land next to those numbers.
#
# WHY THIS IS A FILE AND NOT `bash -c '...'`
#   A `bash -c` wrapper carries the wait pattern in its own command line, so pgrep matches the waiter
#   itself and the loop never ends (or, worse, a pkill kills the wrong process — that happened three
#   times on 17/08). A script file's command line is just its path.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

LOG=logs/campanhas/queue_0a_offlegs.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

log "waiting for probe 0b (flash then pro) to finish before touching the machine"
# One server, one .env: never overlap. Wait for BOTH the chain and any run_matrix in flight.
while pgrep -f "campanhas/sondas_p2.sh" >/dev/null || pgrep -f "venv/bin/python scripts/run_matrix" >/dev/null; do
  sleep 120
done
log "machine is free — running the two plan=OFF legs off-peak"

F=scripts/campanhas/final_battery.sh
bash scripts/campanhas/offpeak.sh bash "$F" deepseek-v4-flash workload postgres none on "--only q69" off
log "  q69 plan=OFF done (exit $?)"
bash scripts/campanhas/offpeak.sh bash "$F" deepseek-v4-flash fresh    postgres none on "--only q28" off
log "  q28 plan=OFF done (exit $?)"
# 0d — the monolithic baseline (LITHE's shape). Queued here because it is the cheapest thing that
# can invalidate the P2 premise: if the model working ALONE ties with the architecture, spending the
# remaining ~US$ 38 would be spending it on a claim that does not hold.
# 0d — PROMOTED to a paper table on 18/08 (cell 0c was cancelled and funded this). Two stages on
# purpose: the 6-query screen answers "does the monolithic baseline tie?" for US$ 0.94 — and a tie
# there would mean the remaining ~US$ 38 defends a claim that does not hold. If it does not tie, the
# full 21x5 runs into the SAME store and --resume keeps every screening run.
log "0a done — 0d stage 1: monolithic screening (6 queries x3)"
bash scripts/campanhas/offpeak.sh bash scripts/campanhas/control_monolithic.sh 3
log "  0d screening exit=$?"
log "0d stage 2: FULL monolithic baseline (21 queries x5) — the paper table"
bash scripts/campanhas/offpeak.sh bash scripts/campanhas/control_monolithic.sh 5 \
     q1 q3 q7 q9 q11 q18 q25 q27 q28 q30 q38 q40 q51 q63 q69 q73 q81 q84 q85 q88 q96
log "  0d done (exit $?)"

log "###### 0a + 0d COMPLETE — compare against: q69 ON 5/5 (10.3-12.7%) · q28 ON 4/5 (44.8-48.6%) ######"
