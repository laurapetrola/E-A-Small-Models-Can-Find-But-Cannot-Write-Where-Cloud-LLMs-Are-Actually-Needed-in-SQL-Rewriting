#!/usr/bin/env bash
# v5 (16/09/2026) — ENTRAM O I1 E O I2: **IMDb raw no regime do P1**.
#
#   POR QUE. O `next_steps` lista "nenhum IMDb raw no regime do P1" como a **nº 2 das três coisas que
#   mais pesam**, e a auditoria de 16/09 confirmou que continua verdade: os stores `imdb_pg` e
#   `imdb_mysql` são `arm=raw`, mas o modelo gravado é `qwen-reasoning+think+nohw` — **sem `+noplan`**.
#   São plan-ON, FORA do regime do P1.
#
#   ⭐ O motivo NÃO é cobertura — é FILTRO DE GENERALIDADE. Do último yield de descoberta, **2 dos 7
#      candidatos a regra vinham com nome de coluna do TPC-DS colado dentro**. Sem um raw de IMDb no
#      mesmo regime não há como separar "regra geral" de "regra decorada do TPC-DS".
#
#   ⚠️ IMDb NÃO TEM ESCALA REDUZIDA (dataset real, não gerado) ⇒ `VERIFY_DB_URI = DB_URI`. Nenhum
#      land pode cair no fallback SF1: ou a equivalência vale em escala real, ou não vale.
#   ⚠️ Os contêineres de IMDb ficam PARADOS entre campanhas — o `p1_local.sh` dá `docker start`.
#   ⚠️ n=3 e 13 queries JOB (10 curated + 3 heldout) — é o mesmo n das outras células de IMDb
#      (R1 e I-MY), então o painel de IMDb fica casado.
#
#   ORDEM — I1/I2 entram ANTES de W2 e LB, que são cobertura e ablação:
#     1. M2b  · ~12 runs  — TPC-DS/MySQL raw noplan (em curso; o `--resume` retoma)
#     2. I1   ·  39 runs  — IMDb/PostgreSQL raw, regime P1
#     3. I2   ·  39 runs  — IMDb/MySQL raw, regime P1
#     4. W2   · ~38 runs  — writer `qwen3:8b +think` (COBERTURA de writer)
#     5. LB   · 105 runs  — reasoning OFF (ABLAÇÃO da claim iv)
#
#   ✅ JÁ FECHADOS, fora desta cadeia: L4 (104 runs) e L5 (110 runs) — a linha aided-local fechou
#      com 4 modelos em 15/09.
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
# FILA LOCAL v6 — reordenada em 09/09 para fechar o CONTRASTE DE DECOMPOSIÇÃO.
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
LOG=logs/campanhas/fila_local_v6.log
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

log "###### FILA LOCAL v6 — LB NA FRENTE (139 runs, custo zero) ######"

# ⛔⛔ ORDEM INVERTIDA em 17/09: o LB passa NA FRENTE do W2.
#
#   POR QUÊ. O W2 virou a célula mais cara em tentativas de todo o painel: **45 timeouts de baseline
#   para 33 runs** — mais tentativas morrem do que produzem. Em 15 h ele fez 4 runs (0,3/h, seis vezes
#   mais lento que o normal). A 0,3/h os ~30 runs restantes levariam ~4 dias e o LB não começaria
#   antes de 21/09, comendo a margem de escrita.
#
#   ⭐ REGRA DA USUÁRIA APLICADA: "o que fica de fora é COBERTURA, nunca MANCHETE".
#      · O **LB** decide uma CLAIM — sem ele a (iv) sai do paper.
#      · O **W2** é COBERTURA de writer (o terceiro do eixo local).
#   ⇒ O LB vem primeiro. O W2 guarda os 33 runs; o `--resume` retoma se sobrar janela, e se não
#     sobrar ele é reportado com **n reduzido declarado**.
#
#   ✅ STORE DO LB CONFERIDO A SECO (17/09): `p1_qwen_aided_local_schemalink_reaoff` — separado do
#      SL1 (`..._schemalink`). O 7º argumento (REASONING forçado) é aplicado na linha 174 e o
#      `STORE=` da linha 180 reatribui DEPOIS dele. ⚠️ A geometria é a mesma do bug #25; o que salva
#      aqui é existir um segundo `STORE=` após o sufixo.

perna "1/2 LB · aided-local reasoning OFF (105 runs) — o par limpo ON x OFF" \
      env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b "" postgres off

perna "2/2 W2 · writer qwen3:8b +reasoning (~30 runs) — COBERTURA, retoma dos 33" \
      env SL=on bash "$P" bash "$L" qwen 3 off qwen3:8b on

log "###### FILA LOCAL v6 CONCLUÍDA ######"
