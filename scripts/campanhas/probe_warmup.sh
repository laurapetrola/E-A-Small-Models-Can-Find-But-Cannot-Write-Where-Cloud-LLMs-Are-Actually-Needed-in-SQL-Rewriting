#!/usr/bin/env bash
# SONDA DO ORÇAMENTO DE AQUECIMENTO — q38, n=5.
#
# PERGUNTA: os timeouts de baseline da q38 no C1 são a query sendo pesada, ou o AQUECIMENTO
# estourando o orçamento antes de chegar às runs quentes?
#
# EVIDÊNCIA QUE MOTIVA: 138 registros da q38 no corpo, a original completou em 122 deles. No C1 de
# hoje ela alterna timeout/sucesso, e quando completa varia de 1,1 s a 19,1 s — 17× na MESMA query,
# no MESMO banco, no MESMO dia, e sem órfãs no ar. Isso é cache, não capacidade da query.
#
# O QUE MUDA: só `WARMUP_TIMEOUT_S`. As runs CRONOMETRADAS seguem em 45 s (executor.py:172), então
# nenhum número reportado muda de regime — o aquecimento é descartado de qualquer forma.
#
# LEITURA: se as 5 runs completarem (contra ~50% de timeout no C1), o orçamento do aquecimento é a
# causa e vira decisão de congelamento. Se continuarem estourando, a causa é outra e o C1 segue
# como está.
#
# ⚠️ Store SEPARADO — não contamina o C1, que fica preservado com as suas 11 runs reais.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

WRITER=deepseek-v4-flash
STORE=".cache/suggestions_probe_warmup"
LOG="logs/campanhas/probe_warmup.log"
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
mkdir -p logs/campanhas; : > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

if ps -eo args --no-headers | grep -q "[r]un_matrix.py"; then
  log "ABORT: outra célula está rodando. Um servidor, um .env."; exit 1
fi

restart_server(){
  for p in $(ps -eo pid,args --no-headers | grep "[u]vicorn src.main:app" | awk '{print $1}'); do kill "$p" 2>/dev/null; done
  sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server não subiu"; return 1
}

log "###### SONDA AQUECIMENTO — q38 · n=5 · WARMUP_TIMEOUT_S=180 (cronometradas seguem 45 s) ######"
docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
for i in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/5435) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/5437) 2>/dev/null && { exec 3>&- 4>&-; log "  bancos UP"; break; }
  sleep 3
done
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)

# config IDÊNTICA ao C1 (p1_cloud.sh qwen deepseek-v4-flash) — só o aquecimento muda
$SE "MODEL_FAMILY=qwen-reasoning" "REASONING=on" TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud "FIND_BACKEND=local" "CLOUD_FIND_MODEL=${WRITER}" \
   "CLOUD_CODER_MODEL=${WRITER}" WRITER_REASONING=on \
   APPLY_HEURISTICS=off PLAN_HINTS=off HW_HINTS=off MASKING=off SCHEMA_LINKING=off \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   INDEX_VALIDATION=executed \
   WARMUP_TIMEOUT_S=180 \
   DB_TYPE=postgres "DB_URI=postgresql://postgres:${PW}@localhost:5435/tpcds" \
   "VERIFY_DB_URI=postgresql://postgres:${PW}@localhost:5437/tpcds" \
   "SUGGESTION_STORE_DIR=${STORE}" "INDEX_SUGGESTION_STORE_DIR=.cache/index_probe_warmup" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FALHOU"; exit 1; }
rm -f .cache/schema_cache.json
restart_server || exit 1

$PY scripts/run_matrix.py --runs 5 --resume --dirs curated heldout non-curated --only q38 >>"$LOG" 2>&1
log "###### SONDA DONE (exit $?) ######"
touch .cache/.queue_probe_warmup.DONE
