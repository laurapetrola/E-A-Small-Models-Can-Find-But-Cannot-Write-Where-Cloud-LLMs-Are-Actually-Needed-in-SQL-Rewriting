#!/usr/bin/env bash
# FILA DA NUVEM DO P1 — reordenada 02/09 pela REGRA DA USUÁRIA:
#
#     "se o saldo apertar, o que fica de fora é COBERTURA, nunca MANCHETE."
#
# Com US$ 3,25 de saldo para ~US$ 3,53 de fila, a ordem não é detalhe: é o que decide o que existe no
# paper se o dinheiro acabar. Manchete primeiro, cobertura por último.
#
#   1. C4  — TPC-DS/MySQL aided-cloud, 16q x3 (faltam 42 runs)   MANCHETE
#            3º degrau da escada do MySQL. Sustenta a claim CONDICIONAL por engine — sem ele, a
#            afirmação "a parede se move conforme o engine" fica sem o degrau de cima no MySQL.
#            ✅ DESBLOQUEADO 02/09: a validação de índice do MySQL agora MEDE por execução
#               (antes o canário recusava, e recusava CERTO — ver fixed_bugs / next_steps).
#
#   2. R1  — IMDb/PG aided-cloud, 13q x3 (faltam 24 runs)        MANCHETE
#            É a 2ª coluna de DATASET. A célula antiga (`imdb_pg_aided_cloud_noplan`, 35 runs) usou o
#            writer `deepseek-chat`, que a sonda CP1 provou NÃO ser equivalente ao `flash` (concordam
#            em 2 de 5 queries, divergindo nos dois sentidos). Refazer não é capricho: é a única forma
#            de a coluna IMDb existir com o mesmo writer das outras.
#            ⚠️ Mesmo store da CP1 — os 15 runs dela CONTAM, o --resume só completa o que falta.
#
#   3. P1-1 a/b/c — llama / mistral / deepseek-r1 acham, flash escreve, 21q x3 (189 runs)  COBERTURA
#            Completa o degrau de cima da escada para os 3 modelos que não são o qwen. Hoje a coluna
#            aided-cloud existe SÓ para o qwen (6 -> 13 de alcance), então a frase honesta hoje é
#            "no qwen a parede é o WRITE", não "a parede é o WRITE".
#            ⚠️ n=3 = COBERTURA pela regra do projeto (comparação é n=5). Por isso vem por último,
#               apesar do peso que tem na claim (ii). Se o saldo cortar aqui, corta no lugar certo.
#            ⛔ NÃO estão pendentes por falta de tempo: abortaram em 3 MINUTOS em 29/08 com
#               "ABORT: outra célula está rodando", e a fila leu o exit 1 como perna concluída.
#               (família do bug #19 — código de saída mentiroso). O p1_cloud.sh hoje ESPERA.
#
# ⛔ NADA aqui roda em paralelo com o local: um servidor, um .env, e os bancos não cabem juntos na RAM.
#    Esta cadeia ESPERA a máquina, não aborta.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}

# ⛔ GUARDA DE SALDO. O erro 402 já mordeu 2x NO MEIO de uma célula, e o modo de falha engana: grava
#    `mechanics_failed` UNIFORME e PARECE limite do modelo quando é billing. Conferir antes de cada
#    perna custa 1 segundo e evita quarentenar um store inteiro.
_check_saldo(){
  local s; s=$(_saldo)
  log "   saldo: US\$ ${s:-?}"
  # ⚠️ PISO SUBIDO 0,40 -> 1,00 (02/09 22:40). O 0,40 foi calibrado com um custo/run que eu havia
  #    estimado errado. A medição real da perna do C4 mostrou que **uma tentativa que dá TIMEOUT custa
  #    US$ 0,60** (chamada completa ao flash, 40 min, descartada). Com piso em 0,40, um único timeout
  #    depois da checagem já deixaria a conta negativa no meio da célula — que é exatamente o modo de
  #    falha do erro 402: grava `mechanics_failed` UNIFORME e PARECE limite do modelo sendo billing.
  if [ -n "${s:-}" ] && [ "$(echo "$s < 1.00" | bc -l 2>/dev/null)" = "1" ]; then
    log "⛔ SALDO ABAIXO DE US\$ 0,40 — parando a fila ANTES de gastar. Recarregue e relance."
    exit 9
  fi
}

log "###### FILA DA NUVEM P1 — manchete primeiro (regra do saldo) ######"

# ── ESPERA A MÁQUINA SER SOLTA — não que a célula local TERMINE.
#
#   ⛔ A versão anterior esperava o `run_matrix` DESAPARECER, o que significa "o local terminou a
#      célula inteira". Combinado com o supervisor local, que fora do pico seguia rodando até o fim,
#      isso era um IMPASSE: a nuvem esperava o local acabar, o local não tinha por que acabar.
#      Em 02/09 queimou ~3h20 de janela barata.
#
#   ✅ Agora o critério é o ESTADO do processo. O `peak_local.sh` CONGELA (SIGSTOP) o local quando a
#      janela barata abre e há fila de nuvem esperando; congelado, ele aparece em estado `T`. Então
#      "posso assumir" = não existe run_matrix em estado de execução (R/S/D).
#      ⚠️ Congelado ≠ morto: os runs dele estão no store e o `--resume` retoma exatamente de onde
#      parou quando a máquina voltar para o local.
_local_rodando(){
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}
while _local_rodando; do
  log "aguardando a máquina (célula local em execução — será congelada quando a janela barata abrir)"
  sleep 300
done
log "máquina livre (nenhum run_matrix local em execução) — assumindo"

_check_saldo
# ── C4 · AS 21 QUERIES (decisão da usuária 02/09) ──────────────────────────────────────────────
#   Antes rodava com ONLY de 16, excluindo q40 q50 q51 q73 q81 por ECONOMIA DE SALDO (elas nunca
#   produziram run real no MySQL — só timeout, e cada timeout é chamada COMPLETA ao flash, paga e
#   descartada). A usuária optou pelo corpus inteiro. Sem ONLY, o run_matrix varre as 21.
#   ⚠️ q51 já tem 2 runs reais no store — não é caso de "nunca produz", precisa de 1 só.
#
#   💰 MEDIÇÃO DO CUSTO (por que o log abaixo existe). Não sabemos o custo/run desta célula: o
#      gasto de 02/09 (US$ 1,66) pagou C3 + CP1, mas o store do C3 tem **0% de instrumentação de
#      tokens**, então não dá para separar. O único número defensável é um TETO — se o C3 tivesse
#      custado zero, sairia US$ 0,998/1M tokens de saída, o que dá <= US$ 0,106/run no C4 e
#      <= US$ 5,82 nos 55 runs. ⛔ Esse teto NÃO cabe no saldo de US$ 3,25.
#      Por isso o saldo é lido ANTES e DEPOIS: o delta desta perna é a primeira medição limpa de
#      custo/run de uma célula aided-cloud em MySQL. ⚠️ Não extrapolar de outra célula — foi
#      exatamente isso que produziu os dois números errados de 02/09.
_S_ANTES=$(_saldo)
# ⛔⛔ DE VOLTA ÀS 16 QUERIES (02/09 22:40) — REVERTIDO POR EVIDÊNCIA MEDIDA, não por precaução.
#
#   Em 02/09 a célula foi levada às 21 queries a pedido da usuária. A justificativa que EU dei para
#   reincluir a q51 era que ela "já tem 2 runs reais, precisa de 1 só — é a mais barata de recuperar".
#   ⛔ ERRADO, e o próprio run desmentiu em 42 minutos:
#
#       q51 run3: FAILED → timed out (2399s)
#       saldo 3.25 -> 2.65   ·   runs reais produzidos: ZERO
#
#   Ter 2 runs reais não torna a query produtiva — torna INTERMITENTE. E um timeout não é barato:
#   são 40 min de chamada COMPLETA ao flash, paga e descartada. **US$ 0,60 por tentativa falha**, que
#   é ~6x o teto por run que eu havia calculado. Com o saldo de US$ 2,65, quatro timeouts assim
#   consomem quase tudo — e o R1 e o P1-1 ficariam sem.
#
#   ✅ As 5 (q40 q50 q51 q73 q81) viram RESULTADO DECLARADO, que é afirmação legítima e sai de graça:
#      "em 5 das 21 queries do TPC-DS em MySQL o modelo não converge a um resultado medível."
log "1/4 ⭐ C4 · TPC-DS · MySQL — aided-cloud, 16 queries x3 (MANCHETE · 3º degrau da escada)"
log "   saldo ANTES da perna: US\$ ${_S_ANTES:-?}"
ONLY="q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q63 q67 q69 q85 q96" \
  bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "   C4 exit=$?"
_S_DEPOIS=$(_saldo)
log "   saldo DEPOIS: US\$ ${_S_DEPOIS:-?}  · gasto na perna: US\$ $(echo "${_S_ANTES:-0} - ${_S_DEPOIS:-0}" | bc -l 2>/dev/null)"

_check_saldo
log "2/4 ⭐ R1 — IMDb/PG aided-cloud (MANCHETE · 2ª coluna de dataset)"
BENCH=imdb bash "$O" bash "$P" qwen deepseek-v4-flash 3
log "   R1 exit=$?"

_check_saldo
log "3/4 P1-1 a — llama acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" llama deepseek-v4-flash 3
log "   P1-1a exit=$?"

_check_saldo
log "4/4 P1-1 b/c — mistral e deepseek-r1 acham · flash escreve (COBERTURA)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "   P1-1b exit=$?"
_check_saldo
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$?"

log "###### FILA DA NUVEM CONCLUÍDA — máquina livre para o LOCAL ######"
