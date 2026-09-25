#!/usr/bin/env bash
# FECHAR AS PENDÊNCIAS LOCAIS — 5 runs que faltam para duas células ficarem corretas (06/09).
#
#   O QUE ESTÁ ABERTO (auditado 06/09 22:45, query a query nos stores):
#     · L0b · `p1_qwen_aided_local_raw` (raw qwen, n=3) → `q81` tem **2/3** (+8 timeouts de baseline)
#     · SL1 · `p1_qwen_aided_local_schemalink` (aided-local, n=5) → `q85` **3/5** e `q96` **3/5**
#
#   ⛔ POR QUE ISTO IMPORTA E NÃO É DETALHE. Eu vinha reportando as duas como FECHADAS. A régua de
#      CONSISTÊNCIA do projeto (">=3 lands em 5") só existe a n=5 — numa query com 3 ou 4 runs ela
#      simplesmente NÃO SE APLICA, e é justamente ali que alguém iria lê-la. O mesmo vale para o n=3
#      do L0b: com 2 runs, "alcance" naquela query é uma amostra menor que as demais.
#      ⚠️ É a família dos bugs #19-#21 outra vez: **o total parece saudável** (82 runs / 21 queries,
#      101 runs / 21 queries) e a falta está escondida DENTRO de uma query.
#
#   ⭐ CUSTO ZERO EM DINHEIRO — são runs locais. O custo é janela, ~5 x 276 s ≈ 25 min.
#
#   ⚠️ NÃO RODAR JUNTO COM OUTRA CÉLULA LOCAL: um servidor, um `.env`. Este script ESPERA a máquina.
#   ⚠️ O SL1 é n=5 e o L0b é n=3 — os `--runs` abaixo respeitam isso; o `--resume` só completa o que
#      falta e não repete o que já existe.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fechar_pendencias_local.log
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

log "###### FECHAR PENDÊNCIAS LOCAIS (5 runs) ######"
log "aguardando a máquina"
while _ocupada; do sleep 300; done
log "máquina livre — assumindo"

# ⚠️ SL=on: o SL1 é a célula COM schema. Sem isto o run entraria com outro regime e contaminaria a
#    célula — exatamente o bug #21, que já mordeu este store (1 run sem `+schemalink` em 115).
log "1/2 SL1 · aided-local COM schema — fechar q85 e q96 (4 runs, n=5)"
ONLY="q85 q96" env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b
log "   SL1 exit=$?"

log "2/2 L0b · raw qwen — fechar q81 (1 run, n=3)"
ONLY="q81" env RAWARM=raw bash "$P" bash "$L" qwen 3
log "   L0b exit=$?"

log "###### PENDÊNCIAS LOCAIS CONCLUÍDAS ######"
