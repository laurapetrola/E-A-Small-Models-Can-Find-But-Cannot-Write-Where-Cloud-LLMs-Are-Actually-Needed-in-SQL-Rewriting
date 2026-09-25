#!/usr/bin/env bash
# IMDb (JOB) — a bateria que faltava. Cobre C+D da fila do P2.
#
#   usage:  bash scripts/campanhas/imdb_battery.sh <writer> <postgres|mysql> <plan on|off> [runs]
#   e.g.:   bash scripts/campanhas/imdb_battery.sh deepseek-v4-flash mysql off 3
#
# POR QUE ELA SUBIU DE PRIORIDADE (19/08)
#   O achado que sustenta o PLANO ("+2 alcance e não-equivalências 4→2") nasceu aqui, em IMDb/MySQL,
#   em 07/08 — mas na composição ANTIGA (v4-pro · +think · regras ON · masking vazando). Na composição
#   nova o plano já se mostrou NEUTRO ou NOCIVO no PostgreSQL (q69: 51% sem plano × 11% com). Este par
#   é a medição que decide se o plano vive: ele é o único lugar onde o plano já rendeu de verdade.
#   Custo ~US$ 4 · protege US$ 19,38 (B+B2 + célula MySQL do TPC-DS).
#
# ⚠️ ESCALA ÚNICA: o IMDb não tem instância reduzida (não é escalonado como o TPC-DS). O gate S=1
#    compara em ESCALA REAL direto, com VERIFY apontando para a mesma instância. Isso é mais forte
#    que o caso TPC-DS, não mais fraco — não há fallback de escala aqui.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

WRITER="${1:-deepseek-v4-flash}"
ENGINE="${2:-mysql}"
PLAN="${3:-on}"
RUNS="${4:-3}"
case "$PLAN" in on|off) ;; *) echo "plan must be on|off"; exit 2 ;; esac

case "$ENGINE" in
  postgres) CONT=imdb;       PORT=5436; URI="postgresql://postgres:postgres@localhost:5436/imdb" ;;
  mysql)    CONT=imdb-mysql; PORT=3307; URI="mysql+pymysql://root:mysql@localhost:3307/imdb" ;;
  *) echo "engine must be postgres|mysql"; exit 2 ;;
esac
SUF="_${ENGINE}"; [ "$PLAN" = off ] && SUF="${SUF}_noplan"
STORE=".cache/suggestions_imdb_final_${WRITER##*-}${SUF}"
LOG="logs/campanhas/imdb_final_${WRITER##*-}${SUF}.log"
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
mkdir -p logs/campanhas; : > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

if pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" >/dev/null; then
  log "ABORT: outra célula está rodando. Um servidor, um .env."; exit 1
fi

restart_server(){
  for p in $(pgrep -f "uvicorn src.main:app"); do kill "$p" 2>/dev/null; done; sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server não subiu"; return 1
}

log "###### IMDb ${ENGINE} · writer=${WRITER} · plano=${PLAN} · n=${RUNS} ######"
docker start "$CONT" >/dev/null 2>&1
for i in $(seq 1 30); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && { exec 3>&-; log "  banco UP"; break; }; sleep 3; done

$SE MODEL_FAMILY=qwen-reasoning REASONING=off TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud FIND_BACKEND=local APPLY_HEURISTICS=off "PLAN_HINTS=${PLAN}" HW_HINTS=off \
   MASKING=on MASK_SCOPE=id_str SCHEMA_LINKING=on \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   INDEX_VALIDATION=executed WRITER_REASONING=on \
   "DB_TYPE=${ENGINE}" "DB_URI=${URI}" "VERIFY_DB_URI=${URI}" \
   "CLOUD_CODER_MODEL=${WRITER}" \
   "SUGGESTION_STORE_DIR=${STORE}" \
   "INDEX_SUGGESTION_STORE_DIR=.cache/index_imdb_final_${WRITER##*-}${SUF}" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || log "  ⚠️ ANALYZE falhou — seguindo (IMDb pode não precisar)"
rm -f .cache/schema_cache.json

restart_server || exit 1
# ⚠️ A validação de índice por EXECUÇÃO é do PostgreSQL — `mysql.py` NÃO a implementa (0 ocorrências
# de `_index_validation_executed`, contra 7 no `postgres.py`). No MySQL o índice é criado de verdade
# (CREATE/DROP em autocommit) mas o benefício vem de `EXPLAIN FORMAT=JSON` = CUSTO, não tempo.
# Exigir `--index executed` aqui seria um portão impossível de satisfazer. ⚠️ CONSEQUÊNCIA A DECLARAR:
# os números de índice das células MySQL são ESTIMADOS, e não podem ser comparados com os medidos do PG.
IDXCHECK="--index executed"
[ "$ENGINE" = mysql ] && IDXCHECK=""

DIRS="curated heldout"        # as 13 queries JOB (10 curated + 3 heldout); o runner escolhe pelo DB_URI

# CANARY: uma run, depois confere o REGISTRO contra a intenção.
# ⚠️ `--runs 1` SEM `--only` significa "uma run de CADA query" — 13 runs, ~4 h. O canário existe para
# gastar UMA e abortar cedo; escrito assim ele só validaria depois de meia campanha. Corrigido 19/08.
if [ "$(ls "${STORE}"/*.json 2>/dev/null | wc -l)" -eq 0 ]; then
  log "canary: UMA run de UMA query (1a) para validar a configuração"
  $PY scripts/run_matrix.py --runs 1 --resume --dirs $DIRS --only 1a >>"$LOG" 2>&1
fi
# ⚠️ NÃO usar `if ! canary | tee` — o `if` avalia o exit do TEE (sempre 0) e a falha do canário
# some. Descoberto em 20/08: o canário imprimiu "CANARY FAILED" e a cadeia seguiu em frente. A
# salvaguarda existiu por dois dias em CINCO cadeias sem nunca ter bloqueado nada.
_cout=$($PY scripts/canary_check.py "$(basename "$STORE" | sed 's/^suggestions_//')" \
      --writer "cloud:${WRITER}" --rules off --plan "$PLAN" $IDXCHECK 2>&1); _crc=$?
printf '%s\n' "$_cout" | tee -a "$LOG"
if [ "$_crc" -ne 0 ]; then
  log "ABORTANDO: o canário recusou a configuração."; exit 1
fi
log "canário passou"

$PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs $DIRS >>"$LOG" 2>&1
log "###### IMDb ${ENGINE} plano=${PLAN} DONE (exit $?) ######"
