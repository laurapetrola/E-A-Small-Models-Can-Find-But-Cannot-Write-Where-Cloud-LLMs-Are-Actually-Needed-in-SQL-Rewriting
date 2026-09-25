#!/usr/bin/env bash
# VIGIA DE METADATA LOCK — MySQL (04/09).
#
#   POR QUE EXISTE. Em 04/09 o mesmo deadlock parou a campanha TRÊS vezes; uma delas custou 2h15 sem
#   um único run. O padrão: uma conexão ociosa no pool retém `SHARED_READ` de uma tabela, o
#   `CREATE INDEX`/`DROP INDEX` do simulate_index entra em "Waiting for table metadata lock", e TUDO
#   atrás dele para — ANALYZE, EXPLAIN, os SELECT de baseline.
#   ⛔ O dano é o pior tipo: a célula NÃO falha com erro, ela acumula `timeout de baseline`, que não
#      conta como run e PARECE limite do modelo. É infraestrutura travada disfarçada de resultado.
#
#   ⭐ CAUSA-RAIZ já corrigida em `src/connections.py` (pool_recycle=600 + pool_pre_ping), mas isso só
#      vale a partir do PRÓXIMO restart do servidor — o uvicorn em execução carregou o módulo antes.
#      Esta vigia é a ponte até lá, e a rede de segurança depois.
#
#   REGRA DE MORTE — deliberadamente estreita, para nunca matar trabalho:
#     mata uma sessão SÓ SE ela estiver (a) OCIOSA (`Sleep`), (b) detendo lock CONCEDIDO, e
#     (c) houver ALGUÉM ESPERANDO por lock naquele instante. Sem (c) não há dano a desfazer.
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/watchdog_locks.log
INTERVALO="${WATCHDOG_INTERVAL_S:-120}"
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
log "###### VIGIA DE LOCKS no ar (checa a cada ${INTERVALO}s) ######"
while true; do
  for cont in imdb-mysql tpcds-mysql tpcds-mysql-sf1; do
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$cont" || continue
    pw=$(docker inspect "$cont" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
         | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
    [ -z "$pw" ] && continue
    # (c) alguém esperando?
    esperando=$(timeout 15 docker exec "$cont" mysql -uroot -p"$pw" -N -e \
      "select count(*) from information_schema.processlist where state like '%metadata lock%'" 2>/dev/null)
    [ "${esperando:-0}" -eq 0 ] 2>/dev/null && continue
    # (a)+(b) donos ociosos do lock
    ids=$(timeout 15 docker exec "$cont" mysql -uroot -p"$pw" -N -e \
      "select distinct p.id from performance_schema.metadata_locks m
         join performance_schema.threads t on t.thread_id = m.OWNER_THREAD_ID
         join information_schema.processlist p on p.id = t.processlist_id
        where m.LOCK_STATUS='GRANTED' and p.command='Sleep'" 2>/dev/null)
    for id in $ids; do
      timeout 10 docker exec "$cont" mysql -uroot -p"$pw" -e "KILL $id;" >/dev/null 2>&1 \
        && log "$cont: ${esperando} esperando lock -> encerrada a sessão OCIOSA $id"
    done
  done
  sleep "$INTERVALO"
done
