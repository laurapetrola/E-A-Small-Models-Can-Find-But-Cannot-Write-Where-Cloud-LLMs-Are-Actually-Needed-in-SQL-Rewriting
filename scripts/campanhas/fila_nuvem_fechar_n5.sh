#!/usr/bin/env bash
# FILA DA NUVEM — FECHAR AS MANCHETES A n=5 ANTES DA COBERTURA (06/09 22:50).
#
#   POR QUE ISTO VEM NA FRENTE. Auditoria de 06/09 mostrou que **C1 e C2 NÃO estavam fechadas**:
#   cada uma tem UMA query com 4 runs em vez de 5.
#     · C1 → `q11` (4/5, +9 timeouts de baseline)
#     · C2 → `q73` (4/5, +1 timeout)
#   ⛔ Eu vinha reportando as duas como "fechadas". Elas são as células do par que isola a
#      CAPACIDADE (alcance 13 → 17) — o achado central do eixo (ii) — e a régua de CONSISTÊNCIA
#      (>=3 lands em 5) só existe a n=5. Com 4 runs a régua não se aplica ÀQUELA query, que é
#      exatamente onde ela seria lida.
#
#   ⭐ CUSTO x VALOR: são **2 runs**, ~US$ 0,12. A cobertura (P1-1b/c) são 126 runs, ~US$ 8. Fechar
#      duas manchetes por 12 centavos vem antes de cobertura por 8 dólares — é a regra da usuária
#      ("o que fica de fora é COBERTURA, nunca MANCHETE") aplicada ao caso mais barato possível.
#
#   ⏰ Pode subir a qualquer hora: o `offpeak.sh` segura o GASTO até a janela barata.
#   ⚠️ PENDÊNCIAS LOCAIS (não entram aqui, não custam dinheiro): `L0b` q81 (1 run) e
#      `SL1` q85+q96 (4 runs). Ver `scripts/campanhas/fechar_pendencias_local.sh`.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_fechar_n5.log
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
    log "⛔ SALDO ABAIXO DE US\$ 1,00 — parando ANTES de gastar."; exit 9
  fi
}
_ocupada(){
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}

log "###### FECHAR C1 e C2 a n=5, depois a cobertura ######"
log "aguardando a máquina (o offpeak segura o gasto até a janela barata)"
while _ocupada; do sleep 300; done

_check_saldo
log "1/4 ⭐ C1 · TPC-DS/PG — fechar a q11 (1 run, para chegar a 5)"
ONLY="q11" bash "$O" bash "$P" qwen deepseek-v4-flash 5
log "   C1 exit=$? · saldo: US\$ $(_saldo)"

_check_saldo
log "2/4 ⭐ C2 · teto (flash acha) — fechar a q73 (1 run, para chegar a 5)"
ONLY="q73" bash "$O" bash "$P" cloud deepseek-v4-flash 5
log "   C2 exit=$? · saldo: US\$ $(_saldo)"

_check_saldo
log "3/4 P1-1b — mistral acha · flash escreve (COBERTURA · 54 runs)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
_RC=$?
log "   P1-1b exit=$_RC · saldo: US\$ $(_saldo)"
if [ "$_RC" -ne 0 ]; then
  log "###### ⛔ P1-1b FALHOU (rc=$_RC) — PARANDO para investigar ######"; exit "$_RC"
fi

_check_saldo
log "4/4 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA · 63 runs)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$? · saldo: US\$ $(_saldo)"
log "###### CONCLUÍDA ######"
