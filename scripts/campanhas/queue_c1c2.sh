#!/usr/bin/env bash
# C1 + C2 — o par que responde a MANCHETE do P1. Relançados 22/08 depois de o canário os ter barrado
# por um falso positivo: ele exigia registro de índice, mas o eixo de índice é de ESCALADA e a run do
# canário LANDOU — ausência de índice ali é o sistema funcionando, não configuração errada.
#   C1: qwen 8B LOCAL acha → flash escreve   (2 agentes)
#   C2: flash acha          → flash escreve   (2 agentes)  ← o TETO
# Mesmo writer, mesma janela, mesmas 21 queries, n=5. A ÚNICA variável é QUEM ACHA.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/queue_c1c2.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

while ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -qv "^T"; do sleep 120; done
log "###### C1 + C2 ######"
log "⭐ C1 — qwen 8B local ACHA · flash ESCREVE"
bash "$O" bash "$P" qwen deepseek-v4-flash 5
C1_RC=$?
log "  C1 exit=$C1_RC"
# ⭐ REASONING NOS DOIS DEGRAUS — verificado por chamada à API em 25/08, NÃO por suposição.
#
#   A pergunta do C2 é: "se quem ACHA for melhor, a parede sai?". Para a resposta significar isso,
#   a única coisa que pode mudar entre C1 e C2 é QUEM acha. Se um lado raciocinasse e o outro não,
#   um empate teria duas leituras — "a parede não é o FIND" ou "o teto estava capado" — e não
#   daria para escolher entre elas.
#
#   | degrau | quem acha        | raciocina? | como                      |
#   |--------|------------------|------------|---------------------------|
#   | C1     | qwen 8B local    | SIM        | toggle `+think` (ollama)  |
#   | C2     | deepseek-v4-flash| SIM        | NATIVO (default do modelo)|
#
#   ⚠️ `REASONING=off` aparece no ramo `cloud` do p1_cloud.sh e é INERTE: `_find_llm` (architect.py)
#      só passa o toggle no ramo local. Não adianta mexer nessa variável.
#   ⚠️ E "flash" não quer dizer "sem raciocínio": medido, ele devolve `reasoning_content` e
#      `reasoning_tokens > 0`. ⛔ NÃO tentar "consertar" o reasoning do C2 — ele está correto.
log "⭐⭐ C2 — TETO: flash ACHA e flash ESCREVE (ambos raciocinam — ver bloco acima)"
bash "$O" bash "$P" cloud deepseek-v4-flash 5
C2_RC=$?
log "  C2 exit=$C2_RC"

# ⚠️ O MARCADOR AGORA DEPENDE DO EXIT CODE (corrigido 26/08).
#
#   Como era antes: `touch` incondicional. Em 22/08 o C2 foi ABORTADO pelo canário
#   (`index validation is 'absent', expected 'executed'` → exit=1) e mesmo assim a cadeia registrou
#   "C1+C2 COMPLETOS" e criou o marcador. A `queue_p1_cloud_rest.sh` leu o marcador, imprimiu
#   "a fila do veredito CONCLUIU — assumindo" e seguiu para o C3 — com o TETO nunca medido.
#   O C2 sumiu do radar por três dias, e só apareceu porque a usuária perguntou "e o C2 seria o quê?".
#
#   Uma célula abortada NÃO é uma célula concluída. Sem o marcador, a fila de baixo espera —
#   que é o comportamento correto: melhor parada e visível do que adiantada e errada.
if [ "$C1_RC" -eq 0 ] && [ "$C2_RC" -eq 0 ]; then
  log "###### C1+C2 COMPLETOS (exit 0 nos dois) ######"
  touch .cache/.queue_c1c2.DONE
else
  log "⛔ C1+C2 NÃO concluíram (C1=$C1_RC · C2=$C2_RC) — marcador NÃO criado."
  log "⛔ A fila de nuvem seguinte permanece ESPERANDO, de propósito. Investigar antes de seguir:"
  log "     tail logs/campanhas/p1cloud_qwen_flash.log  ·  tail logs/campanhas/p1cloud_cloud_flash.log"
  exit 1
fi
