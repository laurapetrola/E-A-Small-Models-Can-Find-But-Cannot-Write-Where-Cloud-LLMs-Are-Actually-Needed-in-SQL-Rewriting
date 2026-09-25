#!/usr/bin/env bash
# FILA DA NUVEM — reordenada 03/09 19:55 para pôr o C4 (MANCHETE) na frente da cobertura.
#
#   POR QUÊ. A cadeia anterior (`fila_nuvem.sh`) pulou o C4 às 08:15 — o canário recusou, com razão,
#   por causa do orçamento de 180s herdado do PostgreSQL — e seguiu para o P1-1a. Resultado: a
#   COBERTURA passou na frente da MANCHETE, que é o inverso da regra da usuária:
#       "se o saldo apertar, o que fica de fora é COBERTURA, nunca MANCHETE."
#   Com US$ 4,71 para ~219 runs (~US$ 13-20), a ordem decide o que existe no paper.
#
#   ✅ Os três bloqueios do C4 estão resolvidos (03/09): backend MySQL mede por EXECUÇÃO ·
#      INDEX_MEASURE_TIMEOUT_S=600 · registros `estimated` antigos em quarentena.
#
#   ORDEM:
#     1. C4      — TPC-DS/MySQL aided-cloud, 16q x3 (faltam 41)   ⭐ MANCHETE
#     2. P1-1b   — mistral acha, flash escreve  (faltam 63)        cobertura
#     3. P1-1c   — deepseek-r1 acha, flash escreve (faltam 63)     cobertura
#
#   ⚠️ O P1-1a (llama) NÃO está aqui: ele já está em voo, com 11 runs pagos. Esta cadeia ESPERA ele
#      terminar — mata-lo desperdiçaria o que já foi gasto.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_c4_primeiro.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}
# ⛔ piso de US$ 1,00 — um timeout custa US$ 0,60 (medido 02/09), então piso menor deixaria a conta
#    estourar NO MEIO da célula: o erro 402 grava `mechanics_failed` uniforme e PARECE limite do modelo.
_check_saldo(){
  local s; s=$(_saldo); log "   saldo: US\$ ${s:-?}"
  if [ -n "${s:-}" ] && [ "$(echo "$s < 1.00" | bc -l 2>/dev/null)" = "1" ]; then
    log "⛔ SALDO ABAIXO DE US\$ 1,00 — parando ANTES de gastar. Recarregue e relance."
    exit 9
  fi
}
# ⛔⛔ A ESPERA TEM DUAS CONDIÇÕES (corrigido 03/09 22:50, ANTES de morder).
#
#   O erro que quase aconteceu: esperar só por "run_matrix em execução" não basta. O P1-1a é gerido
#   por um `offpeak.sh` ÓRFÃO (o pai dele foi morto de propósito, para a cobertura não emendar na
#   frente da manchete). Esse supervisor ENCERRA a perna às 22:00 e a RELANÇA à 01:00 — o log dele diz
#   "PEAK started -> pausing (progress is kept; --resume continues)".
#   Entre o congelamento do local à 01:00 e o relance do P1-1a existe uma FRESTA sem nenhum
#   run_matrix vivo. Nessa fresta esta cadeia concluiria "máquina livre" e subiria o C4 — duas
#   células de nuvem ao mesmo tempo, **um servidor, um .env**. As duas escreveriam no store errado.
#
#   ✅ Por isso a espera também observa o supervisor do P1-1a: enquanto ele existir, a perna dele
#      ainda vai voltar, e a máquina NÃO é nossa — mesmo que neste instante nada esteja rodando.
_ocupada(){
  # (a) alguém executando de fato (estado != T; congelado não bloqueia)
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  # (b) o supervisor do P1-1a ainda vivo = a perna dele volta na próxima janela
  pgrep -f "offpeak.sh bash scripts/campanhas/p1_cloud.sh llama" >/dev/null 2>&1 && return 0
  return 1
}

log "###### FILA DA NUVEM — C4 PRIMEIRO (manchete antes da cobertura) ######"
log "aguardando o P1-1a (llama) terminar — os 11 runs dele já foram pagos"
while _ocupada; do sleep 300; done
log "máquina livre — assumindo"

_S=$(_saldo); _check_saldo
log "1/3 ⭐ C4 · TPC-DS · MySQL — aided-cloud, 16 queries x3 (MANCHETE · escada do MySQL)"
log "   saldo ANTES: US\$ ${_S:-?}"
ONLY="q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q63 q67 q69 q85 q96" \
  bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   C4 exit=$?  · saldo DEPOIS: US\$ $(_saldo)"

_check_saldo
# ── I-MY · IMDb + MySQL — O 4º QUADRANTE (enfileirado 04/09 a pedido da usuária) ────────────────
#
#   POR QUÊ AGORA. O P1 nunca rodou IMDb/MySQL. As duas células de agosto (`imdb_final_flash_mysql`
#   e `..._noplan`) são de OUTRO regime — carregam `+schemalink` e `nothink`, e o P1 roda sem schema
#   com reasoning ON. Elas teriam de ser refeitas de qualquer forma.
#
#   ⭐ E há uma razão que só apareceu hoje: **é a única célula MySQL onde o índice PODE ser medido de
#      verdade.** IMDb não tem escala reduzida — é o dado real. Cronometrado em 04/09 no MySQL:
#      `1a` 1,4s · `29c` 12,5s · `17f` 61,7s — tudo dentro do orçamento de 600s. O muro que barrou o
#      C4 é específico do **TPC-DS em SF20**, onde a `q67` passa de 600s. Eu tinha generalizado
#      "MySQL não mede"; o correto é "TPC-DS/MySQL em SF20 não mede".
#      ⇒ Esta célula pode produzir o **1º dado de índice em MySQL validado por execução** do projeto —
#        as 19 células MySQL existentes têm 100% de `validation: estimated`.
#
#   ⚠️ VERIFY_DB_URI = DB_URI aqui (o script já faz): dataset real, sem SF1, então nenhum land cai em
#      escala reduzida e não há a mistura de escalas que inviabiliza a comparação cross-engine.
#   13 queries JOB x3 = 39 runs.
log "2/4 ⭐ I-MY · IMDb · MySQL — aided-cloud (4º QUADRANTE · única MySQL que mede índice)"
BENCH=imdb bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   I-MY exit=$?  · saldo DEPOIS: US\$ $(_saldo)"

_check_saldo
log "3/4 P1-1b — mistral acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "   P1-1b exit=$?"

_check_saldo
log "4/4 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$?"

log "###### FILA CONCLUÍDA — máquina livre para o LOCAL ######"
