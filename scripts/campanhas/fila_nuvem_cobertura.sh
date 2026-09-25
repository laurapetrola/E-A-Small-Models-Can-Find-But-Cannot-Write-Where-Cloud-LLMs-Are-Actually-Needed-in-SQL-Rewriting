#!/usr/bin/env bash
# FILA DA NUVEM — A COBERTURA (06/09 22:20).
#
#   TODAS AS MANCHETES DE NUVEM ESTÃO FECHADAS:
#     C1 (TPC-DS/PG) · C2 (teto) · C3 (monolítico) · C4 (TPC-DS/MySQL) · R1 (IMDb/PG) · I-MY (IMDb/MySQL)
#     ⭐ A matriz `dataset × engine` está COMPLETA — ver o skeleton §4.
#   O que resta é COBERTURA cross-model: dar o escritor de nuvem aos outros modelos do painel.
#
#   ORDEM: 1. P1-1b (mistral acha) · 2. P1-1c (deepseek-r1 acha)
#     ⚠️ O P1-1a (llama) já tem 22 runs de 63 e está FORA desta cadeia de propósito: ele foi
#        interrompido em 04/09 e seria retomado pelo `--resume`, mas priorizar as duas células com
#        ZERO/POUCO dado rende mais cobertura por dólar. Reavaliar quando estas fecharem.
#
#   ⏰ JANELA: esta cadeia pode ser lançada a QUALQUER hora — o `offpeak.sh` a segura até a janela
#      BARATA (01:00-03:00 e 07:00-22:00). Nada é gasto no pico. Pedido da usuária: "religar só no
#      horário seguro".
#
#   ⛔ E ela AGE sobre o código de saída, não só o registra — foi o defeito que deixou o C4
#      incompleto atrás de uma célula de cobertura em 06/09.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_cobertura.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}
# ⛔ piso US$ 1,00 — um timeout custa US$ 0,60 (medido 02/09); piso menor estoura no meio da célula,
#    e o erro 402 grava `mechanics_failed` UNIFORME que PARECE limite do modelo.
_check_saldo(){
  local s; s=$(_saldo); log "   saldo: US\$ ${s:-?}"
  if [ -n "${s:-}" ] && [ "$(echo "$s < 1.00" | bc -l 2>/dev/null)" = "1" ]; then
    log "⛔ SALDO ABAIXO DE US\$ 1,00 — parando ANTES de gastar. Recarregue e relance."; exit 9
  fi
}
_ocupada(){
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}

log "###### FILA DA NUVEM — COBERTURA cross-model ######"
log "aguardando a máquina (o offpeak segura o gasto até a janela barata)"
while _ocupada; do sleep 300; done
log "máquina livre — assumindo"

_check_saldo
log "1/2 P1-1b — mistral acha · flash escreve (COBERTURA · 63 runs)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
_RC=$?
log "   P1-1b exit=$_RC · saldo: US\$ $(_saldo)"
if [ "$_RC" -ne 0 ]; then
  log "###### ⛔ P1-1b FALHOU (rc=$_RC) — PARANDO para investigar ######"
  log "   conferir .cache/matrix_failures.log e os contêineres antes de relançar"
  exit "$_RC"
fi

_check_saldo
log "2/2 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA · 63 runs)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$? · saldo: US\$ $(_saldo)"

log "###### COBERTURA CONCLUÍDA — máquina livre para o LOCAL ######"
