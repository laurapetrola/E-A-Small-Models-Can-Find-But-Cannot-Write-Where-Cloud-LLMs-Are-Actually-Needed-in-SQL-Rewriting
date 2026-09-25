#!/usr/bin/env bash
# P1 — as células com NUVEM, no regime do P1 (sem plano, sem masking, reasoning natural).
#
#   usage:  bash scripts/campanhas/p1_cloud.sh <find> <writer> [runs]
#     find   : qwen | llama | mistral | deepseek | cloud     ("cloud" = o TETO: nuvem no FIND também)
#     writer : deepseek-v4-flash (padrão) | deepseek-v4-pro
#
#   e.g.:  bash scripts/campanhas/p1_cloud.sh qwen  deepseek-v4-flash 3   # degrau aided-cloud
#          bash scripts/campanhas/p1_cloud.sh cloud deepseek-v4-flash 3   # ⭐ o TETO
#
# ⭐ O PAR QUE RESPONDE A MANCHETE DO P1 ("o gargalo está em ACHAR ou em ESCREVER?")
#   Os dois braços acima, rodados com o MESMO writer e na MESMA janela, isolam UMA variável: a
#   qualidade do FIND. Se o teto (nuvem achando) empatar com o 8B local, o achado NÃO é a parede —
#   e a alocação por tier fica justificada por medição, não por intuição.
#
# ⚠️ POR QUE NÃO DÁ PARA PAREAR COM O QUE JÁ EXISTE: o aided-cloud do qwen a n=5 (`6_fresh0804`) usa
#    `v4-pro`. Parear o teto com ele exigiria o teto em pro (US$ 19,15 contra ~US$ 5,73 em flash).
#    Sai mais barato refazer os DOIS braços em flash — e ainda ganha contemporaneidade de janela.
#
# ⚠️ REGIME DO P1, não do P2: PLAN_HINTS=off · MASKING=off · SCHEMA_LINKING=off · sem regras.
#    O P2 mede a ARQUITETURA; o P1 mede CAPACIDADE, e escoramento contamina a medida de capacidade.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

FIND="${1:-qwen}"
WRITER="${2:-deepseek-v4-flash}"
RUNS="${3:-3}"
# ENGINE (4º arg, adicionado 25/08). Aditivo: sem o argumento nada muda, e as chamadas existentes
# seguem em postgres. Existe porque a coluna aided-cloud do MySQL precisa ser REFEITA em `flash`:
# a célula atual (`tpcds_mysql_aided_cloud`) usa `deepseek-chat`, que foi RENOMEADO e hoje devolve
# 400 — writer de outra era, incomparável com o C1.
ENGINE="${4:-postgres}"
case "$ENGINE" in
  postgres|mysql) ;;
  *) echo "engine must be postgres|mysql"; exit 2 ;;
esac

# modo natural de reasoning por família (regra do P1)
case "$FIND" in
  qwen)     FAMILY=qwen-reasoning; REASON=on;  FIND_BE=local ;;
  llama)    FAMILY=llama;          REASON=off; FIND_BE=local ;;
  mistral)  FAMILY=mistral;        REASON=off; FIND_BE=local ;;
  deepseek) FAMILY=deepseek;       REASON=on;  FIND_BE=local ;;
  cloud)    FAMILY=qwen-reasoning; REASON=off; FIND_BE=cloud ;;   # TETO: o FIND vem da nuvem
  *) echo "find must be qwen|llama|mistral|deepseek|cloud"; exit 2 ;;
esac

# BENCHMARK (env `BENCH`, 30/08) — espelha o p1_local.sh. `BENCH=imdb` roda as 13 queries JOB.
# ⚠️ O runner escolhe o benchmark PELA URI (`_benchmark_subdir`), então apontar o banco basta.
# ⚠️ No IMDb VERIFY_DB_URI = DB_URI: dataset real, sem escala reduzida — nenhum land cai no SF1.
BENCH="${BENCH:-tpcds}"
SUF="_${FIND}_${WRITER##*-}"
[ "$BENCH" = imdb ] && SUF="${SUF}_imdb"
[ "$ENGINE" = mysql ] && SUF="${SUF}_mysql"
[ "$FIND" = cloud ] && SUF="_ceiling_${WRITER##*-}"
STORE=".cache/suggestions_p1cloud${SUF}"
LOG="logs/campanhas/p1cloud${SUF}.log"
# ⚠️ `-u` (sem buffer) — mesmo motivo do p1_local.sh (02/09): com o stdout redirecionado o Python
#    bufferiza, e uma célula MORTA na troca de janela leva o buffer junto. O log fica mudo sobre
#    horas de execução. Sem buffer, o log conta o que aconteceu mesmo sem término limpo.
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
  for p in $(ps -eo pid,args --no-headers | grep "[u]vicorn src.main:app" | awk '{print $1}'); do kill "$p" 2>/dev/null; done
  sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP"; sleep 2; return 0; }; sleep 1; done
  log "  server não subiu"; return 1
}

log "###### P1 CLOUD — FIND=${FIND}(${FIND_BE}, rea=${REASON}) · WRITE=${WRITER} · n=${RUNS} ######"
docker start tpcds tpcds-pg-sf1 >/dev/null 2>&1
for i in $(seq 1 30); do
  (exec 3<>/dev/tcp/127.0.0.1/5435) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/5437) 2>/dev/null && { exec 3>&- 4>&-; log "  bancos UP"; break; }
  sleep 3
done
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)

# URIs por engine e benchmark — DEPOIS do PW, que é usado no ramo postgres.
# ⚠️ REESCRITO 31/08: a versão anterior tinha o bloco do ONLY_ARG DENTRO do `else`, e o ramo `mysql`
#    ficava sem QDIRS — o `$DIRS` saía vazio e o run_matrix nem chegava a rodar. O C4 morria em 35 s
#    logo após o canário, sem imprimir `== done:`. Cada `if` fecha o seu próprio `fi`.
if [ "$BENCH" = imdb ]; then
  if [ "$ENGINE" = mysql ]; then
    docker start imdb-mysql >/dev/null 2>&1
    DBURI="mysql+pymysql://root:mysql@localhost:3307/imdb"
  else
    docker start imdb >/dev/null 2>&1
    DBURI="postgresql://postgres:postgres@localhost:5436/imdb"
  fi
  VURI="$DBURI"
  QDIRS="curated heldout"          # as 13 queries JOB
elif [ "$ENGINE" = mysql ]; then
  MYPW=$(docker inspect tpcds-mysql --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
  DBURI="mysql+pymysql://root:${MYPW}@localhost:3308/tpcds"
  VURI="mysql+pymysql://root:${MYPW}@localhost:3309/tpcds"
  QDIRS="curated heldout non-curated"
else
  DBURI="postgresql://postgres:${PW}@localhost:5435/tpcds"
  VURI="postgresql://postgres:${PW}@localhost:5437/tpcds"
  QDIRS="curated heldout non-curated"
fi

# ONLY (env, 30/08): restringe a célula a queries específicas — usado pela sonda `chat × flash`.
ONLY_ARG=""
[ -n "${ONLY:-}" ] && ONLY_ARG="--only ${ONLY}"

$SE "MODEL_FAMILY=${FAMILY}" "REASONING=${REASON}" TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud "FIND_BACKEND=${FIND_BE}" "CLOUD_FIND_MODEL=${WRITER}" \
   "CLOUD_CODER_MODEL=${WRITER}" WRITER_REASONING=on \
   APPLY_HEURISTICS=off PLAN_HINTS=off HW_HINTS=off MASKING=off SCHEMA_LINKING=off \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off LEARNED_RULES_FILE= \
   INDEX_VALIDATION=executed \
   "DB_TYPE=${ENGINE}" "DB_URI=${DBURI}" "VERIFY_DB_URI=${VURI}" \
   "SUGGESTION_STORE_DIR=${STORE}" "INDEX_SUGGESTION_STORE_DIR=.cache/index_p1cloud${SUF}" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FALHOU"; exit 1; }
rm -f .cache/schema_cache.json
restart_server || exit 1

# ⛔⛔ CONFERIR A MATRIZ ANTES DE GASTAR (pedido da usuária, 04/09) ─────────────────────────────
#
#   O QUE ACONTECEU. Em 04/09 o supervisor órfão desta cadeia relançou a perna do `llama` às 07:00.
#   O `canary_check.py` PASSOU — mas ele lê os REGISTROS ANTIGOS do store, não a config viva. O
#   `.env` estava apontando para OUTRA célula (qwen/mysql, deixada por uma cadeia anterior), e o
#   `run_matrix` subiu com ela: o processo se chamava "llama" e fazia trabalho de C4. A linha
#   `== MATRIX | model=qwen-reasoning ... engine=mysql` foi para o log do llama.
#   ⚠️ Os dados NÃO corromperam (cada registro carrega seu próprio rótulo e foi para o store certo),
#   mas horas de janela e dinheiro foram gastos sob o nome errado, e a confusão levou tempo a desatar.
#
#   A REGRA: antes de gastar um centavo, a config VIVA tem de bater com a célula pedida. O canário
#   olha o passado; isto olha o presente. São checagens diferentes e as duas são necessárias.
_mx(){ grep -m1 "^$1=" .env | cut -d= -f2- | tr -d '"'"'"' '; }
_MX_FAM=$(_mx MODEL_FAMILY); _MX_STORE=$(_mx SUGGESTION_STORE_DIR)
_MX_URI=$(_mx DB_URI);       _MX_CODER=$(_mx CLOUD_CODER_MODEL)
_MX_ENG=postgres; case "$_MX_URI" in mysql*) _MX_ENG=mysql ;; esac
log "MATRIZ VIVA: family=$_MX_FAM engine=$_MX_ENG writer=$_MX_CODER store=${_MX_STORE##*/}"
log "  esperado : family=$FAMILY engine=$ENGINE writer=$WRITER store=${STORE##*/}"
_MX_ERR=""
[ "$_MX_FAM"   = "$FAMILY" ] || _MX_ERR="$_MX_ERR family($_MX_FAM≠$FAMILY)"
[ "$_MX_ENG"   = "$ENGINE" ] || _MX_ERR="$_MX_ERR engine($_MX_ENG≠$ENGINE)"
[ "$_MX_CODER" = "$WRITER" ] || _MX_ERR="$_MX_ERR writer($_MX_CODER≠$WRITER)"
[ "$_MX_STORE" = "$STORE"  ] || _MX_ERR="$_MX_ERR store($_MX_STORE≠$STORE)"
if [ -n "$_MX_ERR" ]; then
  log "###### ⛔ MATRIZ ERRADA —$_MX_ERR ######"
  log "⛔ ABORTANDO ANTES DE GASTAR. O .env não descreve a célula pedida — provavelmente outra"
  log "   cadeia escreveu por cima. Derrube TODA a nuvem e relance UMA cadeia só."
  exit 5
fi
log "✅ matriz confere — pode gastar"

DIRS="$QDIRS"   # definido no bloco de engine/benchmark acima
if [ "$(ls "${STORE}"/*.json 2>/dev/null | wc -l)" -eq 0 ]; then
  # ⛔ A QUERY DO CANÁRIO DEPENDE DO BENCHMARK (04/09). Estava fixa em `q1`, que **não existe no
  #    corpus do IMDb** — lá as queries são `1a`, `17f`, `29c`. Resultado: no I-MY o canário rodou
  #    ZERO runs, o `canary_check` reportou "no run recorded yet — nothing to check" e **passou no
  #    vazio**. Não bloqueou (a célula rodou bem), mas também não protegeu: uma config errada teria
  #    passado batido. Canário que não testa nada é pior que canário ausente, porque dá confiança.
  _CQ=q1; [ "$BENCH" = imdb ] && _CQ=1a
  log "canary: UMA run de UMA query ($_CQ)"
  $PY scripts/run_matrix.py --runs 1 --resume --dirs $DIRS --only $_CQ >>"$LOG" 2>&1
fi
# ⚠️ NÃO usar `if ! canary | tee` — o `if` avalia o exit do TEE (sempre 0) e a falha do canário
# some. Descoberto em 20/08: o canário imprimiu "CANARY FAILED" e a cadeia seguiu em frente. A
# salvaguarda existiu por dois dias em CINCO cadeias sem nunca ter bloqueado nada.
# ⛔ EXIGÊNCIA DE ÍNDICE MEDIDO — condicional ao ENGINE (04/09).
#
#   O canário exigia `--index executed` em toda célula. No MySQL isso barrou o C4 quatro vezes, e o
#   motivo NÃO era a célula: até 03/09 o caminho "cria o índice e EXECUTA" **só existia no backend
#   PostgreSQL**. As 19 células MySQL do projeto têm 100% de `validation: estimated` (ou nem o campo,
#   nas anteriores a 18/08). Depois do conserto o caminho existe, mas as queries pesadas do TPC-DS em
#   MySQL não completam no orçamento: a `q67` (`rank() over partition`) passa de 600s no SF20.
#
#   ⛔ O QUE ISTO **NÃO** É: afrouxar a régua. O número de índice do MySQL continua NÃO PUBLICÁVEL —
#      ele sai das claims de índice, e o `gen_cell_doc.py` (04/09) deixou de imprimir percentual
#      quando não há medição. O que muda é só que uma recomendação de índice não-medida deixa de
#      bloquear a MANCHETE da célula, que é a REESCRITA.
#   ⭐ A medição continua LIGADA (`INDEX_VALIDATION=executed`): onde der para medir — IMDb/MySQL, cujas
#      queries rodam em 1,4-62s, e as queries leves do TPC-DS — o número medido é gravado.
#   ⚠️ Revisitar quando a medição em MySQL for viável para as queries pesadas.
_CANARY_IDX="--index executed"
[ "$ENGINE" = mysql ] && _CANARY_IDX="" && log "⚠️ MySQL: canário NÃO exige índice medido (ver comentário) — o índice desta célula fica FORA das claims de índice"

_cout=$($PY scripts/canary_check.py "$(basename "$STORE" | sed 's/^suggestions_//')" \
      --writer "cloud:${WRITER}" --rules off --plan off $_CANARY_IDX 2>&1); _crc=$?
printf '%s\n' "$_cout" | tee -a "$LOG"
if [ "$_crc" -ne 0 ]; then
  log "ABORTANDO: canário recusou."; exit 1
fi
log "canário passou"

$PY scripts/run_matrix.py --runs "$RUNS" --resume --dirs $DIRS $ONLY_ARG >>"$LOG" 2>&1
_RC=$?
# ⛔ PROPAGAR O CÓDIGO (28/08). Antes o script terminava com o exit do `log` (sempre 0), então uma
# célula que não mediu NADA era registrada como concluída pela cadeia. Ver o C4 de 28/08: containers
# de MySQL parados → 63 runs em HTTP 500 → "DONE (exit 0)" → a fila seguiu para o C3.
if [ "$_RC" -ne 0 ]; then
  log "⛔ A CÉLULA NÃO CONCLUIU (rc=$_RC). A cadeia NÃO deve tratá-la como feita."
  exit "$_RC"
fi
# ⛔ DEFESA EM PROFUNDIDADE (02/09) — sem a linha `== done:`, o run_matrix não chegou ao fim,
#    qualquer que seja o rc. No lado local isto pegou o L8: 4h24 de execução, 6 de 21 queries,
#    e "DONE (exit 0)" porque o `$?` lido era o de um `grep`, não o do run_matrix.
if ! grep -q "== done:" "$LOG"; then
  log "###### ⛔ P1 CLOUD ${FIND}/${WRITER} INTERROMPIDA — sem '== done:'. NÃO concluída ######"
  exit 4
fi
log "###### P1 CLOUD ${FIND}/${WRITER} DONE (exit $_RC) ######"
