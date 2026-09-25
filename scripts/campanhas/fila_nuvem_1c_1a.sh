#!/usr/bin/env bash
# FILA DA NUVEM — o que restou da cobertura cross-model (09/09, depois do reboot).
#
#   POR QUE UMA CADEIA NOVA E NÃO A `fila_nuvem_cobertura.sh`. Aquela começa no P1-1b, que já
#   FECHOU (exit 0 em 08/09 17:46). Relançá-la faria o `p1_cloud.sh` rodar o CANÁRIO do P1-1b antes
#   de descobrir que não há o que fazer — e o canário é uma run PAGA (~US$ 0,13). Esta cadeia entra
#   direto no que falta.
#
#   ORDEM:
#     1. P1-1c · deepseek-r1 acha · flash escreve — 10/63 runs. Faltam ~53.
#     2. P1-1a · llama acha · flash escreve      — 22/63 runs. Faltam ~41.
#
#   ⛔ SALDO. Em 09/09 07:35 o saldo é US$ 3,32 e a medição real é **US$ 0,127/run** (P1-1b). Isto dá
#      ~18 runs até o piso de US$ 1,00 — ou seja, esta cadeia VAI PARAR NO MEIO do P1-1c por saldo,
#      não por erro. É esperado e está declarado: `exit 9` = recarregar e relançar.
#      ⭐ Regra da usuária: "se o saldo apertar, o que fica de fora é COBERTURA, nunca MANCHETE" —
#      as duas células aqui SÃO cobertura, então parar no meio delas é o comportamento certo.
#
#   ⏰ O `offpeak.sh` segura o gasto até a janela barata (01:00-03:00 e 07:00-22:00).
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_1c_1a.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}
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

log "###### FILA DA NUVEM — P1-1c e P1-1a (resto da cobertura) ######"
while _ocupada; do sleep 300; done

_check_saldo
log "1/2 P1-1c — deepseek-r1 acha · flash escreve (10/63 · faltam ~53)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
_RC=$?
log "   P1-1c exit=$_RC · saldo: US\$ $(_saldo)"
if [ "$_RC" -ne 0 ]; then
  log "###### ⛔ P1-1c parou (rc=$_RC) — NÃO seguindo para o P1-1a ######"; exit "$_RC"
fi

_check_saldo
log "2/2 P1-1a — llama acha · flash escreve (22/63 · faltam ~41)"
bash "$O" bash "$P" llama deepseek-v4-flash 3
log "   P1-1a exit=$? · saldo: US\$ $(_saldo)"
log "###### COBERTURA DE NUVEM CONCLUÍDA ######"
