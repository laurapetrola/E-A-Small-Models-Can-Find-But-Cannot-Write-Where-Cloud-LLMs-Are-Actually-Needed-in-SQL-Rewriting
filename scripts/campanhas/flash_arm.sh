#!/usr/bin/env bash
# Writer ablation — FLASH arm, canonical configuration, TPC-DS/PostgreSQL anchor quadrant.
#
# Pairs against the PRO arm (.cache/suggestions_system_pro_tpcds_pg) to decide the canonical
# cloud writer: same FIND, same rules, same plan, same masking — only CLOUD_CODER_MODEL changes.
#
# Canonical configuration under test:
#   FIND  = qwen3:8b LOCAL, reasoning OFF, execution plan ON, validated rules ON
#   WRITE = cloud writer with MASKED payload (id_str) + schema-linking
#   GATE  = S=1 (full-scale execution + SF1 adjudication), n=5 per query
#   STRATEGY_CACHE=off so the 5 runs stay independent (the cache has its own measurement)
#
# Resumable: --resume counts saved records per query, so a reboot costs at most one run.
# Versioned in the repo (not /tmp) after two reboots wiped the scratchpad — and because the
# EDBT CFP requires an artifact section: these chains document HOW each cell was produced.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
LOG=logs/campanhas/flash_arm.log
mkdir -p logs/campanhas
: > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

restart_server(){
  pkill -f "uvicorn src.main:app" 2>/dev/null; sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server did not come up"; return 1
}

# Environment health: containers + both databases reachable (a reboot took them down twice).
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

log "###### WRITER ABLATION — FLASH ARM (resume) ######"
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
   SUGGESTION_STORE_DIR=.cache/suggestions_system_flash_tpcds_pg \
   INDEX_SUGGESTION_STORE_DIR=.cache/index_system_flash_tpcds_pg | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE on both instances (planner statistics)..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FAILED - aborting"; exit 1; }
rm -f .cache/schema_cache.json && log "  schema cache cleared (databases restarted)"

log "running 21 workload queries x5 (resuming)..."
restart_server && $PY scripts/run_matrix.py --runs 5 --resume --dirs curated heldout non-curated >>"$LOG" 2>&1
log "###### FLASH ARM DONE (exit $?) ###### — next: compare against the PRO arm and decide the canonical writer."

# Leave .env on the default writer so an interrupted session never starts a cell on the wrong one.
# ⛔ O default era `deepseek-v4-pro` ATÉ 09/09/2026. Trocado para flash: o pro foi aposentado em
#    14/09/2026 e passou a devolver Flash com o NOME pro — deixar o .env apontado para ele faria
#    QUALQUER célula seguinte nascer mislabelada. O `setenv.py` agora recusa pro (exit 7), então
#    esta linha abortaria o script no fim se não fosse corrigida.
$SE CLOUD_CODER_MODEL=deepseek-v4-flash | tee -a "$LOG"
