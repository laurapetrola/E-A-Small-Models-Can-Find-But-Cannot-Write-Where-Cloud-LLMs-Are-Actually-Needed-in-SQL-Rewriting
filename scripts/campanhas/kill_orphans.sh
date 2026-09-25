#!/usr/bin/env bash
# Mata queries ÓRFÃS deixadas por um run_matrix encerrado. CHAMAR SEMPRE ANTES DE UMA CÉLULA.
#
# ⚠️ POR QUE ISTO EXISTE (23/08 — custou 38 runs)
#   Matar o `run_matrix` NÃO mata a query no banco: o processo Python morre, o backend do PostgreSQL
#   continua executando, e sem `statement_timeout` na sessão ele roda para sempre. Em 23/08 achei
#   quatro `EXPLAIN ANALYZE` órfãos — o mais velho com **27,7 HORAS** — e três `ANALYZE` bloqueados
#   atrás deles.
#
#   O ESTRAGO, medido: nas 38 runs de PostgreSQL que rodaram na janela contaminada,
#     · 50% terminaram em TIMEOUT   (contra 5% fora da janela — DEZ VEZES mais)
#     · só 23% tiveram ganho MEDIDO (contra 60% fora)
#   O tempo de execução É a medição; competir por CPU com quatro queries pesadas a destrói.
#   A célula C1 (27 runs) foi perdida inteira e teve de ser refeita.
#
#   ⚠️ Isto também põe em dúvida o diagnóstico de "cache frio" de 21/08 (a `q69` medindo >45 s numa
#      run e 722 ms em outra): pode ter sido CONTENÇÃO com órfãs, não cache. Os dois sintomas são
#      idênticos e não há como separá-los retroativamente.
#
# ⚠️ BUG CORRIGIDO 24/08 — ÓRFÃ COM PARALELISMO SOBREVIVIA À LIMPEZA
#   O filtro era `backend_type='client backend'`, e um worker paralelo tem
#   `backend_type='parallel worker'` — ou seja, os workers NUNCA eram tocados. Terminar só a líder
#   não resolve: ela fica presa em espera `IPC`, esperando workers que ninguém cancelou, e segue
#   consumindo CPU. Visto duas vezes em 24/08; a limpeza reportava "4 terminadas" e deixava um grupo
#   inteiro vivo. É a explicação mais provável da órfã de 27,7 HORAS — ela não resistiu por acaso,
#   ela era imune ao script.
#
#   A CORREÇÃO: terminar o GRUPO. O PostgreSQL 13+ expõe `leader_pid` em `pg_stat_activity`, então
#   dá para pegar os workers pela líder. Ordem importa — workers PRIMEIRO, líder depois: matar a
#   líder antes deixa os workers órfãos de novo, sem ninguém para os coletar.
set -u
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
total=0
for port in 5435 5437 5436; do
  (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null || continue
  exec 3>&-
  for i in 1 2 3; do
    n=$(PGPASSWORD="$PW" psql -h localhost -p $port -U postgres -d postgres -tAc \
      "select count(*) from pg_stat_activity where state='active' and backend_type='client backend'
       and query not like '%pg_stat%' and now()-query_start > interval '2 minutes'" 2>/dev/null | tr -d ' ')
    [ "${n:-0}" -eq 0 ] && break
    # WORKERS FIRST, then the leader (see the note at the top: killing the leader first only orphans
    # the workers again). `leader_pid` links a parallel worker back to the client backend that owns it.
    PGPASSWORD="$PW" psql -h localhost -p $port -U postgres -d postgres -tAc \
      "select pg_terminate_backend(w.pid) from pg_stat_activity w
        where w.leader_pid in (select pid from pg_stat_activity
                                where state='active' and backend_type='client backend'
                                  and query not like '%pg_stat%'
                                  and now()-query_start > interval '2 minutes')" >/dev/null 2>&1
    sleep 1
    PGPASSWORD="$PW" psql -h localhost -p $port -U postgres -d postgres -tAc \
      "select pg_terminate_backend(pid) from pg_stat_activity where state='active'
       and backend_type='client backend' and query not like '%pg_stat%'
       and now()-query_start > interval '2 minutes'" >/dev/null 2>&1
    total=$((total+n)); sleep 3
  done
done

# ══════════════════════════════════════════════════════════════════════
# MySQL — ADICIONADO 31/08. O script cobria só PostgreSQL, e o MySQL tem o MESMO problema.
#
# O QUE ACONTECEU: ao parar uma célula de MySQL no meio, ficaram no servidor um
# `CREATE INDEX _athena_sim_...` de **2h25**, mais um `EXPLAIN FORMAT=JSON` e um
# `ANALYZE TABLE store_sales` de ~2h enfileirados atrás dele. O `ANALYZE` da célula SEGUINTE entrou
# na fila e ficou preso — o C4 pareceu travado no startup por 4 minutos, e teria ficado indefinidamente.
#
# ⚠️ Pior que travar: órfã competindo por CPU **contamina a medição de tempo**, que é a nossa métrica.
#    É o mesmo estrago documentado no PostgreSQL em 23/08 (50% de timeout na janela contaminada,
#    contra 5% fora).
#
# ⛔ ATENÇÃO ao `CREATE INDEX`: o advisor cria índices de simulação com prefixo `_athena_sim_`. Um
#    CREATE INDEX morto no meio pode deixar índice PELA METADE — por isso, depois de matar, o script
#    confere e DERRUBA qualquer `_athena_sim_%` remanescente. Índice sobrando altera o plano das runs
#    seguintes SEM aparecer em lugar nenhum.
# ⚠️ LIMIAR 1800 s (30 min), NÃO 120 — corrigido 31/08, no mesmo dia em que foi escrito.
#   Rodei a limpeza com o C4 ATIVO e ela matou um `CREATE INDEX` LEGÍTIMO dele: o índice caiu para
#   'estimated', e o canário abortou a célula (corretamente — nenhum run contaminado gravou).
#   No MySQL uma query de campanha passa de 2 min com facilidade: `CREATE INDEX` em `store_sales`,
#   `EXPLAIN ANALYZE` no SF20. 120 s distingue órfã de trabalho? NÃO. 30 min sim — a órfã real que
#   motivou isto tinha **2h25**.
# ⛔ E a regra que eu violei: `kill_orphans` roda ANTES de uma célula, NUNCA durante.
MYPW=$(docker inspect tpcds-mysql --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
for cont in tpcds-mysql tpcds-mysql-sf1 imdb-mysql; do
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$cont" || continue
  pw="$MYPW"; [ "$cont" = imdb-mysql ] && pw=mysql
  timeout 20 docker exec "$cont" mysqladmin ping -uroot -p"$pw" >/dev/null 2>&1 || continue
  ids=$(timeout 20 docker exec "$cont" mysql -uroot -p"$pw" -N -e \
    "select id from information_schema.processlist
      where command<>'Sleep' and info is not null
        and info not like '%processlist%' and time > 1800" 2>/dev/null)
  for id in $ids; do
    timeout 15 docker exec "$cont" mysql -uroot -p"$pw" -e "KILL $id;" >/dev/null 2>&1 && total=$((total+1))
  done

    # ⛔⛔ SESSÕES OCIOSAS QUE SEGURAM METADATA LOCK (04/09) — o filtro acima NÃO as pega, porque ele
    #    exige `command<>'Sleep'`, e estas estão exatamente em `Sleep`.
    #
    #    O caso, que apareceu TRÊS vezes em 04/09: uma conexão dorme no pool com transação de leitura
    #    aberta e retém o metadata lock da tabela. O `DROP INDEX _athena_sim_...` entra em
    #    "Waiting for table metadata lock" e ARRASTA a fila inteira — ANALYZE, EXPLAIN, os SELECT de
    #    baseline. A célula não falha com erro: acumula **timeout de baseline**, que não conta como
    #    run e PARECE limite do modelo. No imdb-mysql custou 2h15 sem um único run novo.
    #    ⭐ Causa-raiz corrigida em `src/connections.py` (pool_recycle=600s); isto é a rede de
    #      segurança para o que já estiver preso quando a célula começar.
    #    ⚠️ Só mata quem está OCIOSO (`Sleep`) há mais de 5 min E detém lock concedido.
    idle=$(timeout 20 docker exec "$cont" mysql -uroot -p"$pw" -N -e \
      "select distinct p.id from performance_schema.metadata_locks m
         join performance_schema.threads t on t.thread_id = m.OWNER_THREAD_ID
         join information_schema.processlist p on p.id = t.processlist_id
        where m.LOCK_STATUS='GRANTED' and p.command='Sleep' and p.time > 300" 2>/dev/null)
    for id in $idle; do
      timeout 15 docker exec "$cont" mysql -uroot -p"$pw" -e "KILL $id;" >/dev/null 2>&1 \
        && { total=$((total+1)); echo "  ⚠️ $cont: sessão OCIOSA $id segurando metadata lock — encerrada"; }
    done
  # índices de simulação sobrando (CREATE INDEX morto no meio)
  for db in tpcds imdb; do
    idx=$(timeout 20 docker exec "$cont" mysql -uroot -p"$pw" -N -e \
      "select distinct concat(table_name,'|',index_name) from information_schema.statistics
        where table_schema='$db' and index_name like '\_athena\_sim\_%'" 2>/dev/null)
    for pair in $idx; do
      t="${pair%%|*}"; i="${pair##*|}"
      timeout 20 docker exec "$cont" mysql -uroot -p"$pw" -e "DROP INDEX \`$i\` ON $db.\`$t\`;" >/dev/null 2>&1 \
        && echo "  ⚠️ índice de simulação órfão removido: $db.$t.$i"
    done
  done
done

[ "$total" -gt 0 ] && echo "  ⚠️ $total query(ies) órfã(s) terminada(s) antes de iniciar a célula"
exit 0
