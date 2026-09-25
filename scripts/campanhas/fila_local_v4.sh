#!/usr/bin/env bash
# ⛔⛔ v4 (11/09/2026) — O L4 VOLTOU PARA A FILA. Ele NUNCA RODOU.
#
#   O QUE ACONTECEU (bug #22). Na v3 o L4 era a perna 3/7. Ele escreveu o `.env` às 06:13 de 10/09,
#   foi CONGELADO às 07:00 pela troca de janela, e às 11:21 a cadeia de NUVEM sobrescreveu o `.env`
#   com a config do P1-1a. Às 22:00 o L4 descongelou e rodou 4h51 **com a config da nuvem**: os 25
#   registros foram para `suggestions_p1cloud_llama_flash`, o próprio store do L4 ficou com 2 runs,
#   e a perna declarou `exit 0`. Uma noite inteira de janela local virou trabalho da célula errada.
#   ⚠️ Nenhum dado foi contaminado — o store da nuvem seguiu homogêneo, e o P1-1a até GANHOU com
#      isso (subiu de n=3 para n=5, 105 runs). Mas custou US$ 1,44 sem decisão e o L4 não existe.
#
#   ⭐ O CONSERTO DE CAUSA-RAIZ já está no `peak_local.sh`: ceder a janela agora é MATAR, não
#      congelar. Ao voltar, o `perna()` relança, o `p1_local.sh` REESCREVE o `.env` e a guarda de
#      matriz roda de novo. Sem isso, re-enfileirar o L4 só repetiria a falha.
#
#   📌 ORDEM: o L4 vem ANTES do L5 porque fecha MANCHETE (o par L0c x L4 = decomposição na escala
#      local). O L5 é o 4o modelo da linha aided-local — importante, mas cobertura. Regra da
#      usuária: "o que fica de fora é COBERTURA, nunca MANCHETE". O L5 guarda seus 7 runs; o
#      `--resume` retoma de onde parou.
# FILA LOCAL v4 — reordenada em 09/09 para fechar o CONTRASTE DE DECOMPOSIÇÃO.
#
#   ⛔ POR QUE UM ARQUIVO NOVO E NÃO UMA EDIÇÃO. O bash lê o script por OFFSET DE BYTE enquanto
#      executa; editar a cadeia viva corrompe o que ela ainda não leu. Já aconteceu 3x nesta
#      campanha. A `fila_local_pendentes.sh` é abandonada, não editada.
#
#   ⭐ O QUE MUDOU E POR QUÊ. O C2 x C3 (nuvem) mostrou que decompor NÃO aumenta o alcance com o
#      mesmo modelo (17 = 17), redistribui quem entra (união 18, interseção 16) e ainda cobra
#      handoff (`mech_f` 10 x 3). Isso é MANCHETE — e hoje só existe na escala de nuvem.
#      Na escala LOCAL o par ainda não fecha:
#        · monolítico local = L0c (raw qwen) — existe a n=3, alcance 6
#        · decomposto local = L4 (aided-local SEM schema) — NUNCA rodou
#      ⚠️ E o par ÓBVIO (SL1 x L0b) é CONFUNDIDO: o SL1 é `+schemalink` e o raw NÃO é. Comparar
#         os dois mistura DECOMPOSIÇÃO com SCHEMA LINKING. O par limpo é **L0c x L4**, os dois
#         sem schema, mesmo modelo, mesmo n.
#      ⇒ L0c e L4 sobem na ordem. M2b/W2 são COBERTURA e LB é ABLAÇÃO: descem.
#      📌 Regra da usuária aplicada: "o que fica de fora é COBERTURA, nunca MANCHETE".
#
#   ORDEM:
#     1. W1   ·   4 runs  — writer local `deepseek-coder`; falta só q85(+1) e q96(+3). Fecha barato.
#     2. L0c  ·  23 runs  — qwen RAW de n=3 para n=5. ⛔ Sem isto o contraste local não pode ser
#                           lido: alcance a n diferentes não se compara (regra do projeto).
#     3. L4   · 105 runs  — aided-local SEM schema. Fecha DOIS eixos de uma vez:
#                             · L0c x L4  = decomposição (limpo, ambos sem schema)
#                             · SL1 x L4  = schema linking (limpo, ambos decompostos)
#     4. L5   · 105 runs  — aided-local do `deepseek-r1`, SEM schema. Completa a LINHA aided-local:
#                           hoje ela tem llama(n=5), mistral(n=3) e qwen; falta o melhor achador
#                           do painel raw (alcance 7). ⚠️ A n=5, NÃO 63 runs — a linha é n=5 em
#                           llama e qwen, e alcance a n diferente não se compara.
#     5. M2b  ·  15 runs  — TPC-DS/MySQL raw noplan (COBERTURA do 2o engine).
#     6. W2   ·  43 runs  — writer `qwen3:8b +reasoning` (COBERTURA de writer).
#     7. LB   · 105 runs  — reasoning OFF, par limpo ON x OFF (ABLAÇÃO da claim iv).
#
#   ⛔ NÃO ENTRARAM (decisão 09/09, explícita para não parecer esquecimento):
#     · RAW a n=5 para llama/mistral/ds-llama (~126 runs) — resolveria o n misturado da coluna RAW,
#       mas empurraria o LB para depois do prazo confortável. Reavaliar quando o L4 fechar.
#     · linha do `ds-llama` (aided-local + aided-cloud) — alcance 1 no raw; gastar NUVEM no modelo
#       que já sabemos ser o piso é cobertura cara. Declarar a ausência sai mais barato que pagá-la.
#
#   ⚠️ NENHUM custa dinheiro — é tempo de janela local (22:00-01:00 e 03:00-07:00).
#   ⚠️ Cada perna ESPERA a máquina: um servidor, um `.env`.
#   ⛔ Interrupção (rc=4/143) NÃO conta como falha — é o custo da alternância de janelas.
#
#   ⚠️ TETO DE TENTATIVAS no L0c: `q81` está com 2 runs + 8 timeouts = 10 tentativas, e o teto a
#      n=5 é 2x5 = 10. `q30` tem 4 runs + 7 timeouts = 11. As duas provavelmente NÃO chegam a n=5
#      e devem ir para o paper com **n reduzido declarado** — não é falha da fila.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_local_v4.log
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

log "###### FILA LOCAL v4 — L4 RECOLOCADO (373 runs, custo zero) ######"

# ⚠️ SL=on: o W1 é da célula COM schema (store `..._schemalink_dscoder`). Sem isso o run entra em
#    outro regime e contamina — bug #21.
perna "1/5 L4 · aided-local SEM schema (105 runs) — o lado DECOMPOSTO local" \
      bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b

# ⛔ L5: family=deepseek, aided, writer local qwen2.5-coder, SEM `SL=on`. O store deriva para
#    `p1_deepseek_aided_local` (conferido no p1_local.sh) — não colide com o `p1_deepseek_raw`
#    legado. n=5 para casar com llama e qwen na mesma linha.
perna "2/5 L5 · aided-local do deepseek-r1 (105 runs) — fecha a LINHA aided-local" \
      bash "$P" bash "$L" deepseek 5 off qwen2.5-coder:7b



# ⚠️ M2b é `raw` (RAWARM) e usa STORES LEGADOS (`tpcds_mysql`, não o nome derivado). Sem isso ele
#    criaria célula NOVA em vez de continuar. Posições: FAMILY RUNS RULES CODER WREASON ENGINE.
perna "3/5 M2b · TPC-DS/MySQL raw noplan (15 runs)" \
      env RAWARM=raw STORE_NAME=tpcds_mysql INDEX_STORE_NAME=index_tpcds_mysql \
      bash "$P" bash "$L" qwen 3 off "" "" mysql

perna "4/5 W2 · writer qwen3:8b +reasoning (43 runs)" \
      env SL=on bash "$P" bash "$L" qwen 3 off qwen3:8b on

# LB: mesmo modelo, mesmo writer, mesmo schema — muda SÓ o reasoning do FIND. Se não fechar até o
# prazo, a claim (iv) SAI do paper (decisão 08/09).
perna "5/5 LB · aided-local reasoning OFF (105 runs) — par limpo ON x OFF" \
      env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b "" postgres off

log "###### FILA LOCAL v4 CONCLUÍDA ######"
