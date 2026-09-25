#!/usr/bin/env bash
# OFF-PEAK SUPERVISOR — run a campaign command only while the provider's off-peak rate applies.
#
#   usage:  bash scripts/campanhas/offpeak.sh <command...>
#   e.g.:   bash scripts/campanhas/offpeak.sh bash scripts/campanhas/final_battery.sh deepseek-v4-pro workload
#
# WHY
#   DeepSeek charges HALF price off-peak, and 96% of our cost is OUTPUT tokens, so the discount
#   applies to essentially the whole bill: the remaining campaign is ~US$59 at peak vs ~US$30
#   off-peak. Peak is only 7h a day and — in UTC-3 — it falls in the middle of the night, so almost
#   nothing is lost by pausing through it.
#
#   PEAK (UTC): 01:00-04:00 and 06:00-10:00.  Everything else is off-peak.
#   In UTC-3 that is 22:00-01:00 and 03:00-07:00 local — i.e. the whole working day is off-peak.
#
# HOW IT PAUSES SAFELY
#   It kills the inner command when peak starts and re-launches it when peak ends. That is only safe
#   because every campaign command is RESUMABLE: run_matrix --resume counts the runs already saved
#   per query, so an interrupted cell restarts from where it stopped and loses at most the run in
#   flight. Never wrap a non-resumable command with this.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
[ $# -ge 1 ] || { echo "usage: $0 <command...>"; exit 2; }

# One log per supervised command: two supervisors writing to the same file made today's failure
# unreadable — the anchor's and the ablation's lines interleaved and looked like one run.
LOG="logs/campanhas/offpeak_$(echo "$*" | tr -cs 'a-zA-Z0-9' '_' | cut -c1-60).log"
mkdir -p logs/campanhas
log(){ echo "[$(date -u +%d/%m\ %H:%M) UTC | $(date +%H:%M) local] $*" | tee -a "$LOG"; }

is_peak(){   # exit 0 when the CURRENT UTC hour is inside a peak window
  local h; h=$(date -u +%-H)
  { [ "$h" -ge 1 ] && [ "$h" -lt 4 ]; } || { [ "$h" -ge 6 ] && [ "$h" -lt 10 ]; }
}

CHILD=""
stop_child(){
  [ -n "$CHILD" ] || return 0
  # Kill the whole process group: the campaign scripts spawn run_matrix as a child, and killing only
  # the wrapper would leave the expensive part running straight into peak pricing.
  kill -- -"$CHILD" 2>/dev/null || kill "$CHILD" 2>/dev/null
  sleep 5
  pkill -f "scripts/run_matrix.py" 2>/dev/null
  CHILD=""
}
trap 'log "supervisor interrupted — stopping child"; stop_child; exit 130' INT TERM

log "###### OFF-PEAK SUPERVISOR ###### cmd: $*"
while true; do
  if is_peak; then
    if [ -n "$CHILD" ]; then log "PEAK started -> pausing (progress is kept; --resume continues)"; stop_child; fi
    sleep 300
    continue
  fi
  if [ -z "$CHILD" ]; then
    log "off-peak -> (re)starting"
    setsid "$@" >>"$LOG.cmd" 2>&1 &
    CHILD=$!
  fi
  # Child finished during off-peak. Distinguish DONE from ABORTED: final_battery.sh refuses to start
  # when another cell is running (one server, one .env) and exits non-zero within seconds. Treating
  # that as success is how the anchor lost its supervisor on 17/08 — the wrapper reported COMPLETED
  # one minute after launch while nothing had run.
  if ! kill -0 "$CHILD" 2>/dev/null; then
    wait "$CHILD" 2>/dev/null; rc=$?
    if [ "$rc" -eq 0 ]; then
      log "###### command COMPLETED during off-peak ######"; exit 0
    fi
    log "###### command EXITED with status $rc — NOT treating as completed ######"
    log "     (most likely the guard: another cell is running. Supervisor stops; nothing was lost.)"
    exit "$rc"
  fi
  sleep 60
done
