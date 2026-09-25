#!/usr/bin/env bash
# CONTINUAÇÃO DA FILA DA NUVEM — o que roda DEPOIS do C4 (04/09).
#
#   POR QUE UM ARQUIVO NOVO em vez de editar o anterior: o coordenador anterior estava EM EXECUÇÃO, e
#   o bash lê o script por OFFSET DE BYTE — inserir linhas num script rodando desloca o offset e ele
#   passa a executar lixo. Então: o C4 continua na sua própria árvore (órfã, de propósito), e esta
#   cadeia ESPERA por ela antes de seguir.
#
#   ORDEM:
#     1. I-MY  — IMDb + MySQL, aided-cloud, 13q x3   ⭐ 4º QUADRANTE (novo no P1)
#     2. P1-1b — mistral acha, flash escreve          cobertura
#     3. P1-1c — deepseek-r1 acha, flash escreve      cobertura
#
#   ⭐ POR QUE O I-MY VEM ANTES DA COBERTURA. Dois motivos que só ficaram claros em 04/09:
#     (a) o P1 nunca rodou IMDb/MySQL — as células de agosto são de outro regime (`+schemalink`,
#         `nothink`), teriam de ser refeitas de qualquer forma;
#     (b) ⭐ é a ÚNICA célula MySQL onde o índice pode ser MEDIDO por execução. IMDb não tem escala
#         reduzida — é o dado real. Cronometrado hoje no MySQL: `1a` 1,4s · `29c` 12,5s · `17f` 61,7s,
#         tudo dentro do orçamento de 600s. O muro que barrou o C4 é específico do TPC-DS em SF20
#         (a `q67` passa de 600s). ⇒ pode produzir o 1º índice em MySQL validado por execução do
#         projeto — as 19 células MySQL existentes têm 100% de `validation: estimated`.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_pos_c4.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh
_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}
# ⛔ piso US$ 1,00 — um timeout custa US$ 0,60 (medido 02/09); piso menor estoura no meio da célula.
_check_saldo(){
  local s; s=$(_saldo); log "   saldo: US\$ ${s:-?}"
  if [ -n "${s:-}" ] && [ "$(echo "$s < 1.00" | bc -l 2>/dev/null)" = "1" ]; then
    log "⛔ SALDO ABAIXO DE US\$ 1,00 — parando ANTES de gastar."; exit 9
  fi
}
# espera o C4 (árvore própria) E qualquer run_matrix em execução (T = congelado não conta)
_ocupada(){
  pgrep -f "p1_cloud.sh qwen deepseek-v4-flash 3 mysql" >/dev/null 2>&1 && return 0
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}
log "###### CONTINUAÇÃO — depois do C4 ######"
log "aguardando o C4 terminar (árvore própria)"
while _ocupada; do sleep 300; done
log "máquina livre — assumindo"

_check_saldo
log "1/3 ⭐ I-MY · IMDb · MySQL — aided-cloud, 13 queries x3 (4º QUADRANTE)"
BENCH=imdb bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   I-MY exit=$?  · saldo: US\$ $(_saldo)"

_check_saldo
log "2/3 P1-1b — mistral acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "   P1-1b exit=$?"

_check_saldo
log "3/3 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$?"
log "###### CONTINUAÇÃO CONCLUÍDA — máquina livre para o LOCAL ######"
