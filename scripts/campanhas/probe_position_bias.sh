#!/usr/bin/env bash
# SONDA DE VIÉS POSICIONAL — o schema do prompt de índice muda a recomendação?
#
#   usage:  bash scripts/campanhas/probe_position_bias.sh
#
# A PERGUNTA
#   O LLM4IA (CIKM '25) demonstra que o GPT-4o "places higher attention on the beginning and ending
#   parts of the workload, ignoring relevant information in the middle" — e escolhe o índice ERRADO
#   por causa disso. O nosso prompt de índice entrega um bloco de schema e pede que o modelo escolha
#   colunas dele: é exatamente essa configuração.
#
#   Se o nosso advisor for posicionalmente enviesado, parte do que reportamos como limite de FIND é
#   artefato de ORDENAÇÃO — e o eixo de índice é o CONTROLE NEGATIVO da tese FIND/WRITE, então a
#   fragilidade contamina o argumento central.
#
# O DESENHO — muda UMA coisa
#   Mesmas queries, mesmo modelo, mesma configuração. Só `SCHEMA_ORDER=reverse`, que inverte a ordem
#   das tabelas no bloco de schema e não toca em mais nada.
#
# LEITURA
#   mesmas colunas recomendadas  -> CAPACIDADE (o viés não opera aqui) -> declarar como DESCARTADO
#   colunas diferentes           -> VIÉS POSICIONAL -> declarar com magnitude MEDIDA
#
# POR QUE 5 QUERIES E NÃO 21
#   A sonda é binária. Se o viés aparecer em 5, ele existe e a §5.4 passa a ter número. Se não
#   aparecer em 5, 21 não mudariam a conclusão. Ampliar só se aparecer.
#
# ⚠️ ONDE O VIÉS **NÃO** MORDE (já argumentado, não re-litigar)
#   · Na reescrita do PostgreSQL, `c=0` em todos os modelos — a classe "não alcançou a técnica" é
#     VAZIA. Não há falha de FIND para o viés explicar.
#   · Ele NÃO pode explicar a inversão PG × MySQL: o schema do TPC-DS é o MESMO nos dois engines, e um
#     artefato de posição agiria igual em ambos.
#
# CUSTO: local, `qwen`, GRÁTIS. 5 queries × 3 runs × 2 ordens = 30 runs (~4 h de janela de pico).
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

QUERIES="q1 q9 q18 q27 q67"     # mistura: 2 que landam sempre, 3 do grupo que estoura orçamento
RUNS=3
PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
LOG="logs/campanhas/probe_position_bias.log"
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

docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
for i in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/5435) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/5437) 2>/dev/null && { exec 3>&- 4>&-; log "  bancos UP"; break; }
  sleep 3
done
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)

run_arm(){       # $1 = normal|reverse
  local order="$1" store=".cache/suggestions_posbias_$1"
  log "###### braço ${order} ######"
  $SE MODEL_FAMILY=qwen-reasoning REASONING=on TWO_AGENT_MODE=off RUN_ARM=raw \
     FIND_BACKEND=local APPLY_HEURISTICS=off PLAN_HINTS=off HW_HINTS=off MASKING=off \
     SCHEMA_LINKING=off DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off LEARNED_RULES_FILE= \
     INDEX_VALIDATION=executed \
     "SCHEMA_ORDER=$( [ "$order" = reverse ] && echo reverse || echo '' )" \
     DB_TYPE=postgres "DB_URI=postgresql://postgres:${PW}@localhost:5435/tpcds" \
     "VERIFY_DB_URI=postgresql://postgres:${PW}@localhost:5437/tpcds" \
     "SUGGESTION_STORE_DIR=${store}" "INDEX_SUGGESTION_STORE_DIR=.cache/index_posbias_${order}" | tee -a "$LOG"
  bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
  restart_server || return 1
  $PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs curated heldout non-curated \
      --only $QUERIES >>"$LOG" 2>&1
  log "  braço ${order} exit=$?"
}

log "###### SONDA DE VIÉS POSICIONAL — ${QUERIES} · n=${RUNS} · 2 ordens ######"
run_arm normal
run_arm reverse
log "###### SONDA COMPLETA — comparar com scripts/compare_position_bias.py ######"
touch .cache/.probe_posbias.DONE
