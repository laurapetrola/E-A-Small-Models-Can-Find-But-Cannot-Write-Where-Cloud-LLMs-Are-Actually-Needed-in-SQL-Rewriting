#!/usr/bin/env bash
# FILA DA NUVEM — I-MY NA FRENTE (04/09, decisão da usuária).
#
#   POR QUE INVERTER. O C4 (TPC-DS/MySQL) estava na frente por ser MANCHETE. A usuária inverteu com um
#   argumento melhor: o I-MY responde uma pergunta ABERTA, o C4 produz dado cuja forma já conhecemos.
#
#   ⭐ A PERGUNTA ABERTA: o MySQL consegue validar índice por EXECUÇÃO? Hoje a resposta é "no TPC-DS,
#      não" — 0 de 8 candidatos mediram, a `q67` (`rank() over partition`) excede 600s no SF20. Se o
#      IMDb também falhar, o projeto fica SEM nenhum índice medido em MySQL e a discussão do SF1
#      volta. Se medir, o MySQL ganha seu primeiro índice validado por execução e o assunto fecha.
#      ⇒ Descobrir isso ANTES vale mais do que 6h de C4, porque muda o que ainda precisa ser feito.
#   ⭐ E é barato em tempo: as queries JOB no MySQL rodam em 1,4s (`1a`), 12,5s (`29c`), 61,7s (`17f`),
#      contra ~626 s/run do TPC-DS/MySQL. Mais dado por hora e por dólar.
#
#   ⚠️ CONSEQUÊNCIA DE SALDO, declarada: US$ 3,05 e o I-MY sozinho custa ~US$ 2,3-3,5. A guarda
#      provavelmente PARA a fila antes do C4. Isso é a inversão de prioridade assumida — o C4 fica
#      esperando recarga. Nada se perde: `--resume` retoma os 15 runs que ele já tem.
#
#   ORDEM: 1. I-MY (IMDb/MySQL) · 2. C4 (TPC-DS/MySQL) · 3. P1-1b · 4. P1-1c
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_imdb_primeiro.log
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
log "###### FILA DA NUVEM — I-MY NA FRENTE ######"
while _ocupada; do log "aguardando a máquina"; sleep 300; done

_check_saldo
log "1/4 ⭐ I-MY · IMDb · MySQL — 13 queries x3 · RESPONDE: o MySQL mede índice por execução?"
BENCH=imdb bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   I-MY exit=$?  · saldo: US\$ $(_saldo)"
log "   👉 conferir agora: .cache/index_p1cloud_qwen_flash_imdb_mysql  — algum validation=executed?"

_check_saldo
log "2/4 ⭐ C4 · TPC-DS · MySQL — 16 queries x3 (MANCHETE · escada do MySQL)"
ONLY="q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q63 q67 q69 q85 q96" \
  bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   C4 exit=$?  · saldo: US\$ $(_saldo)"

_check_saldo
log "3/4 P1-1b — mistral acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "   P1-1b exit=$?"

_check_saldo
log "4/4 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$?"
log "###### FILA CONCLUÍDA ######"
