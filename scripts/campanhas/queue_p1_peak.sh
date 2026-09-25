#!/usr/bin/env bash
# P1 LOCAL QUEUE — the FREE cells, run inside the PEAK windows where the cloud pauses.
#
# WHY A FILE AND NOT `bash -c '...'`
#   The previous queue was a `bash -c` whose text contained `grep -qF "p1_local.sh deepseek"`. That
#   text is visible in `ps`, so the wait loop matched ITSELF and never exited — the whole P1 side sat
#   idle and ~7h/day of free machine was wasted. A script file's ps line is just its path.
#
# ORDER — P1-0 FIRST, and it is not arbitrary
#   P1-0 answers "does the local writer need to REASON?", and its answer decides the writer used by
#   every later local cell. Running the later cells first risks having to redo them.
#   Arm 1 (qwen2.5-coder, no reasoning) is ALREADY DONE — `p1_mistral_aided_local`, 63 runs. What is
#   missing are the two arms that make it a comparison:
#     · deepseek-coder:6.7b   — code model, SAME FAMILY as the cloud writer
#     · qwen3:8b + reasoning  — reasoning model, same size class -> isolates REASONING from SIZE
#
# Everything here is local (ollama). Zero API cost.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/queue_p1_peak.log
mkdir -p logs/campanhas
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

# ⚠️ NÃO AVANÇAR QUANDO O ITEM ABORTA (20/08). O supervisor de pico devolve o código do filho, e a
# fila tratava QUALQUER saída como "próximo item". Em 20/08 03:01 a célula `qwen raw` abortou pela
# guarda de concorrência (bug do congelamento) com 9 de 63 runs, e a fila seguiu adiante — a célula
# ficou pela metade sem ninguém perceber. Agora cada item é tentado até 3 vezes antes de desistir.
run_item(){
  local desc="$1"; shift
  local try=1 rc pausas=0
  # ⚠️ `while`, não `for try in 1 2 3` — dentro de um `for` sobre lista, alterar a variável NÃO muda
  #    quantas voltas o laço dá. Precisamos poder NÃO consumir tentativa (ver abaixo).
  while [ "$try" -le 3 ]; do
    log "$desc (tentativa $try/3)"
    "$@" && { log "  ✅ ok"; return 0; }
    rc=$?
    # ⛔⛔ INTERRUPÇÃO NÃO É FALHA — não gastar tentativa com ela (08/09).
    #
    #   O QUE ACONTECEU: o W2 foi ABANDONADO após "3 tentativas" — e as TRÊS foram mortes por
    #   DESCONGELAMENTO, não falhas da célula. O `peak_local` congela o local (SIGSTOP) enquanto a
    #   nuvem usa a máquina; o SIGSTOP para o processo mas NÃO o relógio do socket, então uma janela
    #   longa (10-15 h, como as de 07 e 08/09) mata o run em voo assim que ele descongela. O
    #   `p1_local.sh` sai com rc=4 ("sem linha == done:") — corretamente, para não marcar a célula
    #   como concluída. Mas o contador lia isso como fracasso.
    #   ⇒ A célula ia BEM (11 → 25 runs) e foi descartada por um efeito da alternância de janelas.
    #
    #   rc=4   = interrompida sem `== done:` (congelamento/kill) → NÃO consome tentativa
    #   rc=143 = SIGTERM                                          → NÃO consome tentativa
    #   ⚠️ Outros códigos (2 = nenhum run medido · 5 = matriz errada · 1 = canário) são falha REAL e
    #      consomem tentativa — senão um item quebrado giraria para sempre.
    #   ⛔ TETO DE PAUSAS: 20. Sem ele, um item que só é interrompido nunca sairia da fila e travaria
    #      todos os itens seguintes. 20 x (1 run + 1 min) cobre dias de alternância normal.
    if [ "$rc" -eq 4 ] || [ "$rc" -eq 143 ]; then
      pausas=$((pausas+1))
      if [ "$pausas" -gt 20 ]; then
        log "  ⛔ INTERROMPIDA 20 vezes sem concluir — algo está errado além da alternância. Desistindo."
        return 1
      fi
      log "  ⏸️ INTERROMPIDA (rc=$rc) — não é falha; tentativa $try NÃO consumida (pausa $pausas/20)"
      sleep 60
      continue
    fi
    log "  ⚠️ item saiu com erro (rc=$rc) — aguardando 5 min e tentando de novo"
    try=$((try+1))
    sleep 300
  done
  log "  ⛔ desistindo após 3 tentativas: $desc"
  return 1
}

# ── MARCADORES POR ITEM (25/08) — para que um REINÍCIO retome de onde parou
#
# ⚠️ POR QUE EXISTE: esta fila é uma sequência linear. Quando a máquina reiniciou às 07:21 de 25/08, ela
#    voltou ao item 1 — e o `P1-0 braço 2`, que tinha COMEÇADO às 04:00, ficou para trás. Nenhuma run
#    gravada se perde (o `--resume` conta o que está no store), mas se perde JANELA: o pico é de 7h/dia,
#    e re-percorrer itens já concluídos consome o horário em que o local é gratuito.
#
# Cada item concluído deixa `.cache/queue_p1_peak_steps/<id>.DONE`. Um relançamento pula os concluídos
# e retoma no primeiro pendente. Para forçar a re-execução de um item, apagar o marcador dele.
STEPS=.cache/queue_p1_peak_steps
mkdir -p "$STEPS"

# ⚠️ A DESCRIÇÃO TEM DE COMEÇAR PELO RÓTULO DA FILA (`L1`, `L3b`, `M2`, `LA`…), porque é dele que o
#    `health_check.sh` extrai o item para conferir contra o `next_steps.md`. Sem rótulo, a checagem
#    PULA o item em silêncio — foi assim que `LA` e `LB` rodaram sem estar no planejamento.
step(){          # step <id> "<RÓTULO> · <descrição>" <comando...>
  local id="$1" desc="$2"; shift 2
  if [ -f "$STEPS/$id.DONE" ]; then
    log "  ⏭️  PULANDO «$desc» — já concluído ($id.DONE)"
    return 0
  fi
  run_item "$desc" "$@" && touch "$STEPS/$id.DONE"
}

P=scripts/campanhas/peak_local.sh
L=scripts/campanhas/p1_local.sh

log "###### P1 LOCAL QUEUE (peak windows only) ######"
log "  itens já concluídos: $(ls "$STEPS" 2>/dev/null | wc -l) — apague $STEPS/<id>.DONE para refazer"

# BUDGET PROBE — ✅ JÁ RODOU em 19/08 08:35, resultado DEFINITIVO (removida da fila para não repetir):
#   q1  ❌ não terminou em 600s · q30 ❌ 601s · q81 ❌ 600s
# Nenhuma das três originais completa em 10 minutos (13x o orçamento do gate). Os três lands ficam
# PROVISÓRIOS de forma permanente — e isso deixou de ser ressalva para virar FATO MEDIDO.
# Ganho colateral: o piso do limite inferior sobe de 45s para 600s → lb de ~96-98% para ~99,7-99,9%.

# DISCOVERY YIELD — a DRY report answering "could we discover more than two rules?" (user, 18/08).
# Runs second: it needs ollama (embeddings + teacher) and so must not overlap the cloud cell's FIND.
# ⛔ DRY ONLY. Injecting new rules mid-campaign would give mistral a different rule set from llama and
# qwen, and the contour law would measure "how many rules" instead of "capacity of the receiver".
step discovery_yield "L2 · discovery yield (dry-run, nada é injetado)" \
     bash "$P" .venv/bin/python scripts/discovery_yield_report.py --store aided

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# ORDEM CORRIGIDA 20/08 — o P1-0 DECIDE O WRITER LOCAL, então tem de vir ANTES das células que o usam.
# O `next_steps` já dizia isso ("P1-0 vem PRIMEIRO: define o writer local de TODAS as células
# seguintes") e eu tinha posto a escada do qwen na frente. É a mesma dependência do 0c no P2 —
# escolha de componente antes de quem a consome — aplicada lá e esquecida aqui.
#
# ⚠️ O RISCO CONCRETO: se o P1-0 mostrar que `qwen3:8b`+reasoning escreve muito melhor que o
# `qwen2.5-coder:7b` (o padrão), o degrau `aided-local` estaria medindo um writer pior que o
# disponível — e a conclusão "o WRITE é a parede" sairia INFLADA. O degrau ficaria subestimado.
#
# `qwen raw` NÃO depende disso (é agente único, não tem writer) → continua na frente.
# ═══════════════════════════════════════════════════════════════════════════════════════════════

step qwen_raw_think "L3 · QWEN raw +think — não depende do writer local" \
     env RAWARM=raw bash "$P" bash "$L" qwen 3

# ── P1-0: QUAL WRITER LOCAL? Três braços, MESMO FIND (mistral). O braço 1 (`qwen2.5-coder:7b`) já
#    existe em `p1_mistral_aided_local` (n=3). Estes são os dois que faltam.
# ══════════════════════════════════════════════
# ⚠️ PRIMEIRO DE TUDO: FECHAR O PAINEL n=3 QUE ESTÁ INCOMPLETO (achado 25/08).
#
#   O store tem 63 json e PARECE completo — mas 13 deles são `timeout`, que o --resume não conta
#   como run. O painel cross-model estava documentado como fechado com TRÊS QUERIES SEM DADO NENHUM.
#
#   | célula          | runs reais | alvo | buracos                                   |
#   |-----------------|-----------|------|-------------------------------------------|
#   | p1_deepseek_raw | 50        | 63   | q63/q67/q85 = ZERO · q18/q40/q69/q81 = 2  |
#   | p1_qwen_raw     | 62        | 63   | q81 = 2                                    |
#
#   ⛔ Nada mais roda antes destes dois. Um painel com query sem dado não é n=3 — é n=3 alegado.
#   ⚠️ STORE_NAME é obrigatório aqui: o nome legado é `p1_deepseek_raw`, e sem o override este
#      script escreveria em `p1_deepseek_aided_local_raw` — um store NOVO, do zero.
# ══════════════════════════════════════════════

step deepseek_raw_fill "L0 · FECHAR p1_deepseek_raw — 13 runs faltando, 3 queries em ZERO" \
     env RAWARM=raw STORE_NAME=p1_deepseek_raw INDEX_STORE_NAME=index_p1_deepseek_raw \
     bash "$P" bash "$L" deepseek 3

step qwen_raw_fill "L0b · FECHAR p1_qwen_raw — a q81 tem 2 runs reais, não 3" \
     env RAWARM=raw bash "$P" bash "$L" qwen 3


# ══════════════════════════════════════════════
# ⭐ TIER 1 — A ESPINHA DO PAPER. Reordenado 26/08 a pedido da usuária ("acho que não
#    priorizamos bem"), e ela estava certa: o degrau que FALTA estava na 10ª posição.
#
#    Estado da escada do qwen quando reordenamos:
#      raw          68 runs · 21 queries        ✅ (L0c subindo para n=5)
#      aided-local   7 runs ·  7 queries        ⛔ PRATICAMENTE INEXISTENTE  ← este
#      aided-cloud 108 runs · 21 q · alcance 13 ✅ C1 fechado
#      teto         36 runs ·  8 queries        🔄 C2 rodando
#
#    ⛔ O P1-0 NÃO bloqueia mais o L4. Ele existia para ELEGER o writer local — mas a coluna
#       aided-local JÁ está padronizada em `qwen2.5-coder:7b` nas três famílias (llama 105 runs,
#       mistral 63, qwen 7). Trocar o writer só no qwen quebraria a coluna: é exatamente o erro
#       dos TRÊS writers diferentes que achamos na coluna de nuvem em 25/08.
#       → O writer está escolhido por CONSISTÊNCIA DE COLUNA, e o P1-0 vira ABLAÇÃO ("e se fosse
#         outro writer?"), lá no fim. Frase honesta: "usamos o writer que padroniza a coluna, e
#         medimos depois se a escolha importa".
#    ⚠️ O writer agora é EXPLÍCITO no comando. Antes vinha do default do p1_local.sh — sairia certo,
#       mas por acidente, e um default mudado silenciosamente quebraria a coluna sem aviso.
# ══════════════════════════════════════════════



# ⚡ SL1 ANTES DO L4 (29/08) — decisão da usuária: *"esse schema linking é muito importante nessas
#    tarefas"*. Ele é n=3 (~2 dias) e responde a pergunta que preocupa; o L4 é n=5 e leva ~10 dias.
#    ⚠️ O L4 foi RECOMEÇADO DO ZERO: o prompt mudou em 29/08 e os 54 runs anteriores usavam a versão
#       antiga → quarentenados em `.cache/suggestions_p1_qwen_aided_local_PROMPTv1_29ago`.
#       Misturar duas versões de prompt na mesma célula é a contaminação silenciosa que já nos custou
#       dias (dois regimes no `tpcds_mysql`, três writers na coluna de nuvem).
# ── ⭐ SL1 · O PAR DO SCHEMA-LINKING (enfileirado 29/08 — preocupação da usuária)
#
#   O QUE SE DESCOBRIU: verificado no CÓDIGO que, com `SCHEMA_LINKING=off`, o modelo **NUNCA vê o
#   schema** — nem colunas, nem chaves de junção — em NENHUM braço, raw incluído. Toda a matriz do P1
#   está assim (nenhuma célula tem `+schemalink` no rótulo). O regime é UNIFORME, então as comparações
#   internas estão limpas — mas o PISO é mais baixo que o de qualquer trabalho lido: LLM-R², R-Bot,
#   GenRewrite, LITHE, QUITE, LLM-QO e QueryBooster **todos** dão schema.
#
#   ⛔ O contra-argumento que um revisor vai fazer: schema-linking é DETERMINÍSTICO e BARATO — é
#      higiene, não ajuda cara. "Vocês mediram um setup que ninguém usaria."
#   ✅ A resposta não é negar, é MEDIR. Este é o par limpo do L4: MESMO FIND, MESMO writer, MESMO n —
#      muda **uma** coisa, o schema.
#   📊 Evidência de que importa (29/06): schema-linking no writer levou o qwen de 0/9 → 1/9, e a q9
#      virou de `mechanics_failed` para S≠1 (matou o erro de COLUNA, expôs a EQUIVALÊNCIA).
#   ⚠️ Store SEPARADO (`_schemalink`) — não contamina o L4.
#   📌 n=3, não 5: é ponto de EIXO (mede o efeito), não degrau da escada da manchete.
step qwen_aided_local_SL "SL1 · qwen aided-local COM schema (21×5) — O DEGRAU da escada" \
     env SL=on bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b

# ── ⛔ L4 REMOVIDO 29/08 — decisão da usuária: **"vamos manter o schema"**.
#
#   O L4 era `qwen aided-local SEM schema`, n=5. Com o schema mantido, ele e o SL1 viram A MESMA
#   CÉLULA — o SL1 é exatamente isso com schema. Em vez de rodar 105 runs no L4 e 63 no SL1, o SL1
#   sobe para n=5 e cumpre OS DOIS papéis: degrau da escada E ponto do eixo do schema.
#   ⭐ Economia: ~105 runs.
#
#   BASE DA DECISÃO (sinal com 33 runs, 11/21 queries): `mechanics_failed` **54%** com schema, contra
#   **63-68%** sem (qwen quarentenado 68%, llama 68%, mistral 63%). O schema tira ~14 pontos — efeito
#   REAL, mas a distância para o `flash` escrevendo (7-10%) continua sendo CAPACIDADE, não informação.
#
#   ⚠️ CUSTO A DECLARAR NO PAPER: llama (105 runs) e mistral (63) do braço aided-local são
#      **schema-off**. A comparação ENTRE MODELOS nesse braço passa a misturar regimes. É declarável
#      — "o qwen foi medido também com schema" — mas NÃO é comparação limpa.
#   ✅ A ablação do schema não se perde: existe nos 54 runs quarentenados
#      (`p1_qwen_aided_local_PROMPTv1_29ago`) e nas células de llama/mistral.


# ── ⏸️ M2b REBAIXADO 28/08 23:45, DEPOIS DE MEDIDO — a promoção durou 50 minutos.
#
#   O QUE ACONTECEU: promovi o M2b à frente do L4 porque "17 runs ≈ 2 dias" era o melhor retorno por
#   hora da fila. A estimativa usou a MÉDIA da célula — e o que falta são as 5 queries que NUNCA
#   produziram dado (q40 q50 q51 q73 q81), isto é, a CAUDA. Em 50 min de execução: **14 chamadas ao
#   ollama e ZERO registros**. O L4, mesmo lento, produz ~1,3 run/h.
#
#   ⛔ E o MySQL estava OCIOSO o tempo todo (9 amostras de `processlist`, 0 queries ativas): não é a
#      original que não completa — é o LAÇO DE RE-ESTRATÉGIA da LLM. O custo é do conselho.
#
#   ⚠️ DÍVIDA TÉCNICA DESCOBERTA AQUI: o "orçamento de 40 min" **NÃO é teto rígido**. O cliente usa
#      `urllib.urlopen(req, timeout=...)`, que é timeout de **SOCKET**, não de duração total — se o
#      servidor mantém a conexão viva, o run passa de 40 min sem ninguém desistir. Os registros de
#      timeout que vimos (2177-2400 s) são casos em que o socket ficou mudo, não uma regra.
#      Um teto real exigiria watchdog no cliente.
#
#   ✅ DECISÃO: as 5 queries viram RESULTADO DECLARADO, não buraco a preencher —
#      *"em 5 das 21 queries do TPC-DS em MySQL, o modelo leve não converge a um resultado medível"*.
#      É medida legítima do eixo de engine. Forçá-las custaria dias para virar número.
# ── ⚡ M2b PROMOVIDO À FRENTE DO L4 (28/08) — melhor retorno por hora da fila inteira.
#
#   Medido: o L4 custa **335 s de inferência por run** (qwen `+think`), contra 91 s do llama
#   (reasoning OFF) e 179 s do mistral. Não é a célula que está lenta — é o RACIOCÍNIO do qwen, e
#   nenhuma outra célula do qwen será mais rápida. A ~1,3 run/h, os 94 runs que faltam no L4 são
#   ~72 h = **10 dias** de janela de pico.
#
#   O M2b são **17 runs = ~2 dias**, e fecha **5 queries que hoje não têm dado NENHUM**
#   (q40 q50 q51 q73 q81) na escada do MySQL — que é a manchete CONDICIONAL.
#   ⛔ Trocar o L4 por LA ou L8 NÃO ganharia tempo (7 dias cada) e adiaria a manchete principal.
#      Só o M2b é barato o bastante para valer a inversão.
#   ⚠️ Precisa dos containers de MySQL NO AR — em 28/08 o C4 falhou 63/63 com HTTP 500 porque eles
#      estavam parados há 4 dias. O script dá `docker start`, mas conferir.
# ── Lei do contorno: par interno (regras on × off) com o writer CONSTANTE, então não depende do P1-0.
# ── M2 (25/08) — o braço de 1 AGENTE que falta no MySQL/IMDb ─────────────────────────────────────
# Com o P2 fora do ciclo, o MySQL passou a ser do P1 e é ele que dá a MANCHETE CONDICIONAL. Auditando
# por BRAÇO (não por célula — o alcance por célula mistura os dois e já me enganou):
#     TPC-DS MySQL raw  `noplan`  ⚠️ INCOMPLETO — 46 runs REAIS de 63 (corrigido 25/08: o "63" vinha
#                                    do contador que somava os 17 timeouts de baseline como runs)
#     IMDb   MySQL raw  `noplan`  ❌ NÃO EXISTE — `imdb_mysql` é plano-ON, e só tem 13 das 21 queries
#  ⚠️ E o store `tpcds_mysql` guarda DOIS regimes no mesmo diretório (`+nohw` plan-ON e
#     `+nohw+noplan`). Qualquer leitura que não filtre por `model` mistura os dois — o que é
#     comparação inválida pela nossa própria regra de setup.
# Sem esta célula, o IMDb/MySQL não tem braço de agente único no regime do P1, e a comparação
# monolítico × decomposto fica só com o TPC-DS.
# ⛔ M2 REMOVIDO 26/08 — o rótulo dizia "IMDb MySQL raw" mas o comando NÃO tinha `BENCH=imdb`.
#    Sem isso a URI é a do TPC-DS/MySQL, e o `run_matrix` escolhe o benchmark PELA URI — teria
#    rodado TPC-DS com nome de IMDb, e o resultado entraria na tabela errada.
#    ✅ Substituído pelo **I2**, no Tier 2, com o env correto.
#    ⚠️ STORE_NAME obrigatório — o store legado é `tpcds_mysql` e guarda TAMBÉM o braço plan-ON.
#    O `--resume` filtra por `model`, então só completa o braço `+noplan`; o plan-ON não é tocado.




# ══════════════════════════════════════════════════════════════════════════════════════════════
# DUAS CÉLULAS QUE FECHAM BURACOS DO P1 (21/08) — locais e GRÁTIS
#
# (A) MySQL — mata a exposição de ENGINE ÚNICO, um dos três riscos que listamos para o P1.
#     A escada de MySQL do qwen JÁ TEM os outros dois degraus:
#         raw          `tpcds_mysql`             n=6 · 8/21
#         aided-cloud  `tpcds_mysql_aided_cloud` n=3 · 7/20
#     Falta só o aided-local. Com ele, a frase vira "a parede reaparece num SEGUNDO engine",
#     que responde ao "isso é artefato do planner do PostgreSQL".
#     ⚠️ Sinal a observar: no MySQL o RAW alcança 8 e o aided-cloud 7 — o agente único empata ou
#        supera a arquitetura, o INVERSO do PostgreSQL. (Ressalva: writers e n diferentes.)
#
# (B) reasoning OFF — par limpo ON × OFF no regime do P1, na célula que a MANCHETE usa.
#     Hoje só temos o par de IMDb/MySQL, braço raw (ON 0/13 × OFF 2/13): contemporâneo, mas de
#     OUTRO quadrante. E o paper afirma que o raciocínio do WRITER é essencial (14→8) enquanto
#     desliga o do FIND — o revisor pergunta por quê, e a resposta precisa ser medida.
# ══════════════════════════════════════════════════════════════════════════════════════════════
step qwen_aided_local_mysql "LA · qwen aided-local em MySQL — fecha a exposição de engine único" \
     env SL=on bash "$P" bash "$L" qwen 3 off qwen2.5-coder:7b "" mysql

# ── L8 · `raw · TPC-DS/PG · ds-llama` — FECHA O BRAÇO VERTICAL DA CRUZ.
#    O painel de modelos vive numa coluna só (TPC-DS/PG) e tem 4 dos 5: qwen, deepseek-r1, llama,
#    mistral. Falta o `deepseek-r1:8b-llama-distill`.
#    ⚠️ NÃO é o mesmo modelo que `deepseek-r1:8b`: aquele é R1 destilado sobre **Qwen3**
#       (`ollama show` → architecture qwen3), este é destilado sobre **Llama**. Com os dois, o painel
#       ganha um par que isola a DESTILAÇÃO com a base constante (qwen puro × R1/qwen · llama puro ×
#       R1/llama) — que é mais forte do que só "5 modelos".
  #    ⚠️ PARCIAL (02/09) — a 1ª passada foi MORTA à 01:00 na troca pico→nuvem e registrada como
  #       concluída (bug #19 em documentation/fixed_bugs.md). Ficaram **6 de 21 queries** no store
  #       `p1_deepseek-llama_aided_local_raw`: q1 q7 q38 q51 q67 q69, 18 runs, alcance 0.
  #       O `.DONE` foi apagado; o `--resume` retoma nas 15 que faltam, sem repetir as 6.
step dsllama_raw "L8 · RAW ds-llama (TPC-DS/PG) — fecha o painel VERTICAL de 5 modelos" \
     env RAWARM=raw bash "$P" bash "$L" deepseek-llama 3

# ══════════════════════════════════════════════
# ⬜ O RESTO — importante, mas não é manchete. Roda depois; nada foi cortado.
# ══════════════════════════════════════════════

# ── W1/W2 · ABLAÇÃO DO WRITER LOCAL, sobre a MESMA célula do L4 (repromovida 26/08).
#
#   Eu tinha rebaixado isto ao Tier 4 argumentando que o writer já estava escolhido por CONSISTÊNCIA
#   DE COLUNA (llama 250 runs e mistral 70 usam `qwen2.5-coder:7b`). O argumento é verdadeiro e
#   INCOMPLETO — a usuária apontou o furo: **o resultado do aided-local só vale o quanto vale o
#   MELHOR writer local que tentamos.** Se o `qwen2.5-coder` escrever mal, o braço parece fraco e a
#   leitura vira "escrever localmente não funciona" — quando só escolhemos mal. Isso SUBVENDE
#   exatamente a tese leve/local que o braço existe para sustentar.
#
#   ✅ Desenho final: o **L4** (n=5, `qwen2.5-coder`) é o número COMPARÁVEL com llama/mistral; W1 e W2
#      medem se **outro writer local faria melhor**. Frase que isso habilita: *"o melhor writer local
#      disponível entrega X"* — mais forte que "um writer local entrega X".
#   ⚠️ Stores SEPARADOS por coder (`_dscoder`, `_qwen3writer`) — sem risco de misturar writers.
#   ⛔ `p10q_arm1` REMOVIDO: era `qwen 3 off qwen2.5-coder:7b` = o PRÓPRIO L4 com n menor e o MESMO
#      store; depois do L4 o `--resume` não faria nada.
#   📊 Métrica pré-registrada: `mechanics_failed` (primária) · `equivalence_failed` (secundária) ·
#      lands só como desempate.
# ⚠️ SL=on ADICIONADO 29/08. Sem isto, W1/W2 rodariam schema-OFF e o par com o SL1 (schema-on)
#    mudaria DUAS coisas — o writer E o schema. O eixo mede o WRITER, então o schema fica constante.
#    ⭐ Frase que isto habilita: "o melhor writer local disponível, com schema, entrega X".
step p10q_arm2 "W1 · writer local ALTERNATIVO: deepseek-coder:6.7b (controle de FAMÍLIA)" \
     env SL=on bash "$P" bash "$L" qwen 3 off deepseek-coder:6.7b

step p10q_arm3 "W2 · writer local ALTERNATIVO: qwen3:8b COM reasoning (isola RACIOCÍNIO de TAMANHO)" \
     env SL=on bash "$P" bash "$L" qwen 3 off qwen3:8b on

# ── ⏰ M2b POSICIONADO PARA HORÁRIO ACOMPANHADO (30/08, pedido da usuária).
#    Ele é o item de MAIOR RISCO de queimar janela sem produzir: em 28/08 rodou **50 min sem gravar
#    um único registro**. As 15 runs que faltam são exatamente as 5 queries que NUNCA produziram dado
#    (q40 q50 q51 q73 q81), e o gargalo é o LAÇO DE RE-ESTRATÉGIA — não o banco (MySQL ocioso em 9
#    amostras de `processlist`).
#    ⭐ Vale tentar de novo porque o prompt mudou (v2), mas o critério de desistência é:
#       **~2 h sem nenhum registro ⇒ pular para o próximo** e declarar as 5 como resultado:
#       "em 5 das 21 queries do TPC-DS em MySQL o modelo leve não converge a um resultado medível".
#    ⛔ Continua schema-OFF (completa store com 65 runs schema-off).
# ── ⬆️ M2b RE-PROMOVIDO 30/08 (pedido da usuária). Ele já tinha rodado 50 min em 28/08 sem gravar
#    registro — o gargalo eram as 5 queries que nunca produziram dado (q40 q50 q51 q73 q81), e o
#    laço de re-estratégia, NÃO o banco (MySQL ocioso em 9 amostras de `processlist`).
#    ⛔ CONTINUA SCHEMA-OFF: completa o store `tpcds_mysql`, que tem 65 runs schema-off. Ligar SL
#       misturaria regimes DENTRO da célula.
#    ⭐ Por que vale subir: é o DEGRAU RAW da escada do MySQL — sem ele não se escreve "a parede muda
#       de lugar entre engines", só "o aided-local no MySQL faz X".
# ── ⏬ M2b REBAIXADO 30/08 — decisão da usuária, e o motivo é o REGIME.
#
#   Ele COMPLETA o store `tpcds_mysql`, que tem **65 runs schema-OFF**. Com o resto da fila indo para
#   schema-ON, ele seria a única célula fora do padrão — e ligar `SL=on` nele NÃO é opção: misturaria
#   os dois regimes DENTRO da mesma célula, o que é dado inutilizável (não há como separar depois).
#
#   ⚠️ CONSEQUÊNCIA A DECLARAR: a escada do MySQL fica **sem o degrau raw**.
#      raw = 46/63 runs, com **5 queries em ZERO** (q40 q50 q51 q73 q81).
#      ⇒ Dá para escrever "o qwen aided-local no MySQL faz X"; ⛔ NÃO dá para escrever
#        "a parede muda de lugar entre engines" — isso precisa do raw como PISO.
#   ✅ Se a manchete condicional for necessária, a saída é rodar a célula INTEIRA com schema num store
#      NOVO (63 runs, ~20h), não completar a antiga.
# ══════════════════════════════════════════════
# ⚠️⚠️ REGRA DO SCHEMA, CORRIGIDA 30/08 — NÃO é "tudo com schema".
#
#   O critério não é a data: é **com que conjunto a célula vai ser comparada**.
#
#   ⛔ SCHEMA OFF quando a célula COMPLETA ou ENTRA num conjunto já medido schema-off:
#      · M2b → escreve em `tpcds_mysql`, que tem **65 runs schema-off**. Com SL=on misturaria os dois
#        regimes DENTRO da mesma célula — pior que misturar entre células.
#      · L8  → entra no painel raw (qwen/llama/mistral/deepseek), todo schema-off.
#   ✅ SCHEMA ON quando a célula é NOVA ou é ablação sobre o SL1:
#      · LA (aided-local MySQL, não existe) · W1 e W2 (ablação de writer SOBRE o SL1).
#
#   📊 Base da regra (medida 30/08): o schema mexe **3 pontos** no `mechanics_failed` (65% com × 63-68%
#      sem). ⇒ os dois regimes são quase equivalentes, e a diferença é DECLARÁVEL. Mas "quase" só vale
#      ENTRE células — dentro de uma, nunca.
# ══════════════════════════════════════════════
# (histórico) A decisão de 29/08 dizia "tudo com schema"; corrigida acima.
#   Base: `mechanics_failed` 54% com schema × 63-68% sem. O efeito é real (~14 pontos) mas não é o que
#   separa o 8B do `flash` (7-10%) — essa distância é CAPACIDADE.
#   ⚠️ As células JÁ MEDIDAS ficam como estão (schema-off) e são declaradas. Nada é refeito.
#   ⛔ Consequência: comparações entre células novas e antigas cruzam regimes — declarar SEMPRE.
# ══════════════════════════════════════════════
step tpcds_mysql_raw_fill "M2b · FECHAR TPC-DS MySQL raw `noplan` — 17 runs faltando de 63" \
     env RAWARM=raw STORE_NAME=tpcds_mysql INDEX_STORE_NAME=index_tpcds_mysql \
     bash "$P" bash "$L" qwen 3 off "" "" mysql

# ── L0c · REBAIXADO 27/08 — estava ANTES do L4 e virou gargalo.
#
#   Ele já subiu a n=5 as 9 queries FÁCEIS; o que sobrou são as que acumulam timeout (q11, q30, q67,
#   q81, q38), onde cada tentativa custa até 40 min SEM produzir dado. Em 1h11 de janela não fechou
#   um run real. Reprojetado: mais 3-4 dias — e travava o L4, que é o degrau que NÃO EXISTE (7 runs).
#
#   ✅ Por que dá para adiar sem perder nada: o **painel cross-model lê `--first-n 3`** de todas as
#      células, então o raw a n=3 já serve para tudo. O que o topup entrega é SÓ a régua de
#      CONSISTÊNCIA no degrau raw da escada — útil, mas não bloqueia leitura nenhuma.
#   ⛔ O L4, ao contrário, é pré-requisito de uma frase: sem ele não se pode escrever "dá para rodar
#      tudo na sua máquina", só "dá para ACHAR localmente, se você pagar uma API para escrever".
# ── L0c · O raw do qwen é degrau da ESCADA (n=5 pela regra) E ponto do PAINEL cross-model (n=3).
#    Sobe para 5 SEM quebrar o painel, porque o topup só ACRESCENTA runs: os 3 primeiros de cada
#    query continuam sendo exatamente os que existem hoje.
#    🔒 REGRA DE LEITURA (declarar no paper): o painel cross-model lê os **3 primeiros runs por
#       query, em ordem cronológica** — de TODAS as células, qwen inclusive. A escada lê os 5.
#       Uma célula, duas leituras, cada uma com o seu n declarado. ⛔ Nunca comparar alcance de
#       n=5 contra alcance de n=3: mais runs = mais chance de landar ≥1, o viés é mecânico.
#    ⚠️ Roda DEPOIS de L0/L0b: fechar buraco vem antes de subir alvo.
step qwen_raw_topup "L0c · QWEN raw n=3 → n=5 — fecha a régua da ESCADA (painel segue lendo os 3 primeiros)" \
     env RAWARM=raw bash "$P" bash "$L" qwen 5


# ══════════════════════════════════════════════
# ⭐ TIER 2 — IMDb RAW: o filtro de GENERALIDADE das regras (enfileirado 26/08 a pedido da usuária)
#
#   POR QUE ISTO NÃO É "só cobertura de um 2º benchmark":
#   O último `discovery_yield` produziu 7 candidatos a regra — e DOIS vinham com nome de coluna do
#   TPC-DS colado dentro (`pre-filter CTE with ca_state = 'TN'`, `replace IN subquery with JOIN to
#   date_dim_filtered`). Uma regra destilada só de TPC-DS pode ser PADRÃO MEMORIZADO do benchmark,
#   não regra. O teste que separa os dois é: **ela sobrevive em outro dataset?**
#   → O corpus de IMDb é o que alimenta o L6/L7 (lei do contorno) com regras que generalizam.
#   ⭐ E casa com a crítica publicada (Leis et al., VLDB'15): TPC-DS é gerado com as MESMAS
#     suposições que o otimizador faz; IMDb existe porque o sintético é fácil demais.
#
#   ⛔ ESTADO: NÃO temos NENHUM IMDb raw no regime do P1. As três células que existem
#      (`imdb_pg`, `imdb_mysql`, `imdb_mysql_schemalink`) são TODAS **plan-ON** — fora do regime.
#
#   ⚠️ No IMDb `VERIFY_DB_URI = DB_URI` (dataset real, sem escala reduzida): ou a equivalência vale
#      em escala real, ou não vale. Nenhum land de IMDb cai no fallback SF1.
#   ⚠️ Containers ficam parados entre campanhas — o script dá `docker start`.
# ══════════════════════════════════════════════

step imdb_pg_raw "I1 · IMDb/PG raw `noplan` — alimenta a descoberta de regras" \
     env RAWARM=raw BENCH=imdb bash "$P" bash "$L" qwen 3

step imdb_mysql_raw_noplan "I2 · IMDb/MySQL raw `noplan` — o par cross-engine do I1" \
     env RAWARM=raw BENCH=imdb bash "$P" bash "$L" qwen 3 off "" "" mysql

# ══════════════════════════════════════════════
# TIER 2b — MANCHETE CONDICIONAL (eixo de engine, TPC-DS).
# ══════════════════════════════════════════════







step deepseek_aided_local "L5 · deepseek-r1 no FIND, aided-local (writer vencedor)" \
     bash "$P" bash "$L" deepseek 3


step qwen_aided_local_nothink "LB · qwen aided-local reasoning OFF — par limpo ON × OFF" \
     bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b "" postgres off


# ── M3 · ESCOPO B (decidido 25/08): um SEGUNDO modelo no MySQL, para que a inversão da parede não
#    fique dependente de um modelo só. ⭐ O llama é a escolha certa: já tem a escada LIMPA em
#    PostgreSQL (legado da auditoria de 19/08), então o par PG×MySQL sai do que já existe.
#    ⛔ O mistral foi descartado para este papel: gastaria a janela para produzir ~67% de
#    `mechanics_failed`, que não discrimina nada.
#    📌 Os outros 3 modelos ficam como ESCOPO DECLARADO — "o eixo de engine é medido com dois
#       modelos" — e só entram se sobrar janela (escopo C, ~2,5 dias por modelo).
step llama_mysql_raw "M3 · LLAMA raw MySQL `noplan` — 2º modelo do eixo de engine (escopo B)" \
     env RAWARM=raw bash "$P" bash "$L" llama 3 off "" "" mysql

step mistral_norules "L6 · contorno 3º ponto (a): mistral SEM regras, n=3 → n=5" \
     bash "$P" bash "$L" mistral 5 off
# ⚠️ GUARDA (25/08): este item tem um bloqueio que NÃO é de fila — ele precisa das regras destiladas
#    em `rulesets/discovered_v2.json`. Sem o arquivo, `APPLY_HEURISTICS=on` rodaria com o ruleset
#    ERRADO (ou vazio) e a célula mediria outra coisa, sem ninguém perceber. A extração exige a GPU
#    sozinha e NÃO pode tocar o `learned_rules` (senão contamina as células unaided).
if [ ! -s rulesets/discovered_v2.json ]; then
  log "  ⛔ PULANDO «L7 mistral COM regras» — rulesets/discovered_v2.json não existe."
  log "     A célula mediria com ruleset errado. Extrair as regras primeiro (GPU sozinha)."
else
step mistral_rules "L7 · contorno 3º ponto (b): mistral COM regras, n=5" \
     bash "$P" bash "$L" mistral 5 on
fi

log "###### P1 LOCAL QUEUE DONE ######"

# ══════════════════════════════════════════════
# TIER 4 — ABLAÇÕES E RQs SECUNDÁRIAS. Nada aqui é pré-requisito de nenhum degrau da escada.
#   · P1-0 (3 braços) — DESPROMOVIDO de "seleção de componente" a ABLAÇÃO em 26/08.
#   · L3b/L1b/L2b — RQ3, ameaça à validade e rendimento da descoberta.
# ══════════════════════════════════════════════

# ── L2b · O yield é de 24/08, ANTES de o qwen raw fechar (+89 runs em 25/08 04:00). Re-rodar
#    com o corpo atual. ⛔ CONTINUA DRY — nada é injetado enquanto a campanha não fecha.
step discovery_yield_v2 "L2b · discovery yield RE-RUN com o corpo atual (dry-run)" \
     bash "$P" .venv/bin/python scripts/discovery_yield_report.py --store aided



# ── L3b (23/08) — a perna que FALTA do par da RQ3 ────────────────────────────────────────────────
# A RQ3 ("o raciocínio ajuda, e em qual eixo?") exige um A/B no MESMO engine E no MESMO setup.
# A perna `+think` é a de cima. A perna `nothink` NÃO EXISTE: varri o corpo e não há nenhuma célula
# PG raw com rótulo `nothink+nohw+noplan` — todas as `nothink` de PostgreSQL carregam `+schemalink`,
# e a maioria é de 2 agentes. Pela nossa regra de não comparar setups diferentes, nenhuma serve.
# ⇒ Sem esta célula, a RQ3 NÃO FECHA nem com a perna `+think` pronta.
step qwen_raw_nothink "L3b · QWEN raw NOTHINK — fecha o par da RQ3" \
     env RAWARM=raw REASONING=off bash "$P" bash "$L" qwen 3

# ── L1b (25/08) — SONDA DE VIÉS POSICIONAL ───────────────────────────────────────────────────────
# O LLM4IA (CIKM '25) demonstra que o GPT-4o "places higher attention on the beginning and ending
# parts of the workload, ignoring relevant information in the middle" — e escolhe o índice ERRADO por
# isso. O nosso prompt de índice entrega um bloco de schema e pede que o modelo escolha colunas dele:
# é exatamente essa configuração. O R-Bot e o LLM-R² citam a mesma degradação por contexto longo.
#
# ⚠️ POR QUE NÃO FICA SÓ COMO AMEAÇA DECLARADA: o eixo de índice é o CONTROLE NEGATIVO da tese
#    FIND/WRITE. Se ele estiver enviesado por ordenação, o argumento que sustenta "a parede é o WRITE"
#    fica frágil — não é ameaça periférica, é ameaça ao centro.
#
# LEITURA: mesmas colunas recomendadas -> capacidade, ameaça DESCARTADA com medição.
#          colunas diferentes          -> viés posicional, declarado COM magnitude.
# Comparação: scripts/compare_position_bias.py
step probe_position_bias "L1b · sonda de VIÉS POSICIONAL (schema invertido)" \
     bash "$P" bash scripts/campanhas/probe_position_bias.sh

# ══════════════════════════════════════════════════════════════════════════════════════════════
# ── L4 · qwen aided-local SEM schema, n=5 — REINSTAURADO 04/09 como ÚLTIMO item
#
#   ⚠️ ELE FOI REMOVIDO EM 29/08 POR DECISÃO, NÃO POR ESQUECIMENTO. O raciocínio de então era bom:
#      com o schema mantido, L4 e SL1 viram a mesma célula, e o SL1 a n=5 cumpre os dois papéis —
#      degrau da escada E ponto do eixo do schema. Economizou ~105 runs.
#
#   ⛔ O QUE ENFRAQUECEU. Aquela decisão fechava dizendo que "a ablação do schema não se perde:
#      existe nos 54 runs quarentenados e nas células de llama/mistral". Auditado em 04/09, nenhum
#      dos dois é par limpo do qwen:
#        · `p1_qwen_aided_local_PROMPTv1_29ago` (41 runs reais) usa o **prompt v1** — o prompt mudou
#          em 29/08, então a diferença medida seria prompt+schema, não schema;
#        · llama e mistral são **outros modelos** — a diferença seria modelo+schema.
#      ⇒ Hoje o eixo do schema tem UM LADO SÓ (SL1, schema ON) e o skeleton descreve um par que não
#        existe: *"SL1 é o par limpo do L4 — muda só o schema"*.
#
#   ⭐ O QUE ELE HABILITA, e nada mais habilita:
#      (a) a ABLAÇÃO DO SCHEMA no qwen — responde à crítica "schema-linking é higiene barata, vocês
#          mediram um setup que ninguém usaria" com medição em vez de argumento;
#      (b) o DEGRAU DO MEIO da escada do qwen sem schema: hoje é `L0b raw 6 → ??? → C1 aided-cloud 13`,
#          e as escadas de llama/mistral são schema-off — sem o L4, a do qwen não é comparável com elas.
#
#   ⚠️ n=5 obrigatório (o SL1 é n=5; comparar n=3 com n=5 é viés mecânico — mais runs, mais chance
#      de ≥1 land). ⛔ Sem `SL=on`: o ponto dele é ser o lado SEM schema.
#   ⏱️ 21 queries × 5 = 105 runs · ~276 s/run medidos → ~8 h de janela local.
#   📌 ÚLTIMO da fila por decisão da usuária (04/09) — tudo que já está enfileirado vem antes.
step qwen_aided_local_L4 "L4 · qwen aided-local SEM schema (21×5) — o lado que falta do eixo do schema" \
     bash "$P" bash "$L" qwen 5 off qwen2.5-coder:7b
log "⭐ P1-0 COMPLETO — conferir qual writer local vence ANTES de rodar os degraus aided-local"

# ── P1-0 REDESENHADO 25/08: o FIND passa de mistral para QWEN.
#    ⛔ Motivo (dados, não preferência): trocar quem escreve no substrato do mistral moveu
#    `mechanics_failed` de 42/63 (raw, mistral escreve) para 40/63 (writer qwen2.5-coder) — 2 em 63.
#    Um substrato que não responde a troca de writer não pode ELEGER writer. O qwen tem headroom
#    (alcance 6 × 1 do mistral). Os três braços rodam de novo com qwen no FIND.


