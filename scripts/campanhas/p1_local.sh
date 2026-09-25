#!/usr/bin/env bash
# P1 — the ALL-LOCAL cells (no cloud API, therefore FREE).
#
#   usage:  bash scripts/campanhas/p1_local.sh <mistral|deepseek> [runs] [rules] [local-coder] [coder-reasoning]
#   e.g.:   bash scripts/campanhas/p1_local.sh mistral
#           bash scripts/campanhas/p1_local.sh deepseek 3
#
# WHY THESE RUN DURING PEAK HOURS
#   The provider charges double between 01:00-04:00 and 06:00-10:00 UTC. These cells never call the
#   API — FIND and WRITE are both local (ollama) — so they cost nothing and are the right work to
#   fill the peak window while the cloud cells are paused. Run them as a BLOCK (user's choice,
#   2026-08-17): simpler and safer than switching the server config four times a day.
#
# ⚠️ NEVER RUN CONCURRENTLY WITH A CLOUD CELL. There is ONE server and it reads .env at startup;
#    two cells at once means one of them silently inherits the other's configuration.
#
# CONFIGURATION — mirrors the existing P1 local column (`p1_llama_aided_local`):
#   FIND local · reasoning in each model's NATURAL mode · no plan · no rules · writer LOCAL
#   (qwen2.5-coder:7b) · no masking (masking exists to protect the CLOUD leg; there is none here).
#
#   "Natural mode" is the P1 cross-model rule: qwen/deepseek reasoning ON, llama/mistral OFF —
#   each model measured as it is meant to be used, declared in the methodology.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

FAMILY="${1:-}"
RUNS="${2:-3}"          # n=3: this is a COVERAGE cell (cross-model), not a count comparison
RULES="${3:-off}"       # off | on — "on" injects the rules the system discovered and S=1-validated
CODER="${4:-qwen2.5-coder:7b}"   # the LOCAL writer under test
WREASON="${5:-}"                 # ""|on|off — reasoning of the LOCAL writer
# WHY THE WRITER IS A PARAMETER (2026-08-18): the P1 question is whether the local writer fails
# because it is SMALL or because it does not REASON. qwen2.5-coder:7b has no reasoning and fails
# mechanically ~71% of the time; disabling reasoning on the CLOUD writer was equally catastrophic
# (reach 14->8, 9 non-equivalences). Three arms, same FIND:
#   qwen2.5-coder:7b   code model, no reasoning      (the current default)
#   deepseek-coder:6.7b code model, SAME FAMILY as the cloud writer
#   qwen3:8b + reasoning  reasoning model, same size class -> isolates REASONING from SIZE
# WHY THE RULES ARM EXISTS HERE (2026-08-18): the rules were REMOVED from the P2 architecture (they
# redistribute rather than improve: on the stronger model they add +2 marginal reach and COST one
# consistent query). What survives is a P1 result — the CONTOUR LAW: external scaffolding is worth
# more to a weaker receiver. Measured so far: llama (weaker) 10->12 with q63 0/5->5/5 robust; qwen
# (stronger) 12->14 but consistency 10->9. Adding mistral (weakest) turns two points into a CURVE.
case "$FAMILY" in
  mistral)  REASON=off; FAMILY_ENV=mistral  ;;   # non-reasoning -> modo natural OFF
  deepseek) REASON=on;  FAMILY_ENV=deepseek ;;   # deepseek-r1:8b raciocina -> natural ON
  # qwen/llama added 19/08: the HEADLINE ladder is qwen's, and an audit found it confounded —
  # aided-local was measured with reasoning ON while aided-cloud was measured with reasoning OFF,
  # in different provider eras. The P1 rule is each family in its NATURAL mode: qwen ON, llama OFF.
  # ⚠️ `qwen` mapeia para qwen2.5:7b, que NÃO suporta thinking → HTTP 500 se REASONING=on.
  # Quem raciocina é a família `qwen-reasoning` (qwen3:8b). Custou 105 runs falhadas em 19/08 22:03.
  qwen)     REASON=on;  FAMILY_ENV=qwen-reasoning ;;
  llama)    REASON=off; FAMILY_ENV=llama ;;
  # deepseek-llama added 25/08: the 5th panel model had no raw cell at all. It is R1 DISTILLED ONTO
  # Llama 3.1, so it reasons (natural mode ON) even though plain llama3.1 does not. Distinct model
  # from `deepseek` (deepseek-r1:8b) — never conflate the two in a table.
  deepseek-llama) REASON=on; FAMILY_ENV=deepseek-llama ;;
  *) echo "usage: $0 <mistral|deepseek|deepseek-llama|qwen|llama> [runs] [rules] [coder] [coder-reasoning]"; exit 2 ;;
esac

# ⚠️ `-u` (sem buffer) NÃO é cosmético — adicionado 02/09. O stdout do run_matrix é redirecionado
#    para um arquivo, e aí o Python passa a bufferizar. Quando a célula é MORTA no meio (troca de
#    janela pico→nuvem à 01:00), o buffer morre com o processo: o log do L8 ficou com 16 linhas,
#    sem uma única linha por run e sem o `== done:` — 4h24 de execução invisíveis. Com `-u` o log
#    conta o que aconteceu mesmo quando o processo não termina.
PY=".venv/bin/python -u"
SE="$PY scripts/campanhas/setenv.py"
SUF=""; [ "$RULES" = "on" ] && SUF="_hint"
# ⚠️ O SUFIXO DO SCHEMA-LINKING TEM DE VIR **ANTES** DE QUALQUER `STORE=` (corrigido 29/08).
# Na 1ª versão pus `SL`/`SUF` na linha 157, DEPOIS dos dois `STORE=` (71 e 141) — e o SL1 começou a
# escrever no store do L4. Pegamos com 0 runs gravados, mas era contaminação silenciosa da célula
# mais importante da fila. REGRA: tudo que altera SUF vem antes de qualquer STORE=.
SL="${SL:-off}"
[ "$SL" = "on" ] && SUF="${SUF}_schemalink"
# store per writer arm: two arms with the same FIND must never share a store
case "$CODER" in
  qwen2.5-coder:7b)   SUF="${SUF}" ;;                       # o default mantém o nome já em uso
  deepseek-coder:6.7b) SUF="${SUF}_dscoder" ;;
  qwen3:8b)           SUF="${SUF}_qwen3writer" ;;
  *)                  SUF="${SUF}_$(echo "$CODER" | tr -cs 'a-zA-Z0-9' '_')" ;;
esac
[ "$WREASON" = "on" ] && SUF="${SUF}_wthink"
# ⛔⛔ BUG #25 (16/09/2026) — O SUFIXO DO BENCHMARK FOI MOVIDO PARA CA.
#   Ele estava no bloco `BENCH` (~linha 196), DEPOIS dos dois `STORE=` e ANTES do `IDXSTORE=`.
#   Em 16/09 a perna I1 rodou com `DB_URI` apontando para o **IMDb** e gravou em
#   `suggestions_p1_qwen_aided_local_raw` — o store do **L0c (TPC-DS)**, celula FECHADA e DOCUMENTADA.
#   19 registros de IMDb entraram nela. O store de INDICE recebeu o `_imdb` corretamente, e foi essa
#   assimetria que denunciou o problema.
#   ⚠️ REINCIDENCIA EXATA da regra escrita acima em 29/08 ("tudo que altera SUF vem antes de
#      qualquer STORE="), depois do mesmo acidente com o schema-linking. O bloco BENCH e de 26/08 e
#      nunca foi movido para obedece-la.
#   ⚠️ Nunca estourou porque este caminho (`BENCH=imdb` no p1_local.sh) NUNCA havia sido usado:
#      todas as celulas de IMDb do projeto vieram do `p1_cloud.sh` ou do `imdb_battery.sh`.
[ "${BENCH:-tpcds}" = "imdb" ] && SUF="${SUF}_imdb"
STORE=".cache/suggestions_p1_${FAMILY}_aided_local${SUF}"
LOG="logs/campanhas/p1_${FAMILY}_local${SUF}.log"
mkdir -p logs/campanhas
: > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

guard_no_cloud_cell(){
  # ⚠️ IGNORA processos CONGELADOS (estado T). O supervisor de pico congela a célula de nuvem com
  # SIGSTOP antes de soltar o trabalho local — e um processo parado CONTINUA aparecendo no pgrep.
  # Sem este filtro a guarda via a nuvem que o próprio supervisor acabara de congelar e abortava:
  # em 20/08 03:01 isso matou a célula `qwen raw` em 9/63 e a fila avançou como se tivesse terminado.
  # Os dois mecanismos brigavam entre si.
  local running
  running=$(ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -cv "^T")
  # ⏳ ESPERAR, NÃO ABORTAR (31/08) — mesmo conserto que o p1_cloud.sh recebeu em 29/08.
  #
  #   O QUE ACONTECEU: às 22:00 a janela de pico abriu, o supervisor CONGELOU o `run_matrix` da nuvem
  #   (C3) e soltou o trabalho local — mas o processo ainda não tinha virado estado `T` quando esta
  #   guarda olhou. Ela via "outra célula rodando" e ABORTAVA; a fila esperava 5 min e repetia, três
  #   vezes seguidas, com a máquina ociosa no meio.
  #   ⛔ A assimetria era minha: consertei o lado da NUVEM para esperar e deixei o LOCAL abortando.
  #   ✅ Esperar resolve sozinho: ou o congelamento se completa (o processo vira T e some da contagem),
  #      ou a célula de nuvem termina. Não há nada a "resolver", só a aguardar.
  local n=0
  while [ "${running:-0}" -gt 0 ]; do
    [ $((n % 10)) -eq 0 ] && log "  aguardando a máquina ficar livre (outra célula não-congelada)... ${n}x"
    n=$((n+1)); sleep 30
    running=$(ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -cv "^T")
  done
  [ "$n" -gt 0 ] && log "  máquina livre depois de ~$((n/2)) min de espera"
  if false; then
    log "ABORT: another cell is running (não-congelada). Um servidor, um .env."; return 1
  fi
  return 0
}

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

# The local models must actually be pulled, or every run fails identically and looks like a model limit.
check_models(){
  local want="$1"
  for m in "$want" "$CODER"; do
    curl -s http://localhost:11434/api/tags | grep -q "\"$m\"" || { log "  MISSING ollama model: $m"; return 1; }
  done
  log "  ollama models present ($want + $CODER)"
}

case "$FAMILY" in
  mistral)  OLLAMA_MODEL="mistral:7b-instruct" ;;
  deepseek) OLLAMA_MODEL="deepseek-r1:8b" ;;
  qwen)     OLLAMA_MODEL="qwen3:8b" ;;
  llama)    OLLAMA_MODEL="llama3.1:8b" ;;
  deepseek-llama) OLLAMA_MODEL="deepseek-r1:8b-llama-distill-q4_K_M" ;;
esac

log "###### P1 ALL-LOCAL — FIND=${FAMILY}(rea=${REASON}) · WRITE=${CODER}(rea=${WREASON:-default}) · rules=${RULES} · n=${RUNS} · no API ######"
guard_no_cloud_cell || exit 1
health || exit 1
check_models "$OLLAMA_MODEL" || exit 1

# ENGINE (6º arg) e REASONING FORÇADO (7º arg) — adicionados 21/08 para duas células do P1:
#   · MySQL: fecha a exposição de ENGINE ÚNICO. A escada de MySQL do qwen já tem `raw` (8/21,
#     `tpcds_mysql`) e `aided-cloud` (7/20); falta o degrau `aided-local`.
#   · REASONING forçado: par ON × OFF no regime do P1. O par que temos hoje (IMDb/MySQL, braço raw,
#     0/13 × 2/13) é de OUTRO quadrante; este é limpo, na célula que a manchete usa.
ENGINE="${6:-postgres}"
[ -n "${7:-}" ] && REASON="$7" && SUF="${SUF}_rea${7}"
case "$ENGINE" in
  postgres) CONTS="tpcds tpcds-pg-sf1" ;;
  mysql)    CONTS="tpcds-mysql tpcds-mysql-sf1"; SUF="${SUF}_mysql" ;;
  *) echo "engine must be postgres|mysql"; exit 2 ;;
esac
STORE=".cache/suggestions_p1_${FAMILY}_aided_local${SUF}"
LOG="logs/campanhas/p1_${FAMILY}_local${SUF}.log"
docker start $CONTS >/dev/null 2>&1
PGPW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
# SCHEMA-LINKING (env `SL`, adicionado 29/08). Default **off** — o regime do P1 não dá schema ao
# modelo, e TODA a matriz medida até aqui está assim (nenhuma célula do P1 tem `+schemalink`).
#
# POR QUE EXISTE O EIXO: verificado no código em 29/08 que, com `SCHEMA_LINKING=off`, o modelo
# **NUNCA vê o schema** — nem colunas, nem chaves de junção — em NENHUM braço, raw incluído
# (`build_fallback_prompt` recebe `schema_context=""`). ⚠️ Isso torna o nosso piso mais baixo que o de
# QUALQUER trabalho lido: LLM-R², R-Bot, GenRewrite, LITHE, QUITE, LLM-QO e QueryBooster todos dão
# schema. O contra-argumento honesto é que schema-linking é DETERMINÍSTICO e BARATO — é higiene, não
# ajuda cara — e um revisor pode chamar o piso de artificialmente baixo.
# ✅ A resposta não é negar: é MEDIR. `SL=on` dá o par limpo.
# 📊 Evidência de que importa (29/06): schema-linking no writer levou o qwen de 0/9 → 1/9 land, e a
#    q9 virou de `mechanics_failed` para S≠1 — matou o erro de COLUNA e expôs a EQUIVALÊNCIA.

# BENCHMARK (env `BENCH`, adicionado 26/08). Estilo do `RAWARM`: env em vez de 8º argumento
# posicional. `BENCH=imdb` roda as 13 queries JOB; o default segue TPC-DS.
#
# ⚠️ O runner escolhe o benchmark PELO DB_URI (`_benchmark_subdir` em run_matrix.py: "imdb" ou "job"
#    na URI → subdiretório JOB). Não há flag de benchmark aqui — apontar o banco certo BASTA, e é
#    por isso que trocar a URI é suficiente.
# ⚠️ No IMDb, VERIFY_DB_URI = DB_URI: é dataset REAL, não gerado, então não existe instância de
#    escala reduzida. Consequência: nenhum land de IMDb pode cair no fallback SF1 — ou a equivalência
#    vale em escala real, ou não vale. (Espelha o que o imdb_battery.sh já faz.)
# ⚠️ Os containers de IMDb ficam PARADOS entre campanhas — `docker start` antes de medir.
if [ "${BENCH:-tpcds}" = "imdb" ]; then
  # ⚠️ o sufixo `_imdb` JA foi aplicado acima, antes do primeiro STORE= (bug #25).
  #    Aqui ficam so as URIs, os conteineres e os diretorios de query.
  if [ "$ENGINE" = mysql ]; then
    CONTS="imdb-mysql"
    DBURI="mysql+pymysql://root:mysql@localhost:3307/imdb"
  else
    CONTS="imdb"
    DBURI="postgresql://postgres:postgres@localhost:5436/imdb"
  fi
  VURI="$DBURI"
  QDIRS="curated heldout"          # as 13 queries JOB (10 curated + 3 heldout)
elif [ "$ENGINE" = mysql ]; then
  MYPW=$(docker inspect tpcds-mysql --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
  DBURI="mysql+pymysql://root:${MYPW}@localhost:3308/tpcds"
  VURI="mysql+pymysql://root:${MYPW}@localhost:3309/tpcds"
  QDIRS="curated heldout non-curated"
else
  DBURI="postgresql://postgres:${PGPW}@localhost:5435/tpcds"
  VURI="postgresql://postgres:${PGPW}@localhost:5437/tpcds"
  QDIRS="curated heldout non-curated"
fi
# RAW = um agente só (sem writer separado) — o degrau de baixo da escada. `ARM=raw` como 5º arg.
TWO_AGENT="on"; RUN_ARM_V="aided"
if [ "${RAWARM:-}" = "raw" ]; then TWO_AGENT="off"; RUN_ARM_V="raw"; STORE="${STORE}_raw"; fi

# LEGACY STORE OVERRIDE (added 25/08). The raw cells of deepseek/llama/mistral were created by an
# older version of this script and are named `p1_<family>_raw`, while today's naming yields
# `p1_<family>_aided_local_raw`. Without an override, resuming one of them would silently start a
# NEW store and re-run everything from zero instead of filling the gaps. `STORE_NAME` points at the
# existing store by name (no `suggestions_` prefix); `INDEX_STORE_NAME` does the same for indexes.
if [ -n "${STORE_NAME:-}" ]; then STORE=".cache/suggestions_${STORE_NAME}"; fi
IDXSTORE=".cache/index_p1_${FAMILY}_aided_local${SUF}"
[ "${RAWARM:-}" = "raw" ] && IDXSTORE="${IDXSTORE}_raw"
[ -n "${INDEX_STORE_NAME:-}" ] && IDXSTORE=".cache/${INDEX_STORE_NAME}"
$SE "MODEL_FAMILY=${FAMILY_ENV}" "REASONING=${REASON}" "TWO_AGENT_MODE=${TWO_AGENT}" "RUN_ARM=${RUN_ARM_V}" \
   WRITER_BACKEND=local "LOCAL_CODER_MODEL=${CODER}" "WRITER_REASONING=${WREASON}" FIND_BACKEND=local \
   "APPLY_HEURISTICS=${RULES}" PLAN_HINTS=off HW_HINTS=off MASKING=off "SCHEMA_LINKING=${SL}" \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   "DB_TYPE=${ENGINE}" "DB_URI=${DBURI}" "VERIFY_DB_URI=${VURI}" \
   "SUGGESTION_STORE_DIR=${STORE}" \
   "INDEX_SUGGESTION_STORE_DIR=${IDXSTORE}" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE on both instances..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FAILED - aborting"; exit 1; }
rm -f .cache/schema_cache.json && log "  schema cache cleared"

# CANARY — same protection the P2 chain has: spend ONE run, verify the RECORD matches the intent,
# abort if not. These cells are free in API terms but each takes hours of machine; running the wrong
# writer for 63 runs and finding out afterwards is the mistake this prevents.
log "running 21 queries x${RUNS} (resuming) -> ${STORE}"
restart_server || exit 1

# ⛔⛔ CONFERIR A MATRIZ ANTES DE RODAR (04/09) — espelha a guarda do p1_cloud.sh.
#   O canário lê os registros ANTIGOS do store; isto lê a config VIVA. São checagens diferentes:
#   em 04/09 o canário passou para uma célula enquanto o `.env` descrevia OUTRA, deixada por uma
#   cadeia anterior. Aqui não há dinheiro em jogo, mas há JANELA — e uma célula local rodando sob
#   a matriz errada gasta horas de pico e grava no store de outra.
_mx(){ grep -m1 "^$1=" .env | cut -d= -f2- | tr -d '"'"'"' '; }
_MX_FAM=$(_mx MODEL_FAMILY); _MX_STORE=$(_mx SUGGESTION_STORE_DIR); _MX_URI=$(_mx DB_URI)
_MX_ENG=postgres; case "$_MX_URI" in mysql*) _MX_ENG=mysql ;; esac
log "MATRIZ VIVA: family=$_MX_FAM engine=$_MX_ENG store=${_MX_STORE##*/}"
log "  esperado : family=$FAMILY_ENV store=${STORE##*/}"
_MX_ERR=""
[ "$_MX_FAM"   = "$FAMILY_ENV" ] || _MX_ERR="$_MX_ERR family($_MX_FAM≠$FAMILY_ENV)"
[ "$_MX_STORE" = "$STORE"      ] || _MX_ERR="$_MX_ERR store($_MX_STORE≠$STORE)"
if [ -n "$_MX_ERR" ]; then
  log "###### ⛔ MATRIZ ERRADA —$_MX_ERR · ABORTANDO ANTES DE OCUPAR A JANELA ######"
  exit 5
fi
log "✅ matriz confere"
if [ "$(ls "${STORE}"/*.json 2>/dev/null | wc -l)" -eq 0 ]; then
  log "canary: one run first to validate the configuration..."
  $PY scripts/run_matrix.py --runs 1 --resume --dirs $QDIRS >>"$LOG" 2>&1
  if [ "${RAWARM:-}" = "raw" ]; then
    _exp_writer=""
  else
    _exp_writer="local:${CODER}"
  fi
  [ "$WREASON" = "on" ]  && _exp_writer="${_exp_writer}+think"
  [ "$WREASON" = "off" ] && _exp_writer="${_exp_writer}+nothink"
  # ⚠️ NÃO usar `if ! canary | tee` — o `if` avalia o exit do TEE (sempre 0) e a falha do canário
# some. Descoberto em 20/08: o canário imprimiu "CANARY FAILED" e a cadeia seguiu em frente. A
# salvaguarda existiu por dois dias em CINCO cadeias sem nunca ter bloqueado nada.
_cout=$($PY scripts/canary_check.py "$(basename "$STORE" | sed 's/^suggestions_//')"         --writer "$_exp_writer" --find "$FAMILY"         --rules "$RULES" --plan off 2>&1); _crc=$?
printf '%s\n' "$_cout" | tee -a "$LOG"
if [ "$_crc" -ne 0 ]; then
    log "ABORTING: canary rejected the configuration. Fix it before spending the cell."
    exit 1
  fi
  log "canary passed — proceeding"
fi
# ⭐ ONLY (env, 06/09) — espelha o `p1_cloud.sh`, que já tinha isto desde 30/08.
#   Serve para FECHAR PENDÊNCIA de query específica sem re-varrer a célula inteira: em 06/09 a
#   auditoria achou o `SL1` com q85/q96 em 3/5 e o `L0b` com q81 em 2/3 — 5 runs escondidos DENTRO
#   de células cujo total parecia saudável (101 e 82 runs, 21/21 queries). Sem `ONLY`, fechar isso
#   exigiria varrer as 21 queries de novo.
#   ⚠️ O `--resume` continua mandando: ele só completa o que falta, não repete o que já existe.
ONLY_ARG=""
[ -n "${ONLY:-}" ] && { ONLY_ARG="--only ${ONLY}"; log "ONLY=${ONLY} — restringindo a célula a estas queries"; }
$PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs $QDIRS $ONLY_ARG >>"$LOG" 2>&1
_RC=$?
# ⚠️ run_matrix devolve 0 mesmo quando TODAS as runs falham. Em 19/08 a cadeia reportou
# "DONE (exit 0)" depois de 105 falhas de HTTP 500 — e a janela de pico seguiu adiante como se a
# célula tivesse rodado. Conferir o resumo do próprio log é a única checagem confiável.
if grep -qE "== done: 0 ok, [1-9][0-9]* failed" "$LOG"; then
  log "###### ⛔ P1 ${FAMILY} LOCAL FALHOU — 0 runs OK. NÃO tratar como concluída ######"
  grep -m1 "== done:" "$LOG" | tee -a "$LOG"
  exit 3
fi
# ⛔⛔ CÉLULA INTERROMPIDA NO MEIO (adicionado 02/09) — o buraco que a guarda acima NÃO cobria.
#
#   O QUE ACONTECEU COM O L8: à 01:00 a janela de pico fechou, o supervisor matou o `run_matrix`
#   para ceder a máquina à nuvem, e o processo morreu **sem escrever a linha `== done:`**. A guarda
#   de cima procura "0 ok, N failed" — não achou nada, passou em branco — e o script registrou
#   "DONE (exit 0)". A fila marcou a célula como concluída **com 6 de 21 queries**.
#   ⚠️ É exatamente o mesmo bug que o `p1_cloud.sh` teve em 28/08, e que eu consertei LÁ e não aqui.
#
#   REGRA: sem a linha `== done:`, o run_matrix NÃO terminou. Isso é falha, não conclusão.
if ! grep -q "== done:" "$LOG"; then
  log "###### ⛔ P1 ${FAMILY} LOCAL INTERROMPIDA — sem linha '== done:' (rc=$_RC). NÃO concluída ######"
  exit 4
fi
if [ "$_RC" -ne 0 ]; then
  log "###### ⛔ P1 ${FAMILY} LOCAL rc=$_RC — NÃO tratar como concluída ######"
  exit "$_RC"
fi
log "###### P1 ${FAMILY} LOCAL DONE (exit $_RC) ######"
log "REMINDER: restore the cloud config before resuming a cloud cell — the chains do their own setenv,"
log "          so just launch the next chain normally; do NOT hand-edit .env."
