#!/usr/bin/env bash
# 0d — THE MONOLITHIC BASELINE (the cloud model working ALONE, no decomposition).
#
#   usage:  bash scripts/campanhas/control_monolithic.sh [runs] [queries...]
#   e.g.:   bash scripts/campanhas/control_monolithic.sh 3 q69 q28 q1 q25 q27 q30
#
# WHY IT EXISTS
#   Two different objections, and only this cell answers the second:
#     "the weak FIND is useless"      -> answered by P1-3 (strong model in FIND *and* WRITE, same
#                                        WRITE as ours, so only the FIND's quality varies)
#     "why decompose at all?"         -> NOT answered anywhere: every cell we have decomposes.
#   LITHE — the closest competitor, same venue (EDBT'26) — is MONOLITHIC. If decomposition is the
#   contribution, the baseline to beat is the SAME MODEL WITHOUT DECOMPOSING, which is his design.
#   In 3,256 recorded runs there is not one where a cloud model works alone.
#
# THE DESIGN — same model on both sides
#   ours:    qwen (weak, local) FINDS -> flash WRITES
#   control: flash alone, one free-form call
#   Only ONE thing varies: whether the strategy from the weak model exists. Any difference is
#   attributable to the decomposition and not to a change of model.
#
# EVERYTHING ELSE IS HELD AT THE SHIPPED COMPOSITION: masking + schema-linking ON, rules OFF,
# plan ON, INDEX_VALIDATION=executed, same S=1 gate, same databases.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

RUNS="${1:-3}"; shift || true
# ⚠️ DEFAULT ATUALIZADO 01/09 para as 21 do corpus do P1. O anterior era `q69 q28 q1 q25 q27 q30`
# — seis queries da era do P2, escolhidas como "onde o nosso sistema landa bem". Chamar este script
# sem argumentos rodaria um recorte enviesado e minúsculo.
QUERIES="${*:-q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q40 q50 q51 q63 q67 q69 q73 q81 q85 q96}"
MODEL="deepseek-v4-flash"                # the shipped writer — same model as ours, by design
# ⚠️ STORE NOVO (31/08). O `_flash` tem **43 runs no regime do P2** (PLAN_HINTS=on, MASKING=on,
# SCHEMA_LINKING=on) — este script nunca fora convertido quando o P2 saiu do ciclo, e eu o coloquei
# na tripla como C3 sem conferir. Aqueles 43 runs NÃO comparam com o C2 (que é noplan, sem masking,
# sem schema): mudariam TRÊS variáveis além do número de agentes, e nenhuma conclusão sobreviveria.
# ⛔ Não completar o store antigo — ele fica como registro do regime P2.
STORE=".cache/suggestions_control_monolithic_${MODEL##*-}_p1"
LOG="logs/campanhas/control_monolithic.log"
# ⚠️ `-u` (sem buffer) — mesmo motivo do p1_local/p1_cloud (02/09). Com o stdout indo para
#    arquivo o Python bufferiza, e a célula morta na virada de janela leva o log junto. Hoje
#    mesmo o log desta célula ficou mudo por 13 min enquanto ela trabalhava normalmente.
PY=".venv/bin/python -u"
SE="$PY scripts/campanhas/setenv.py"
mkdir -p logs/campanhas; : > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

# ⏳ ESPERAR, NÃO ABORTAR (28-29/08). Este guard era `exit 1`, e isso transformava uma espera de
# 3 horas em uma CÉLULA PERDIDA. Na madrugada de 29/08, às 01:00, a janela de pico abriu, o
# `peak_local` congelou a nuvem e soltou o trabalho local; o C3 tentou retomar, achou o `run_matrix`
# do L4 vivo e ABORTOU — e a fila seguiu abortando as TRÊS pernas do P1-1, uma a cada 55 segundos.
# Quatro células perdidas em 4 minutos.
#
# A máquina fica livre sozinha quando a janela de pico fechar. Esperar é o comportamento correto:
# não há nada a "resolver", só a aguardar. ⚠️ Ignorar processos CONGELADOS (estado T) — o supervisor
# de pico usa SIGSTOP, e um processo parado continua aparecendo no `ps`.
# ⛔ Sem teto de espera de propósito: a alternativa (desistir) é justamente o bug que isto conserta.
_wait_free(){
  local n=0
  while ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -qv "^T"; do
    [ $((n % 10)) -eq 0 ] && log "  aguardando a máquina ficar livre (outra célula rodando)... ${n}x"
    n=$((n+1)); sleep 120
  done
  [ "$n" -gt 0 ] && log "  máquina livre depois de ~$((n*2)) min de espera"
  return 0
}
_wait_free


restart_server(){
  for p in $(pgrep -f "uvicorn src.main:app"); do kill "$p" 2>/dev/null; done; sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server did not come up"; return 1
}

log "###### 0d MONOLITHIC BASELINE — ${MODEL} ALONE · n=${RUNS} · queries: ${QUERIES} ######"
docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)

# TWO_AGENT_MODE=off  -> no architect/writer split: ONE model, ONE free-form call.
# FIND_BACKEND=cloud  -> that one model is the cloud model (CLOUD_FIND_MODEL), not the local qwen.
# CLOUD_CODER_MODEL   -> kept identical so any mechanical correction also stays on the same model.
$SE MODEL_FAMILY=qwen-reasoning REASONING=off TWO_AGENT_MODE=off RUN_ARM=aided \
   WRITER_BACKEND=cloud FIND_BACKEND=cloud "CLOUD_FIND_MODEL=${MODEL}" "CLOUD_CODER_MODEL=${MODEL}" \
   APPLY_HEURISTICS=off PLAN_HINTS=off HW_HINTS=off \
   MASKING=off SCHEMA_LINKING=off \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   INDEX_VALIDATION=executed WRITER_REASONING=on \
   DB_TYPE=postgres "DB_URI=postgresql://postgres:${PW}@localhost:5435/tpcds" \
   "VERIFY_DB_URI=postgresql://postgres:${PW}@localhost:5437/tpcds" \
   "SUGGESTION_STORE_DIR=${STORE}" \
   "INDEX_SUGGESTION_STORE_DIR=.cache/index_control_monolithic_flash" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE on both instances..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FAILED"; exit 1; }
rm -f .cache/schema_cache.json

restart_server || exit 1
# ⛔ `heldout_op2` REMOVIDO 02/09 — era resíduo do P2. Ele foi posto aqui para alcançar a q28, que
#    estava na lista de queries da época do P2; o corpus do P1 são as 21 e a q28 não é uma delas.
#    Consequência de tê-lo mantido: o store desta célula ficou com **q28 (5 runs) e q88 (5 runs)**,
#    e os números do C3 (alcance 16, mech_f 2%) foram calculados sobre 18 queries do corpus + 2
#    estranhas — ou seja, o C2×C3 comparava CONJUNTOS DE QUERIES DIFERENTES. Os 10 registros foram
#    para .cache/_quarentena_p2_fora_corpus/ (não apagados: são medições válidas, de outro corpus).
#    ✅ Verificado antes de remover: nenhuma das 21 vive só no heldout_op2.
DIRS="curated heldout non-curated"

# CANARY: one run, then prove the strategy field is EMPTY. If TWO_AGENT_MODE silently stayed on, this
# cell would compare the architecture against itself, tie, and the tie would read as evidence.
if [ "$(ls "${STORE}"/*.json 2>/dev/null | wc -l)" -eq 0 ]; then
  log "canary: one run first — must prove the model got NO strategy"
  $PY scripts/run_matrix.py --runs 1 --resume --dirs $DIRS --only $QUERIES >>"$LOG" 2>&1
fi
# ⚠️ NÃO usar `if ! canary | tee` — o `if` avalia o exit do TEE (sempre 0) e a falha do canário
# some. Descoberto em 20/08: o canário imprimiu "CANARY FAILED" e a cadeia seguiu em frente. A
# salvaguarda existiu por dois dias em CINCO cadeias sem nunca ter bloqueado nada.
_cout=$($PY scripts/canary_check.py "$(basename "$STORE" | sed 's/^suggestions_//')" \
      --single-agent --index executed --rules off 2>&1); _crc=$?
printf '%s\n' "$_cout" | tee -a "$LOG"
if [ "$_crc" -ne 0 ]; then
  log "ABORTING: canary rejected — this is NOT a monolithic run."
  exit 1
fi
log "canary passed — the model is working alone"

$PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs $DIRS --only $QUERIES >>"$LOG" 2>&1
log "###### 0d DONE (exit $?) ######"
log "COMPARE against the SAME queries in the architecture cells. Reminder: n=3 on 6 queries is"
log "SCREENING — do not report it as a result. The full version is 21 queries x5."
