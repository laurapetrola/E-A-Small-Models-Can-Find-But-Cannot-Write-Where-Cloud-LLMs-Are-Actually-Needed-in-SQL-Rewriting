#!/usr/bin/env bash
# RETOMAR A CAMPANHA DEPOIS DE UM REBOOT — um comando só.
#
#   POR QUE EXISTE. Estava na lista de pendências desde 02/09 e cobrou o preço em 04/09: a máquina
#   reiniciou, TODOS os contêineres caíram, e nada volta sozinho. Sem isto, retomar é uma sequência
#   manual de ~8 passos em que esquecer um deles falha em SILÊNCIO — em especial o ANALYZE, cuja
#   ausência deixa o planner achatado e faz a original travar no S=1, gravando `orig_ms=None` e
#   ZERO lands em tudo. Parece "o modelo ficou ruim"; é estatística faltando.
#
#   ⛔ O QUE ELE NÃO FAZ: escolher célula. Ele restaura a INFRAESTRUTURA e relança as duas filas, que
#      já sabem onde pararam (`--resume` + marcadores `.DONE`). Nenhum dado é reprocessado.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/resume_all.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }

log "###### RETOMANDO A CAMPANHA ######"

# 1 ── contêineres. ⚠️ O `tpcds` (PG SF20, 25 GB) é o mais lento a ficar pronto; esperar por ELE.
log "1/5 subindo contêineres"
for c in tpcds tpcds-pg-sf1 tpcds-mysql tpcds-mysql-sf1 imdb imdb-mysql; do
  docker start "$c" >/dev/null 2>&1 && log "   ▶ $c" || log "   ⛔ falhou: $c"
done

log "2/5 esperando os bancos aceitarem conexão (até 5 min)"
for i in $(seq 1 30); do
  # ⚠️ A PORTA IMPORTA (04/09): o postgres do `tpcds` escuta na **5435** DENTRO do contêiner
  #    (mapeamento 5435->5435), não na 5432 padrão. Um `pg_isready` sem `-p` falha sempre e o laço
  #    gira os 5 minutos inteiros à toa — foi o que aconteceu na primeira execução deste script.
  ok=1
  docker exec tpcds pg_isready -q -p 5435 2>/dev/null || ok=0
  docker exec imdb  pg_isready -q 2>/dev/null || ok=0
  [ "$ok" = 1 ] && { log "   ✅ PostgreSQL pronto"; break; }
  sleep 10
done
for c in tpcds-mysql imdb-mysql; do
  P=$(docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
  docker exec "$c" mysqladmin -uroot -p"$P" ping >/dev/null 2>&1 && log "   ✅ $c" || log "   ⚠️ $c não respondeu"
done

# 3 ── ollama. Sem ele o FIND local não roda e a célula falha em cadeia.
log "3/5 conferindo o ollama"
curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1 \
  && log "   ✅ ollama no ar" \
  || log "   ⛔ OLLAMA FORA — o FIND local não vai rodar. Subir antes de seguir."

# 4 ── ANALYZE. ⛔ NÃO PULAR: as células fazem o seu próprio ANALYZE, mas um banco recém-subido com
#      estatísticas frias faz a PRIMEIRA medição sair errada, e ela é o baseline de tudo.
log "4/5 ANALYZE nas instâncias (planner frio depois de reboot)"
.venv/bin/python scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "   ✅ ANALYZE ok" || log "   ⚠️ ANALYZE falhou — conferir antes de confiar em tempo"

# 5 ── filas. Cada uma sabe onde parou; nada é reprocessado.
#      ⚠️ Só sobe o que NÃO estiver vivo — relançar por cima cria duas cadeias disputando o mesmo
#      `.env`, que é a família de bug que mais custou tempo nesta campanha.
log "5/5 relançando as filas"
# ⛔ CADEIA LOCAL ATUAL (09/09): `fila_local_pendentes.sh`. A `queue_p1_peak.sh` era uma passada
#    ÚNICA, já atravessada, e apontar para ela depois de um reboot relançaria células ERRADAS.
#    Corrigido em 09/09 — este arquivo ainda apontava para a antiga quando a máquina reiniciou.
# ⛔ CADEIA LOCAL ATUAL (12/09): `fila_local_v4.sh` — L4 → L5 → M2b → W2 → LB.
#    ⚠️ ESTE PONTEIRO JÁ FICOU VELHO DUAS VEZES (09/09 apontava para a `queue_p1_peak.sh` morta;
#       11/09 para a `fila_local_pendentes.sh`). Ao trocar de cadeia, TROCAR AQUI TAMBÉM — senão o
#       próximo reboot relança células erradas.
if pgrep -f "campanhas/fila_local_v4.sh" >/dev/null; then
  log "   ⏭️  fila LOCAL já viva"
else
  nohup bash scripts/campanhas/fila_local_v4.sh >/dev/null 2>&1 &
  log "   ▶ fila LOCAL · fila_local_v4.sh (pid $!)"
fi
# ⛔⛔ FILA DE NUVEM: NÃO RELANÇAR (12/09/2026).
#    A `fila_nuvem_1c_1a.sh` CONCLUIU em 10/09 19:32 — P1-1c e P1-1a fecharam, e com elas a coluna
#    aided-cloud (4 modelos). Relançá-la NÃO seria inofensivo: o `p1_cloud.sh` roda um CANÁRIO PAGO
#    (~US$ 0,13 cada) antes de descobrir que não há o que fazer. Não há cobertura de nuvem pendente
#    — o ds-llama ficou de fora por decisão (alcance 1 no raw, cobertura cara).
#    ✅ Para religar nuvem no futuro: criar a cadeia nova e apontar ESTE bloco para ela.
log "   ⏭️  fila NUVEM: nada pendente (a cobertura fechou em 10/09) — não relançando"

sleep 5
# 6 ── vigia de metadata lock. ⛔ Sem ela a nuvem trava sozinha: 13 intervenções em 2 dias, uma
#      delas evitando 2h15 de máquina parada. O conserto de causa-raiz (pool_recycle) já está em
#      `src/connections.py`, mas a vigia continua como rede de segurança.
if pgrep -f "campanhas/watchdog_locks.sh" >/dev/null; then
  log "6/6   ⏭️  vigia de locks já viva"
else
  nohup bash scripts/campanhas/watchdog_locks.sh >/dev/null 2>&1 &
  log "6/6   ▶ vigia de metadata lock (pid $!)"
fi

log "###### PRONTO — conferir com scripts/campanhas/health_check.sh ######"
