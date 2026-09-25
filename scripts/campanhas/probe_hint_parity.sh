#!/usr/bin/env bash
# SONDA DE PARIDADE DE PISTA — o efeito medido do PLANO é do plano, ou do TEXTO da instrução?
#
#   uso:  bash scripts/campanhas/probe_hint_parity.sh [runs]   (default 5)
#
# O CONFUNDIMENTO (achado 21/08)
#   Os dois ramos do prompt diferem em DUAS coisas, não uma:
#     (a) a EVIDÊNCIA        — plano+hardware presentes ou ausentes   ← o que a ablação quer medir
#     (b) o TEXTO DA PISTA   — ON: "full scans, nested loops, mis-estimation" (direção de ACESSO)
#                              OFF: "subqueries evaluated repeatedly, large joins, repeated scans"
#                                   (direção de MATERIALIZAÇÃO)
#   E o vocabulário decidido pelo FIND seguiu a pista de cada ramo: na q69 o ON nomeou `index scan`
#   (24×) e `filter early` (20×); o OFF nomeou `materialize subquery` (15×). O ganho foi 11,2% × 51,2%.
#
# ESTA SONDA dá ao ramo COM plano a MESMA pista do ramo sem plano (PROMPT_HINT_PARITY=on),
# isolando (a). Três desfechos:
#   ~51%  → era a INSTRUÇÃO, não o plano  → a claim do plano CAI
#   ~11%  → era o PLANO                    → a claim se sustenta
#   meio  → os dois contribuem             → o efeito do plano é MENOR do que reportamos
#
# POR QUE VALE: essa claim hoje sustenta o cancelamento de US$ 19,38 (MySQL/TPC-DS + B+B2) e a
# leitura de dois papers. A sonda custa ~US$ 0,23.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
RUNS="${1:-5}"
STORE=".cache/suggestions_probe_hintparity_q69"
LOG="logs/campanhas/probe_hint_parity.log"
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
mkdir -p logs/campanhas; : > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

if ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -qv "^T"; then
  log "ABORT: outra célula rodando."; exit 1
fi

log "###### SONDA DE PARIDADE DE PISTA — q69 · plano ON + pista do ramo OFF · n=${RUNS} ######"
docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
for i in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/5435) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/5437) 2>/dev/null && { exec 3>&- 4>&-; log "  bancos UP"; break; }; sleep 3
done
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)

# composição IDÊNTICA à da perna "plano ON" da 0a (que deu 11,2%), mudando UMA coisa: a pista.
$SE MODEL_FAMILY=qwen-reasoning REASONING=off TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud FIND_BACKEND=local CLOUD_CODER_MODEL=deepseek-v4-flash WRITER_REASONING=on \
   APPLY_HEURISTICS=off PLAN_HINTS=on HW_HINTS=off \
   MASKING=on MASK_SCOPE=id_str SCHEMA_LINKING=on \
   PROMPT_HINT_PARITY=on \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   INDEX_VALIDATION=executed \
   DB_TYPE=postgres "DB_URI=postgresql://postgres:${PW}@localhost:5435/tpcds" \
   "VERIFY_DB_URI=postgresql://postgres:${PW}@localhost:5437/tpcds" \
   "SUGGESTION_STORE_DIR=${STORE}" "INDEX_SUGGESTION_STORE_DIR=.cache/index_probe_hintparity_q69" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FALHOU"; exit 1; }
rm -f .cache/schema_cache.json

for p in $(ps -eo pid,args --no-headers | grep "[u]vicorn src.main:app" | awk '{print $1}'); do kill "$p" 2>/dev/null; done
sleep 3
nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; break; }; sleep 1; done

$PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs curated heldout non-curated --only q69 >>"$LOG" 2>&1
log "###### SONDA COMPLETA — comparar com: plano ON 11,2% · plano OFF 51,2% ######"
