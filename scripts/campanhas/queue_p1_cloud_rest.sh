#!/usr/bin/env bash
# P1 EM NUVEM — o que vem DEPOIS do C1/C2 (que já estão na `queue_plan_verdict.sh`).
#
# Esta cadeia ESPERA a de cima terminar e emenda, para não depender de relançamento manual entre
# etapas. Ela NÃO decide nada — só executa as células do P1 que faltam em nuvem.
#
# ORDEM E POR QUÊ:
#   C3     — fecha a tripla. `C1×C2` isola a QUALIDADE DO ACHADO; `C2×C3` isola a DECOMPOSIÇÃO
#            (mesmo modelo nos dois, muda só se o achado é externalizado em texto). O `0d` atual
#            roda na composição do P2 (plano ON, masking ON) e por isso NÃO compara com o teto.
#   P1-1   — a coluna aided-cloud refeita: MESMO writer, MESMA janela, para as quatro famílias.
#            Sem isso a comparação ENTRE FAMÍLIAS mistura writers e eras — o confundimento que
#            invalidou o par de julho do llama e a escada do qwen.
#            ⚠️ A perna do `qwen` já é o C1 — não repetir aqui.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/queue_p1_cloud_rest.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

log "###### P1 NUVEM — o resto (espera o C1/C2 terminarem) ######"
# ⚠️ ESPERA O MARCADOR DE CONCLUSÃO, não o desaparecimento do processo (correção 21/08).
# "a cadeia anterior não existe mais" ≠ "a cadeia anterior concluiu": pausar a nuvem manualmente
# mata o processo, e a versão anterior disparava o C3 achando que o veredito tinha saído.
# ⚠️ ESPERA O MARCADOR DO C1+C2 (correção 22/08). O marcador do VEREDITO foi criado às 14:29 quando
# aquela cadeia terminou — mas o C1 e o C2 tinham ABORTADO por um falso positivo do canário e foram
# relançados numa cadeia separada (`queue_c1c2.sh`). Esta viu o marcador antigo, achou que o C1/C2
# estavam feitos e engatou o C3 — duas cadeias de nuvem disputando a mesma máquina.
# O marcador certo é o do C1+C2, que é o que de fato precede o C3.
while [ ! -f .cache/.queue_c1c2.DONE ]; do sleep 300; done
log "a fila do veredito CONCLUIU (marcador presente) — assumindo"
# ⚠️ C4 PROMOVIDO 28/08 — era o ÚLTIMO da fila, virou o PRIMEIRO depois do C2.
#    Motivo: saldo. Em 28/08 a conta DeepSeek tinha US$ 1,35 para ~US$ 25-30 de fila. A regra que a
#    usuária fixou: se o saldo apertar, o que fica de fora é COBERTURA, nunca MANCHETE.
#    O C4 é manchete — é o 3º degrau da escada do MySQL, que sustenta a claim CONDICIONAL por engine.
#    O P1-1 (a/b/c) é cobertura cross-model e pode esperar.
# ── C4 · REFAZER a coluna aided-cloud do MySQL em `flash` (achado 25/08).
#    `tpcds_mysql_aided_cloud` usa writer `deepseek-chat` — nome RENOMEADO, hoje devolve 400. É
#    writer de outra era e não se compara com o C1 (`flash`). Sem esta célula, a escada de MySQL do
#    qwen não tem degrau aided-cloud no regime do P1, e a MANCHETE CONDICIONAL fica sem apoio.
log "⭐ C4 — qwen FIND + flash em MySQL, n=3 · refaz a coluna aided-cloud do MySQL"
# ⚠️ ONLY com 16 das 21 queries (31/08) — ECONOMIA DE SALDO, não recorte de conveniência.
#
#   As 5 excluídas — **q40 q50 q51 q73 q81** — NUNCA produziram um run real no MySQL: no braço raw
#   (63 runs) todas estão em ZERO, só timeout. E o C4 estava repetindo o padrão: entre 11:12 e 13:57
#   gastou **5 tentativas na q51 e na q67, com ZERO runs** — cada timeout é uma chamada COMPLETA ao
#   `flash`, paga e descartada.
#
#   💰 Com US$ 2,75 de saldo e ~US$ 0,08/run, a célula inteira (52 runs restantes) custaria ~US$ 4,16
#      e estouraria NO MEIO — o modo de falha que grava `mechanics_failed` uniforme e PARECE bug do
#      modelo sendo billing (ver reference_deepseek_insufficient_balance).
#
#   ✅ As 5 viram RESULTADO DECLARADO, igual ao combinado para o M2b:
#      "em 5 das 21 queries do TPC-DS em MySQL o modelo não converge a um resultado medível".
#   ⚠️ A q67 FICA na lista: ela produz no braço raw. Se continuar só dando timeout aqui, revisar.
ONLY="q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q63 q67 q69 q85 q96" bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
log "  C4 exit=$?"

# e espera a máquina ficar livre (ignora processos CONGELADOS pelo supervisor de pico)
while ps -eo stat,args --no-headers | grep "[r]un_matrix.py" | grep -qv "^T"; do sleep 120; done

log "⭐ C3 — monolítico (1 agente) no REGIME DO P1, n=5 · fecha a tripla com C1 e C2"

# ⏬ CP1 REBAIXADA 30/08 (pedido da usuária): "vamos deixar a sonda pra depois das que faltam — a
#    gente não fechou o C3 nem o C4 ainda". Correto: a sonda decide sobre 35 runs JÁ MEDIDOS, e o
#    C4/C3 são células de MANCHETE que ainda não existem. Célula que falta vem antes de sonda sobre
#    célula que existe.
# ══════════════════════════════════════════════════════════════════════
# ⭐ CP1 · SONDA `deepseek-chat` × `deepseek-v4-flash` — 15 runs decidem sobre 95 já medidos
#
#   POR QUÊ. Duas células grandes usam o writer `deepseek-chat`, que foi RENOMEADO e hoje devolve
#   400: `imdb_pg_aided_cloud_noplan` (35 runs) e `tpcds_mysql_aided_cloud` (60). A usuária propôs
#   reutilizá-las, tratando `chat` como equivalente ao `flash`.
#   ⚠️ EVIDÊNCIA CONTRA: o mapeamento provável do rename era chat→flash (tier SEM raciocínio) e
#      reasoner→pro (COM). Mas medimos em 25/08 que o **flash raciocina nativamente**
#      (`reasoning_content` presente, `reasoning_tokens > 0`). Se o `chat` era o tier sem raciocínio,
#      NÃO são o mesmo modelo. A memória também registra "runs passados (writer=chat) não reproduzíveis".
#   ✅ Em vez de assumir ou de refazer 95 runs: MEDIR com 15.
#
#   O DESENHO. Mesma célula (`imdb_pg`, qwen local acha, noplan, sem schema, n=3) — muda SÓ o writer.
#   As 5 queries foram escolhidas para DISCRIMINAR, pelo que o `chat` fez nelas:
#     32a  3/3 lands (unânime)   · 7b  2/3 (1 mech) · 10a 2/3 (1 no_gain)
#     1a   0/3 (todos no_gain)   · 17f 0/3 (1 mech)
#
#   🔒 CRITÉRIO PRÉ-REGISTRADO (fixado ANTES de rodar):
#     ✅ ≥4 das 5 com o mesmo desfecho agregado → EQUIVALENTES: reutiliza os 95 runs
#     ⛔ ≤3 das 5                                → DIVERGEM: as células com `chat` são refeitas
#     ⛔ Basta para REPROVAR, isoladamente: a `32a` (3/3 no chat) não landar, OU a `1a` (0/3) landar.
# ══════════════════════════════════════════════════════════════════════
log "⭐ CP1 — sonda chat×flash: 5 queries do IMDb/PG, muda SÓ o writer"
BENCH=imdb ONLY="32a 7b 10a 1a 17f" bash "$O" bash "$P" qwen deepseek-v4-flash 3
log "  CP1 exit=$?"


log "P1-1 (a): llama FIND + flash, n=3"
bash "$O" bash "$P" llama deepseek-v4-flash 3
log "  exit=$?"
log "P1-1 (b): mistral FIND + flash, n=3"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "  exit=$?"
log "P1-1 (c): deepseek-r1 FIND + flash, n=3"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "  exit=$?"


# ── ⚠️ C3 · SEGUNDA PASSAGEM (01/09) — completar q5, q50 e q67.
#    A primeira passagem fechou com `exit=0` em 20 queries porque a LISTA estava errada (era da era
#    do P2: tinha `q84`, que não existe no corpus, e faltavam q5/q50/q67). A lista abaixo é
#    exatamente as 21 do C2 — o `--resume` pula as 18 já feitas e roda só as três.
#    ⛔ Sem isto, a comparação C2×C3 fica sobre denominadores diferentes (21 × 18 em comum).
# ⚠️ LISTA CORRIGIDA 01/09 — a anterior era da era do P2 e NÃO batia com as 21 do corpus:
#   tinha `q84` (que não existe no corpus) e FALTAVAM **q5, q50 e q67**.
#   Resultado: o C3 fechou com `exit=0` em 20 queries, e três delas nunca foram tocadas — a
#   comparação C2×C3 ficaria sobre denominadores diferentes (21 × 18 em comum).
#   ⛔ As outras células usam `--dirs` e pegam o corpus inteiro; só esta usa lista FIXA. É por isso
#      que só ela desviou.
#   ✅ Lista abaixo = exatamente as 21 que o C2 rodou.
bash "$O" bash scripts/campanhas/control_monolithic.sh 5 \
   q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q40 q50 q51 q63 q67 q69 q73 q81 q85 q96
log "  C3 exit=$?"

log "###### P1 NUVEM COMPLETO — falta só o P1-4 (IMDb/PG), cadeia imdb_battery ######"
