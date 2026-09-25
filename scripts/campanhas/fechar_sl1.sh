#!/usr/bin/env bash
# FECHAR O SL1 — 3 runs (07/09).
#
#   ESTADO: `p1_qwen_aided_local_schemalink` (aided-local qwen COM schema, n=5) tem
#     · `q85` → 4/5  (4 runs + 2 timeouts = 6 tentativas)
#     · `q96` → 3/5  (3 runs + 2 timeouts = 5 tentativas)
#   ⭐ NENHUMA das duas bateu no TETO DE TENTATIVAS (2 x 5 = 10) — ao contrário da `q11` do C1 e da
#     `q81` do L0b, que estouraram e vão para o paper com **n reduzido declarado**. Estas ainda cabem,
#     então fechar é a saída certa: 3 runs a ~276 s ≈ 15 min de janela local, custo ZERO em dinheiro.
#
#   POR QUE O SL1 MERECE ESTE ESFORÇO. Ele é:
#     (a) o DEGRAU aided-local do qwen — o meio da escada `raw 6 → SL1 3 → C1 13`;
#     (b) o lado PostgreSQL do par cross-engine do braço local (SL1 x LA);
#     (c) o ponto "schema ON" do eixo do schema, cujo lado "off" (L4) ainda nem começou.
#   Uma query com n=3 ou n=4 numa célula n=5 quebra a régua de CONSISTÊNCIA (>=3 lands em 5) —
#   justamente onde ela seria lida.
#
#   ⚠️ SL=on é OBRIGATÓRIO: sem isso o run entra sem schema e contamina a célula — foi o bug #21,
#      que já mordeu este store exato (1 run sem `+schemalink` em 115).
#   ⚠️ ESPERA a máquina: um servidor, um `.env`.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fechar_sl1.log
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

log "###### FECHAR O SL1 — q85 e q96 (3 runs para n=5) ######"
log "aguardando a máquina"
while _ocupada; do sleep 300; done
log "máquina livre — assumindo"

ONLY="q85 q96" env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b
log "   SL1 exit=$?"
log "###### FIM — conferir com: python scripts/cell_status.py p1_qwen_aided_local_schemalink ######"
