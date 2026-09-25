#!/usr/bin/env bash
# CADEIA DA NUVEM — 02/09. Escrita porque as duas filas (pico e nuvem) foram PARADAS às 08:15 para
# ceder a máquina, e sem elas nada segue sozinho depois que a célula em voo fecha.
#
#   ORDEM E PORQUÊ:
#     1. C3 2ª passada (q5 q50 q67, n=5) — JÁ EM VOO desde 08:20, esta cadeia só ESPERA por ela.
#        Fecha o corpus do C3 nas 21 e desbloqueia o número da DECOMPOSIÇÃO (C2xC3).
#     2. CP1 — sonda `chat` x `flash`, 15 runs que decidem se 95 runs já medidos podem ser
#        reaproveitados. Vale mais que qualquer célula nova: 15 runs decidindo sobre 95.
#
#   ⛔ A C4 (MySQL) NÃO entra. Ela não falhou por acaso: o canário recusou com
#      "index validation is 'estimated', expected 'executed'" — a validação de índice do MySQL está
#      caindo para ESTIMATIVA DE CUSTO. Rodá-la hoje produziria a coluna aided-cloud do MySQL
#      apoiada em custo simulado, que é exatamente o que o projeto se recusa a publicar. É conserto
#      de código, não de campanha.
#
#   ⛔ NADA LOCAL aqui. Decisão da usuária (02/09): o local só volta depois das 10h E só quando a
#      nuvem tiver acabado — a janela barata da nuvem é finita, a do local não.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/cadeia_nuvem_02set.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

log "###### CADEIA DA NUVEM 02/09 ######"

# 1 — esperar a C3 em voo. Não relança: se já morreu, segue direto para a CP1.
if pgrep -f "control_[m]onolithic.sh" >/dev/null; then
  log "1. C3 (2ª passada) em voo — aguardando"
  while pgrep -f "control_[m]onolithic.sh" >/dev/null; do sleep 60; done
fi
log "   C3 encerrada — conferindo o corpus"
.venv/bin/python scripts/cell_status.py control_monolithic_flash_p1 2>&1 | head -4 | tee -a "$LOG"

# 2 — CP1
log "2. ⭐ CP1 — sonda chat x flash: 5 queries do IMDb/PG, muda SÓ o writer"
BENCH=imdb ONLY="32a 7b 10a 1a 17f" bash "$O" bash "$P" qwen deepseek-v4-flash 3
log "   CP1 exit=$?"

log "###### CADEIA DA NUVEM CONCLUÍDA — a máquina está livre para o LOCAL ######"
log "   próximo local: L8 (15 queries faltando) e depois W1 (6 queries da passada n=1)."
