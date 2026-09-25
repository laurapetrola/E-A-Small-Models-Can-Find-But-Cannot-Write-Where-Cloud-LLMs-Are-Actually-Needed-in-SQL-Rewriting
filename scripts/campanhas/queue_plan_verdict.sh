#!/usr/bin/env bash
# O VEREDITO DO PLANO — o par IMDb/MySQL na composição nova, e só depois o resto.
#
# POR QUE ESTE PAR PASSOU NA FRENTE (19/08):
#   o plano é a contribuição nº2 do P2, e hoje as três medições disponíveis se contradizem:
#     · PG q69 (composição NOVA, n=5): 51,2% SEM plano × 11,2% COM  -> plano NOCIVO
#     · PG q28 (composição NOVA, n=5): 47,7% × 47,3%                -> empate
#     · IMDb/MySQL (composição ANTIGA, 07/08): alcance 9×7 a favor do plano, mas CONSISTÊNCIA 2×5
#       CONTRA, e não-equivalências 2×4 a favor -> depende da régua
#   O par abaixo é a ÚNICA medição que decide, porque é onde o plano já rendeu de verdade e é o único
#   lugar ainda não medido na composição que vamos entregar. ~US$ 4 protegendo US$ 19,38 (B+B2 + a
#   célula MySQL do TPC-DS existem só para medir esse efeito).
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/queue_plan_verdict.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
I=scripts/campanhas/imdb_battery.sh

# n=5, NÃO 3 (19/08). Orçado a n=3 quando era "cobertura"; virou a medição que DECIDE o plano — e a
# regra do n manda n=5 onde a manchete é COMPARAÇÃO DE CONTAGEM entre braços. O n importa muito aqui:
# com n=3 um land 1/3 pesa igual a um 3/3, e foi essa régua que produziu o falso "+2 de alcance" do
# plano no MySQL antigo (pela consistência o plano perdia de 2 a 5). 130 runs · US$ 5,92 (flash).
log "###### VEREDITO DO PLANO — par IMDb/MySQL, composição nova, n=5 ######"
while pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" >/dev/null; do sleep 60; done

log "perna 1/2: IMDb MySQL, plano ON"
bash "$O" bash "$I" deepseek-v4-flash mysql on 5
log "  exit=$?"
# SALVAGUARDA DO WRITER (19/08): o `flash` NUNCA rodou em MySQL — 263 runs, todas em PostgreSQL.
# Referência de falha mecânica: flash/PG 6% · pro/MySQL 11% · chat/MySQL 27%. Se o flash tropeçar no
# dialeto, a célula enche de `mechanics_failed` e o efeito do PLANO fica soterrado em ruído — aí não
# mediríamos o plano, mediríamos o writer. Este portão para ANTES de gastar a segunda perna.
MF=$(.venv/bin/python - <<'PY'
import glob, json
from collections import Counter
c=Counter()
for f in glob.glob('.cache/suggestions_imdb_final_flash_mysql/*.json'):
    try: r=json.load(open(f))
    except Exception: continue
    if r.get('outcome'): c[r['outcome']]+=1
n=sum(c.values()); mf=c.get('mechanics_failed',0)
print(f"{mf*100//n if n else 0} {n} {mf}")
PY
)
set -- $MF
log "  perna ON: ${3:-0} falhas mecânicas em ${2:-0} runs (${1:-0}%) · referência pro/MySQL = 11%"
if [ "${1:-0}" -gt 25 ]; then
  log "  ⛔ PARANDO: falha mecânica ${1}% muito acima da referência (11%). O flash não escreve MySQL bem"
  log "     o bastante para esta medição — o par mediria o WRITER, não o PLANO."
  log "     DECISÃO NECESSÁRIA: refazer a perna ON com v4-pro (US$ 14,20 o par) ou abandonar o eixo."
  exit 2
fi
log "  ✅ dentro da referência — seguindo para a perna OFF"

log "perna 2/2: IMDb MySQL, plano OFF"
bash "$O" bash "$I" deepseek-v4-flash mysql off 5
log "  exit=$?"
log "###### PAR COMPLETO — comparar ALCANCE e CONSISTÊNCIA (a régua de alcance já enganou 3× em 18-19/08) ######"

# ⛔ 0b pro — CORTADA (19/08, decisão da usuária: orçamento).
#   As 11 runs restantes custariam US$ 2,01 (pro = US$ 0,1824/run, 4x o flash). A decisão do writer
#   já está sustentada sem elas:
#     · q1 (controle): flash 5/5 ≥96,3% × pro 5/5 ≥96,4%  -> idêntico
#     · q7           : flash 4/5 27,8% × pro 2/4 28,8%    -> mesmo ganho, flash MAIS consistente
#     · custo medido : flash US$ 0,0455/run × pro US$ 0,1824  -> 4x
#     · falha mecânica no PG: flash 6% (263 runs) × pro 12% (1.139)
#   ⚠️ A frase no paper fica ESCOPADA: "sonda pareada nas DUAS queries medidas nos dois tiers mostrou
#   ganho equivalente" — nunca "os tiers são equivalentes", que 2 queries não sustentam.

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# ⭐⭐ O PAR QUE RESPONDE A MANCHETE DO P1 — "o gargalo está em ACHAR ou em ESCREVER?"
#
#   C1: qwen 8B LOCAL acha  → flash escreve      (2 agentes)
#   C2: flash acha           → flash escreve      (2 agentes)  ← o TETO
#
# MESMO writer, MESMA janela, MESMAS 21 queries, n=5. A ÚNICA variável é QUEM ACHA. Se o teto
# empatar com o 8B, o achado NÃO é a parede — e a alocação por tier fica justificada por MEDIÇÃO,
# não por intuição. É a evidência central do P1.
#
# ⚠️ O C2 NÃO É o `control_monolithic` (0d/C3): lá o modelo é UM agente e o campo `strategy` sai
#    VAZIO. Aqui são DOIS — o flash produz a estratégia em texto e uma SEGUNDA chamada escreve a
#    partir dela. `C2 × C3` isola a decomposição; `C1 × C2` isola a qualidade do achado.
#
# ⚠️ NADA equivalente existe no corpo: as células com FIND de nuvem são todas `v4-pro`, em IMDb ou
#    em 4 queries (`ceiling_tpcds_zerogain`), nunca nas 21 do TPC-DS e nunca a n=5.
#
# Regime do P1: sem plano, sem masking, sem regras — mede CAPACIDADE, não arquitetura.
# Custo: C1 US$ 4,78 · C2 US$ 9,55 (duas chamadas de nuvem por tentativa) = US$ 14,33.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
log "⭐ C1 — qwen 8B local ACHA · flash ESCREVE (2 agentes, n=5)"
bash "$O" bash scripts/campanhas/p1_cloud.sh qwen deepseek-v4-flash 5
log "  C1 exit=$?"

log "⭐⭐ C2 — TETO: flash ACHA e flash ESCREVE (2 agentes, n=5)"
bash "$O" bash scripts/campanhas/p1_cloud.sh cloud deepseek-v4-flash 5
log "  C2 exit=$?"

log "###### C1+C2 COMPLETOS — comparar: se o TETO empatar com o 8B, o achado não é a parede ######"

# ⚠️ MARCADOR DE CONCLUSÃO (21/08). A cadeia seguinte esperava esta SUMIR da lista de processos —
# mas "não existe mais" ≠ "concluiu". Ao pausar a nuvem manualmente, matei esta cadeia e a seguinte
# interpretou como término, disparando o C3 no lugar da perna OFF do plano. Um arquivo distingue
# os dois casos: só é criado quando a fila chega ao fim de verdade.
touch .cache/.queue_plan_verdict.DONE
log "###### FILA DO VEREDITO CONCLUÍDA ######"
