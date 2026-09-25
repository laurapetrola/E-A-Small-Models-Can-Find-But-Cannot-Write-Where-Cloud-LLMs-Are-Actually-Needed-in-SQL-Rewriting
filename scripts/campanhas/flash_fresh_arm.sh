#!/usr/bin/env bash
# Writer ablation — FLASH arm, FRESH BATTERY (11 never-seen queries, queries/heldout_op2/TPCDS).
#
# Pairs against .cache/suggestions_system_pro_fresh (the PRO fresh cell, 11q x5).
# WHY THIS CELL EXISTS: quality tied on the 21-query workload (pro 13 reach / 8 consistent vs
# flash 12 / 9), so the workload alone cannot decide the canonical writer. The fresh battery is
# where the writer works WITHOUT rule coverage — the condition most likely to separate them.
#
# Setup mirrors the PRO fresh cell exactly (only CLOUD_CODER_MODEL differs):
#   FIND  = qwen3:8b LOCAL, reasoning OFF, execution plan ON, validated rules ON
#   WRITE = cloud writer, MASKED payload (id_str) + schema-linking ON
#   GATE  = S=1 (full-scale execution + SF1 adjudication), n=5 per query
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
LOG=logs/campanhas/flash_fresh_arm.log
mkdir -p logs/campanhas
: > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

restart_server(){
  pkill -f "uvicorn src.main:app" 2>/dev/null; sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server did not come up"; return 1
}

health(){
  docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
  for i in $(seq 1 30); do
    (exec 3<>/dev/tcp/127.0.0.1/5435) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/5437) 2>/dev/null && { exec 3>&- 4>&-; log "  databases UP"; return 0; }
    sleep 3
  done
  log "  databases did not come up"; return 1
}

preflight(){
  $PY - "$1" <<'PYEOF' 2>&1 | tee -a "$LOG"
import os,sys
from openai import OpenAI
model=sys.argv[1]
key=os.popen("grep '^KEY_DEEPSEEK' .env | cut -d= -f2").read().strip()
c=OpenAI(api_key=key, base_url="https://api.deepseek.com")
try:
    r=c.chat.completions.create(model=model,messages=[{"role":"user","content":"Return ONLY SQL: select 1"}],temperature=0,max_tokens=400)
    ok=bool((r.choices[0].message.content or "").strip())
    print(f"preflight {model}:", "OK" if ok else "EMPTY - check balance"); sys.exit(0 if ok else 1)
except Exception as e:
    print(f"preflight {model} FAILED:", str(e)[:120]); sys.exit(1)
PYEOF
  return ${PIPESTATUS[0]}
}

log "###### WRITER ABLATION — FLASH ARM · FRESH BATTERY (11q x5) ######"
health || exit 1
log "preflight..."; preflight deepseek-v4-flash || { log "aborting: writer unavailable"; exit 1; }

PGPW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
$SE MODEL_FAMILY=qwen-reasoning REASONING=off TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud FIND_BACKEND=local APPLY_HEURISTICS=on PLAN_HINTS=on HW_HINTS=off \
   MASKING=on MASK_SCOPE=id_str SCHEMA_LINKING=on \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   DB_TYPE=postgres "DB_URI=postgresql://postgres:${PGPW}@localhost:5435/tpcds" \
   "VERIFY_DB_URI=postgresql://postgres:${PGPW}@localhost:5437/tpcds" \
   CLOUD_CODER_MODEL=deepseek-v4-flash \
   SUGGESTION_STORE_DIR=.cache/suggestions_system_flash_fresh \
   INDEX_SUGGESTION_STORE_DIR=.cache/index_system_flash_fresh | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE on both instances (planner statistics)..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FAILED - aborting"; exit 1; }
rm -f .cache/schema_cache.json && log "  schema cache cleared (databases restarted)"

log "running 11 fresh queries x5 (resuming)..."
restart_server && $PY scripts/run_matrix.py --runs 5 --resume --dirs heldout_op2 >>"$LOG" 2>&1
log "###### FLASH FRESH ARM DONE (exit $?) ###### — next: latency micro-benchmark, then the writer decision."

# Leave .env on the default writer so an interrupted session never starts a cell on the wrong one.
# ⛔ O default era `deepseek-v4-pro` ATÉ 09/09/2026. Trocado para flash: o pro foi aposentado em
#    14/09/2026 e passou a devolver Flash com o NOME pro — deixar o .env apontado para ele faria
#    QUALQUER célula seguinte nascer mislabelada. O `setenv.py` agora recusa pro (exit 7), então
#    esta linha abortaria o script no fim se não fosse corrigida.
$SE CLOUD_CODER_MODEL=deepseek-v4-flash | tee -a "$LOG"
