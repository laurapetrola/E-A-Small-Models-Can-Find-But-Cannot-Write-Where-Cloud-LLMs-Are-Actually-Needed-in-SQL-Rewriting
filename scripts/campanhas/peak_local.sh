#!/usr/bin/env bash
# PEAK SUPERVISOR — the mirror of offpeak.sh: run a FREE (all-local) command only during PEAK hours.
#
#   usage:  bash scripts/campanhas/peak_local.sh <command...>
#   e.g.:   bash scripts/campanhas/peak_local.sh bash scripts/campanhas/p1_local.sh mistral
#
# WHY
#   The provider doubles its price between 01:00-04:00 and 06:00-10:00 UTC, so the cloud cells pause
#   there (offpeak.sh). That leaves ~7h a day of idle machine. All-local cells make no API call at
#   all, so they are exactly the work that belongs in the expensive window: free, and otherwise the
#   hardware would be doing nothing.
#
#   Together the two supervisors alternate automatically: cloud off-peak, local on-peak.
#
# THE HANDOFF — why it stops EARLY
#   There is ONE server and ONE .env; the two cells cannot overlap. offpeak.sh resumes the cloud cell
#   the instant peak ends, so if the local cell were still shutting down at that moment, the cloud
#   chain would hit the "another cell is running" guard, abort, and take its supervisor down with it.
#   This one therefore yields STOP_MARGIN minutes before peak ends, leaving the machine clean.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
[ $# -ge 1 ] || { echo "usage: $0 <command...>"; exit 2; }

STOP_MARGIN=6        # minutes before peak ends
LOG="logs/campanhas/peak_$(echo "$*" | tr -cs 'a-zA-Z0-9' '_' | cut -c1-40).log"
mkdir -p logs/campanhas
log(){ echo "[$(date -u +%d/%m\ %H:%M) UTC | $(date +%H:%M) local] $*" | tee -a "$LOG"; }

# Peak (UTC): 01:00-04:00 and 06:00-10:00. "Usable" excludes the last STOP_MARGIN minutes so the
# handoff back to the cloud cell finds the machine free.
usable_peak(){
  local h m t
  h=$(date -u +%-H); m=$(date -u +%-M); t=$((h*60+m))
  { [ $t -ge 60 ]  && [ $t -lt $((240-STOP_MARGIN)) ]; } || \
  { [ $t -ge 360 ] && [ $t -lt $((600-STOP_MARGIN)) ]; }
}

CHILD=""
FROZEN=""
thaw(){   # give the cloud cell back — MUST run on every exit path, or it stays frozen forever
  [ -n "$FROZEN" ] || return 0
  for p in $FROZEN; do kill -CONT "$p" 2>/dev/null; done
  log "thawed the cloud cell (pid$FROZEN)"
  FROZEN=""
}
stop_child(){
  [ -n "$CHILD" ] || return 0
  kill -- -"$CHILD" 2>/dev/null || kill "$CHILD" 2>/dev/null
  sleep 5
  # Match on the interpreter path, never on the bare script name: `pkill -f run_matrix` also matches
  # the shell that is running the pkill, which killed the wrong process twice on 2026-08-17.
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix"); do kill "$p" 2>/dev/null; done
  CHILD=""
}
trap 'log "interrupted — stopping child"; stop_child; thaw; exit 130' INT TERM

log "###### PEAK SUPERVISOR (free/local work) ###### cmd: $*"
while true; do
  # ── FORA DO PICO: ocupar a janela SE as filas de nuvem já ACABARAM.
  #
  # POR QUÊ (28/08). Este supervisor dormia 17h/dia. Fazia sentido enquanto a nuvem tinha fila —
  # off-peak é onde a DeepSeek cobra METADE, então a janela é dela. Mas quando a fila de nuvem
  # esvazia, as 17h ficam OCIOSAS, e a fila local tem 907 runs a ~10/dia (7h de pico) = 91 dias,
  # contra um deadline de 40. Ocupar as janelas vazias leva o local de ~10 para ~35 runs/dia.
  #
  # ⛔⛔ O CRITÉRIO É "AS FILAS DE NUVEM MORRERAM", **não** "não há run_matrix agora". A diferença é
  #    uma CORRIDA que já nos custou uma célula: o `p1_cloud.sh` faz
  #    `ABORT: outra célula está rodando. Um servidor, um .env.` e sai com **exit 1** se achar a
  #    máquina ocupada — e a cadeia trata isso como perna concluída e SEGUE. Foi assim que a célula
  #    `0b pro` ficou em 9/20 em 19/08, e o `qwen raw` em 9/63 em 20/08: os dois mecanismos brigando.
  #    Esperar as CADEIAS morrerem elimina a corrida — se o processo da fila não existe, nenhuma
  #    célula de nuvem vai subir, e não há o que abortar.
  #
  # ⛔ NÃO é execução paralela. Rodar junto é inviável por CACHE DE PÁGINA, não por CPU:
  #    tpcds SF20 (25 GB) + imdb (8,7 GB) > 30 GB de RAM → o segundo banco despeja as páginas do
  #    primeiro, o run "quente" deixa de ser quente e o tempo dobra. Mede-se o cache, não a query.
  # ⛔⛔ SÓ OS NOSSOS (03/09). O `pgrep` por padrão pega TODO run_matrix da máquina — inclusive o da
  #     NUVEM. Em 02/09 isso produziu o pior modo de falha até agora: ao ceder a janela barata, o
  #     supervisor congelava também o processo da nuvem para quem estava cedendo. Resultado: os TRÊS
  #     run_matrix ficaram em estado `T` e **nada rodou por ~7 horas** (o log dizia
  #     "CONGELEI o local (pid 1280510)" — e o 1280510 era da nuvem).
  #     A regra: só é "nosso" o run_matrix que DESCENDE do $CHILD deste supervisor.
  nossos_run_matrix(){
    [ -z "${CHILD:-}" ] && return 0
    for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
      a="$p"
      while [ -n "$a" ] && [ "$a" -gt 1 ] 2>/dev/null; do
        [ "$a" = "$CHILD" ] && { echo "$p"; break; }
        a=$(ps -o ppid= -p "$a" 2>/dev/null | tr -d ' ')
      done
    done
  }

  cloud_queues_alive(){
    pgrep -f "campanhas/queue_c1c2.sh" >/dev/null 2>&1 && return 0
    pgrep -f "campanhas/queue_p1_cloud_rest.sh" >/dev/null 2>&1 && return 0
    pgrep -f "campanhas/offpeak.sh" >/dev/null 2>&1 && return 0
    # ⚠️ ADICIONADA 02/09. A `fila_nuvem.sh` passa a MAIOR parte do tempo esperando a máquina, e
    # nesse estado ela ainda não tem um `offpeak.sh` filho — então sem esta linha o supervisor
    # concluía "as filas de nuvem acabaram" e ocupava a janela barata com trabalho local, para
    # sempre. A fila de nuvem esperava o local acabar; o local nunca acabava. IMPASSE.
    # ⛔ PREFIXO, não nome exato (04/09) — 3ª vez que um PADRÃO de pgrep morde nesta campanha.
    #    "campanhas/fila_nuvem.sh" NÃO casa com "fila_nuvem_c4_primeiro.sh": o supervisor concluía
    #    "as filas de nuvem acabaram", ocupava a janela BARATA com trabalho local, e a cadeia de
    #    nuvem esperava o local soltar. IMPASSE, pela terceira vez. Casar pelo PREFIXO cobre
    #    qualquer variante futura (fila_nuvem_*.sh) sem precisar lembrar de vir aqui.
    pgrep -f "campanhas/fila_nuvem" >/dev/null 2>&1 && return 0
    return 1
  }
  if ! usable_peak; then
    if [ -n "$CHILD" ]; then
      kill -0 "$CHILD" 2>/dev/null || { CHILD=""; }
      # ⭐ CEDER A MÁQUINA (02/09) — o espelho da inversão de prioridade que já existia para o pico.
      #
      #   O QUE ESTAVA ERRADO: fora do pico, com o filho local vivo, este ramo só dormia. O local
      #   seguia até TERMINAR a célula, atravessando a janela barata inteira, enquanto a fila de
      #   nuvem esperava a máquina. Em 02/09 isso queimou ~3h20 de janela barata (1h11 de manhã +
      #   2h07 à tarde) — e a janela do local NÃO tem custo, a da nuvem tem.
      #
      #   A REGRA (usuária, 02/09): local nas janelas CARAS da nuvem (22-01 e 03-07), nuvem nas
      #   BARATAS (01-03 e 07-22). Fora do pico, quem manda é a nuvem — mas só se ela tiver fila.
      #   Sem fila de nuvem, o local continua ocupando a janela ociosa (comportamento de 28/08).
      #
      # ⛔⛔ BUG #22 (11/09/2026) — CEDER AGORA É **MATAR**, NÃO CONGELAR.
      #
      #   O comentário antigo aqui dizia "CONGELAR (SIGSTOP), nunca matar", porque matar fazia a perna
      #   sair com código != 0 e a cadeia tratava isso como conclusão (bug #19, o L8 em 6 de 21).
      #   ⭐ ESSA RAZÃO MORREU: hoje o `p1_local.sh` tem a guarda do `== done:` (sai 4 se o run_matrix
      #      não chegou ao fim) e o `perna()` das filas trata rc=4/143 como INTERRUPÇÃO — relança sem
      #      consumir tentativa. Matar virou seguro.
      #
      #   ⛔ E CONGELAR virou PERIGOSO. O `SIGSTOP` para o processo, mas NÃO preserva a posse do
      #      `.env`. Em 10/09 o L4 escreveu o `.env` às 06:13, foi congelado às 07:00, a cadeia de
      #      NUVEM sobrescreveu o `.env` às 11:21 com a config do P1-1a, e às 22:00 o L4 descongelou
      #      e rodou 4h51 **com a config da nuvem**: 25 registros foram parar em
      #      `suggestions_p1cloud_llama_flash`, o L4 declarou `exit 0` com 2 runs no próprio store, e
      #      gastou US$ 1,44 sem decisão. A guarda de matriz roda ANTES do congelamento e não é
      #      reconferida no descongelamento — o congelamento anda por baixo dela.
      #      ⚠️ Nenhum dado foi contaminado (o store da nuvem ficou homogêneo), mas uma noite inteira
      #         de janela local virou trabalho atribuído à célula errada.
      #
      #   ⭐ MATAR resolve os dois: ao voltar, o `perna()` relança a perna, o `p1_local.sh` REESCREVE
      #      o `.env` e a guarda de matriz roda de novo — posse restabelecida. E mata de brinde a
      #      morte-por-socket de runs congeladas por mais de ~40 min (medida 2x).
      #   ⚠️ Simetria: é exatamente o que o `offpeak.sh` já faz do lado da nuvem desde sempre
      #      ("PEAK started -> pausing (progress is kept; --resume continues)").
      if cloud_queues_alive; then
        log "fora do pico + fila de NUVEM esperando -> CEDENDO a máquina (mato o local; --resume retoma)"
        stop_child
        sleep 120; continue
      fi
      # sem fila de nuvem: descongela o NOSSO local (legado — não congelamos mais, mas pode haver
      # processo congelado de uma execução anterior) e deixa correr
      for p in $(nossos_run_matrix); do kill -CONT "$p" 2>/dev/null; done
      sleep 120; continue
    fi
    if cloud_queues_alive; then
      thaw; sleep 300; continue          # ainda há fila de nuvem: a janela barata é dela
    fi
    thaw
    log "fora do pico e as filas de NUVEM ACABARAM -> ocupando a janela ociosa com trabalho local"
    # ⛔⛔ BUG #24 (14/09/2026) — DOIS defeitos neste ramo, os dois corrigidos aqui.
    #
    #   (1) SAÍDA INVISÍVEL. Este ramo lançava `"$@" &` SEM redirecionar, enquanto o ramo do pico usa
    #       `setsid "$@" >>"$LOG.cmd" 2>&1 &`. ⇒ Tudo que a célula imprimia na janela ociosa ia para o
    #       descritor do supervisor, que roda sob `nohup >/dev/null`. Em 14/09 a célula morreu em
    #       segundos, repetidamente, e NÃO HAVIA ONDE OLHAR — tive de rodar o `p1_local.sh` à mão para
    #       descobrir o motivo. É a mesma família do bug #19: execução invisível.
    #
    #   (2) ⛔ LAÇO DE RELANÇAMENTO INFINITO — o pior dos dois. Este ramo lançava o filho, dormia 120 s
    #       e dava `continue` — **sem NUNCA examinar o código de saída**. A checagem de conclusão
    #       (`wait $CHILD` → "free cell COMPLETED" → `exit`) só existe no fim do laço, alcançável no
    #       ramo do PICO. Resultado: na janela ociosa, uma célula que TERMINA com sucesso é relançada
    #       a cada 2 minutos, para sempre, e a cadeia NUNCA avança para a perna seguinte.
    #       ⚠️ Só apareceu agora porque a fila de nuvem esvaziou em 10/09 (este ramo passou a ser o
    #          caminho normal) E porque o L4 fechou (a célula passou a terminar em 1 segundo).
    #          Com células longas o defeito ficava escondido.
    #
    #   ✅ Agora: redireciona como o outro ramo, e trata a conclusão do mesmo jeito — se o filho sai,
    #      o supervisor sai com o MESMO código, e o `perna()` da cadeia decide (0 = perna cumprida e
    #      segue para a próxima; != 0 = tentativa/interrupção).
    setsid "$@" >>"$LOG.cmd" 2>&1 & CHILD=$!
    sleep 120
    if ! kill -0 "$CHILD" 2>/dev/null; then
      wait "$CHILD" 2>/dev/null; rc=$?
      [ "$rc" -eq 0 ] && { log "###### free cell COMPLETED (janela ociosa) ######"; thaw; exit 0; }
      log "###### free cell EXITED with status $rc (janela ociosa) — stopping supervisor ######"; thaw; exit "$rc"
    fi
    continue
  fi
  # ⛔⛔ DESCONGELAR O NOSSO LOCAL AO VOLTAR AO PICO (05/09) — o congelamento era de MÃO ÚNICA.
  #
  #   O BUG: em 03/09 acrescentei o congelamento do local ao ceder a janela barata para a nuvem
  #   (SIGSTOP). Mas o DESCONGELAMENTO só existia no ramo "fora do pico E sem fila de nuvem".
  #   Quando o PICO volta, este laço encontra `CHILD` VIVO — o `p1_local.sh` nunca morreu, só o
  #   `run_matrix` neto está parado — pula o bloco de baixo inteiro e dorme.
  #   ⇒ O local ficava congelado PARA SEMPRE. Em 05/09 custou **1 dia e 3h30** de processo parado,
  #     das quais ~2h de JANELA DE PICO ociosa (a nuvem pausou às 22:00 e ninguém assumiu).
  #   ⚠️ Modo de falha SILENCIOSO: `ps` mostra tudo "vivo", o health check passa 6/6, e o único
  #     sinal é o estado `T` do processo — que ninguém olha por hábito.
  for p in $(nossos_run_matrix); do
    if [ "$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')" = "T" ]; then
      kill -CONT "$p" 2>/dev/null && log "pico -> DESCONGELEI o local (pid $p)"
    fi
  done
  if [ -z "$CHILD" ]; then
    # ⭐ PRIORITY INVERSION (19/08). This used to WAIT while a cloud cell was running — and on the
    # night of 18-19/08 that cost the ENTIRE peak window: the cloud chain starts each leg back to
    # back, so there was always a run_matrix alive at the instant we checked, and the free work
    # never got a single minute. Peak hours belong to local work: the cloud should not be running
    # here at all (it is paying double).
    #
    # ⛔⛔ BUG #22 (11/09/2026) — AQUI TAMBÉM: MATAR, não congelar. Este é o ESPELHO do defeito.
    #
    #   O texto antigo dizia "congelamos em vez de matar, porque matar faz a perna sair != 0 e a
    #   cadeia segue" (o caso da célula 0b em 9/20, 19/08). ⭐ Essa razão morreu duas vezes:
    #     (a) o `offpeak.sh` ABSORVE o código — ele relança o comando quando a janela volta, e a
    #         cadeia de nuvem só vê um código quando o PRÓPRIO offpeak sai;
    #     (b) o `p1_cloud.sh` ganhou a guarda do `== done:` (sai 4 se não concluiu).
    #
    #   ⛔ E congelar a nuvem tem o MESMO risco que congelar o local: o `SIGSTOP` não preserva a
    #      posse do `.env`. Congelada aqui, a célula de nuvem descongela depois que o local já
    #      reescreveu o `.env` — e passa a rodar com a config do LOCAL. É o bug #22 na direção
    #      oposta; em 10/09 ele mordeu no sentido local←nuvem e custou uma noite de janela.
    #
    #   ⚠️ Na prática isto é redundante quando a nuvem está sob `offpeak.sh` (ele já mata o próprio
    #      filho ao entrar o pico). Fica como rede para cadeia de nuvem lançada sem o supervisor.
    FROZEN=""
    for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix"); do
      kill "$p" 2>/dev/null && log "peak -> MATEI a célula de nuvem (pid $p) — o offpeak relança com --resume"
    done
    log "peak -> (re)starting the free cell"
    setsid "$@" >>"$LOG.cmd" 2>&1 &
    CHILD=$!
  fi
  if ! kill -0 "$CHILD" 2>/dev/null; then
    wait "$CHILD" 2>/dev/null; rc=$?
    [ "$rc" -eq 0 ] && { log "###### free cell COMPLETED ######"; thaw; exit 0; }
    log "###### free cell EXITED with status $rc — stopping supervisor ######"; thaw; exit "$rc"
  fi
  sleep 60
done
