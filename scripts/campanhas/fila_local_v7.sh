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
# FILA LOCAL v7 — reordenada em 09/09 para fechar o CONTRASTE DE DECOMPOSIÇÃO.
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
LOG=logs/campanhas/fila_local_v7.log
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

log "###### FILA LOCAL v7 — LB + PAINEL CROSS-MODEL NO IMDb (291 runs, custo zero) ######"

# ⭐⭐ v7 (17/09) — ENTRA O PAINEL CROSS-MODEL DO IMDb/PostgreSQL (4 modelos, 156 runs).
#
#   A PERGUNTA QUE ELE RESPONDE: a ordenação entre modelos medida no TPC-DS/PG
#   (`r1` 7 · `qwen` 6 · `llama` 3 · `ds-llama` 1 · `mistral` 1) **se mantém em outro benchmark?**
#   Hoje o painel cross-model existe em UM quadrante só; o IMDb tem apenas o `qwen`.
#
#   ⛔ POR QUE SÓ NO PostgreSQL, e NÃO no MySQL (decisão da usuária, 17/09). Para isolar o
#      **benchmark**, os dois painéis têm de estar no MESMO engine — TPC-DS/PG × IMDb/PG. Rodar
#      cross-model também no MySQL misturaria DOIS eixos na mesma expansão: não se saberia se uma
#      mudança de ordenação veio do benchmark ou do engine.
#
#   ✅ O DESENHO QUE ISSO PRODUZ — e é a frase que vai para o paper:
#      · **ordenação entre modelos** medida em DOIS benchmarks (TPC-DS e IMDb, ambos PostgreSQL);
#      · **transferência de ENGINE** sondada com UM modelo (`qwen`) nos QUATRO quadrantes.
#      ⚠️ Limitação a declarar: não medimos se a ordenação se mantém no MySQL. Não afeta as três
#         contribuições, que são sobre PAPÉIS e NÍVEL DE ESCRITOR, não sobre ranking de modelo.
#
#   ⚠️ E o braço **aided-cloud** no IMDb continua só com o `qwen` — completá-lo custaria NUVEM.
#      ⇒ A *lei do escritor* (C2) segue medida cross-model só no TPC-DS. Declarar junto.
#
#   ✅ STORES CONFERIDOS A SECO (17/09): `p1_<familia>_aided_local_imdb_raw` para cada modelo.
#      Nenhum colide com store existente.
#   ⚠️ IMDb: 13 queries JOB, n=3 · `VERIFY_DB_URI = DB_URI` (dataset real, sem escala reduzida).

perna "1/6 LB · aided-local reasoning OFF (105 runs) — o par limpo ON x OFF" \
      env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b "" postgres off

perna "2/6 I3 · IMDb/PG raw · deepseek-r1 (39 runs)" \
      env BENCH=imdb RAWARM=raw bash "$P" bash "$L" deepseek 3

perna "3/6 I4 · IMDb/PG raw · llama (39 runs)" \
      env BENCH=imdb RAWARM=raw bash "$P" bash "$L" llama 3

perna "4/6 I5 · IMDb/PG raw · mistral (39 runs)" \
      env BENCH=imdb RAWARM=raw bash "$P" bash "$L" mistral 3

perna "5/6 I6 · IMDb/PG raw · deepseek-llama (39 runs)" \
      env BENCH=imdb RAWARM=raw bash "$P" bash "$L" deepseek-llama 3

perna "6/6 W2 · writer qwen3:8b +reasoning (~30 runs) — COBERTURA, retoma dos 36" \
      env SL=on bash "$P" bash "$L" qwen 3 off qwen3:8b on

log "###### FILA LOCAL v7 CONCLUÍDA ######"
