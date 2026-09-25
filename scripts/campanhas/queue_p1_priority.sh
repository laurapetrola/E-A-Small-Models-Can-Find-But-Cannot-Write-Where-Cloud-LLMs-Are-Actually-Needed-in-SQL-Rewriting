#!/usr/bin/env bash
# FILA DE CONTINGÊNCIA — dispara SE o par IMDb/MySQL confirmar que o plano não se sustenta.
#
#   uso:  nohup setsid bash scripts/campanhas/queue_p1_priority.sh >/dev/null 2>&1 &
#
# ⛔ NÃO DISPARAR ANTES DO VEREDITO. Conferir primeiro:
#      python scripts/gen_plan_verdict.py     (ou o par em final/p2/imdb_mysql_plan_*.md)
#   O critério é a régua de CONSISTÊNCIA (>=3/5), não a de alcance — a de alcance conta um land 1/5
#   igual a um 5/5 e já enganou três vezes em 18-19/08.
#
# O QUE ESTA FILA ASSUME (e por isso é contingência, não plano A):
#   · o plano NÃO se sustenta em nenhum dos dois engines na composição nova
#     → PG (n=5): q69 51,2% SEM plano × 11,2% COM · q28 empate
#     → MySQL: a decidir pelo par
#   · logo, MySQL/TPC-DS (US$ 11,00) e B+B2 (US$ 8,38) deixam de ter objeto — existem SÓ para medir
#     o efeito do plano. **US$ 19,38 e ~110 h de nuvem liberados.**
#   · o P1 passa a ser prioridade, e essa verba financia as células dele.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/queue_p1_priority.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh

log "###### P1 EM PRIORIDADE — contingência do veredito do plano ######"
log "⚠️ MySQL/TPC-DS e B+B2 CANCELADAS (só existiam para medir o plano) — US\$ 19,38 liberados"
while ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -qv "^T"; do sleep 120; done

# ── C3: o monolítico NO REGIME DO P1 (sem plano, sem masking). Fecha a tripla com C1 e C2.
#    C1×C2 isola a QUALIDADE DO ACHADO · C2×C3 isola a DECOMPOSIÇÃO (mesmo modelo, muda só se o
#    achado é externalizado em texto). O `0d` atual roda na composição do P2 e não compara com o teto.
log "⭐ C3 — monolítico (1 agente) no regime do P1, n=5"
bash "$O" bash scripts/campanhas/control_monolithic.sh 5 \
   q1 q3 q7 q9 q11 q18 q25 q27 q28 q30 q38 q40 q51 q63 q69 q73 q81 q84 q85 q88 q96
log "  C3 exit=$?"

# ── P1-1: a coluna aided-cloud REFEITA — MESMO writer, MESMA janela, para as quatro famílias.
#    Sem isso a comparação ENTRE FAMÍLIAS mistura writers e eras (o confundimento que invalidou o
#    par de julho do llama e a escada do qwen).
log "P1-1 (a): llama FIND + flash, n=3"
bash "$O" bash scripts/campanhas/p1_cloud.sh llama deepseek-v4-flash 3
log "  exit=$?"
log "P1-1 (b): mistral FIND + flash, n=3"
bash "$O" bash scripts/campanhas/p1_cloud.sh mistral deepseek-v4-flash 3
log "  exit=$?"
log "P1-1 (c): deepseek-r1 FIND + flash, n=3"
bash "$O" bash scripts/campanhas/p1_cloud.sh deepseek deepseek-v4-flash 3
log "  exit=$?"

log "###### CONTINGÊNCIA CONCLUÍDA — falta só o P1-4 (IMDb), que precisa da cadeia imdb_battery ######"
log "     P1-4: bash scripts/campanhas/imdb_battery.sh deepseek-v4-flash postgres off 3"
