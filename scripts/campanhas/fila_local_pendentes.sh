#!/usr/bin/env bash
# FILA LOCAL — TUDO QUE NÃO TERMINOU, na ordem de valor por hora (08/09).
#
#   POR QUE ESTA CADEIA. A `queue_p1_peak.sh` é uma passada única e já atravessou vários itens;
#   além disso ela ABANDONOU o W2 por ler 3 mortes de descongelamento como falhas (corrigido hoje no
#   `run_item`, mas a passada já tinha seguido). Esta cadeia junta o que sobrou, com ordem explícita.
#
#   ORDEM — do mais barato/decisivo para o mais caro:
#     1. SL1   ·   2 runs  — fecha o DEGRAU aided-local do qwen a n=5. É peça de DUAS comparações:
#                            a escada (raw 6 → SL1 → C1 13) e o eixo do schema (SL1 × L4).
#     2. W1    ·  14 runs  — writer local `deepseek-coder`; fecha o par de writers com o SL1.
#     3. M2b   ·  15 runs  — TPC-DS/MySQL raw `noplan`; completa o piso no 2º engine.
#     4. W2    ·  43 runs  — writer `qwen3:8b +reasoning`. ⚠️ Foi ABANDONADO por engano; tem 25 runs
#                            e o `mech_f` mais BAIXO dos três writers locais (50%) — pode mudar a
#                            conclusão sobre qual writer local é o melhor.
#     5. L4    · 105 runs  — o lado SEM schema. ⛔ Hoje o eixo do schema tem UM LADO SÓ.
#
#   ⚠️ NENHUM custa dinheiro — é tempo de janela local (~14 h no total).
#   ⚠️ Cada perna ESPERA a máquina: um servidor, um `.env`.
#   ⛔ Interrupção (rc=4/143) NÃO conta como falha — é o custo conhecido da alternância de janelas
#      (o SIGSTOP para o processo, não o relógio do socket). A perna é retomada.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_local_pendentes.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
P=scripts/campanhas/peak_local.sh
L=scripts/campanhas/p1_local.sh

_ocupada(){
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}

# roda uma perna; interrupção não desiste, falha real desiste depois de 3
perna(){
  local nome="$1"; shift
  local try=1 pausas=0 rc
  while [ "$try" -le 3 ]; do
    while _ocupada; do sleep 300; done
    log "$nome (tentativa $try/3)"
    "$@" && { log "   ✅ $nome ok"; return 0; }
    rc=$?
    if [ "$rc" -eq 4 ] || [ "$rc" -eq 143 ]; then
      pausas=$((pausas+1))
      [ "$pausas" -gt 20 ] && { log "   ⛔ $nome interrompida 20x — desistindo"; return 1; }
      log "   ⏸️ $nome INTERROMPIDA (rc=$rc) — alternância de janela; tentativa não consumida ($pausas/20)"
      sleep 60; continue
    fi
    log "   ⚠️ $nome falhou (rc=$rc) — tentativa $try consumida"
    try=$((try+1)); sleep 300
  done
  log "   ⛔ $nome desistindo após 3 falhas reais"
  return 1
}

log "###### FILA LOCAL — PENDENTES (179 runs, custo zero) ######"

# ⚠️ SL=on nos que são da célula COM schema — sem isso o run entra em outro regime e contamina
#    (bug #21, que já mordeu o store do SL1).
perna "1/6 SL1 · fechar q85+q96 (2 runs, n=5)" \
      env ONLY="q85 q96" SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b

perna "2/6 W1 · writer deepseek-coder (14 runs)" \
      env SL=on bash "$P" bash "$L" qwen 3 off deepseek-coder:6.7b

# ⚠️ ASSINATURA DO M2b copiada da fila original — ele é `raw` (RAWARM) e usa STORES LEGADOS
#    (`tpcds_mysql`, não o nome derivado). Sem isso ele criaria uma célula NOVA em vez de continuar.
#    Posições: FAMILY RUNS RULES CODER WREASON ENGINE — daí os dois "" antes de `mysql`.
perna "3/6 M2b · TPC-DS/MySQL raw noplan (15 runs)" \
      env RAWARM=raw STORE_NAME=tpcds_mysql INDEX_STORE_NAME=index_tpcds_mysql \
      bash "$P" bash "$L" qwen 3 off "" "" mysql

perna "4/6 W2 · writer qwen3:8b +reasoning (43 runs) — REABILITADO" \
      env SL=on bash "$P" bash "$L" qwen 3 off qwen3:8b on

perna "5/6 L4 · aided-local SEM schema (105 runs) — o lado que falta do eixo do schema" \
      bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b

# ── LB · o par limpo de REASONING (ON × OFF) — RECOLOCADO 08/09
#
#   ⛔ POR QUE ELE VOLTOU. A contribuição (iv) do skeleton diz "reasoning ON/OFF ≈ não-driver" — e
#      essa afirmação **NÃO TEM DADO no regime do P1**. Auditado em 08/09: os únicos stores `nothink`
#      são de IMDb em OUTRO regime (`+schemalink`, outra era). O par limpo nunca rodou, e eu o havia
#      deixado de fora desta cadeia — o que deixaria a claim sem sustentação nenhuma.
#
#   ⚠️ O sinal que EXISTE hoje é confundido com modelo: no painel raw, reasoning-ON dá alcance 6
#      (qwen), 7 (deepseek-r1) e 1 (ds-llama); OFF dá 1 (mistral) e 3 (llama). Não dá para separar
#      raciocínio de modelo. Só o par LB × SL1 isola — mesmo modelo, mesmo writer, mesmo schema,
#      muda SÓ o reasoning do FIND.
#
#   📌 DECISÃO 08/09: a (iv) deixa de ser CONTRIBUIÇÃO e passa a ser ABLAÇÃO declarada. Contribuição
#      é o que se entrega de novo; "ligar o raciocínio não muda o resultado" delimita escopo.
#      ⇒ Se o LB fechar, vira ablação medida. Se não fechar até o prazo, a (iv) SAI do paper.
#   ⏱️ 105 runs · ~8 h de janela local · custo ZERO em dinheiro.
perna "6/6 LB · aided-local reasoning OFF (105 runs) — o par limpo ON × OFF" \
      env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b "" postgres off

log "###### FILA LOCAL CONCLUÍDA ######"
