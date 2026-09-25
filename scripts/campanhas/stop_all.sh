#!/usr/bin/env bash
# PARADA SEGURA — o único jeito certo de interromper a campanha.
#
# ⚠️ POR QUE EXISTE (23/08 — custou 38 runs e a célula C1 inteira)
#   Matar o `run_matrix` na mão NÃO mata a query no banco. O Python morre, o backend do PostgreSQL
#   continua executando, e sem `statement_timeout` roda para sempre. Nos dias 21-23/08 eu interrompi
#   a campanha várias vezes (pausas, reordenações, correções) e cada uma deixou órfãs. Encontrei
#   quatro `EXPLAIN ANALYZE` vivos, o mais velho com **27,7 HORAS**, e três `ANALYZE` travados atrás
#   deles. Nas 38 runs que rodaram na janela contaminada, **50% deram TIMEOUT** contra 5% fora dela.
#
#   ORDEM CORRETA (e é ela que este script garante):
#     1. as FILAS primeiro — senão elas relançam a célula que acabamos de matar
#     2. o run_matrix
#     3. as queries ÓRFÃS no banco  ← o passo que eu esquecia
#
# ⚠️ RETOMAR EXIGE RELANÇAR AS **DUAS** FILAS (23/08 — segunda lição, custou uma janela de pico)
#   A campanha roda em DOIS supervisores que se alternam sozinhos: `offpeak.sh` toca a NUVEM fora do
#   pico, `peak_local.sh` toca o LOCAL dentro dele. Este script derruba os dois. Ao retomar eu
#   relancei só o da nuvem, e a fila local ficou parada de 09:12 às 23:18 — perdemos quase toda a
#   janela 22:00-01:00, que é justamente o horário em que o local deveria estar trabalhando.
#   Por isso o script IMPRIME os dois comandos de retomada no fim. Não confiar na memória.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
echo "1/3 · encerrando as filas..."
for pid in $(ps -eo pid,args --no-headers | grep -E "[q]ueue_[a-z0-9_]*\.sh|[p]eak_local\.sh|[o]ffpeak\.sh" | awk '{print $1}'); do
  kill -9 "$pid" 2>/dev/null
done
sleep 2
echo "2/3 · encerrando as células..."
for pid in $(ps -eo pid,args --no-headers | grep -E "[p]1_local\.sh|[p]1_cloud\.sh|[i]mdb_battery\.sh|[f]inal_battery\.sh|[c]ontrol_monolithic\.sh" | awk '{print $1}'); do
  kill -9 "$pid" 2>/dev/null
done
sleep 1
for pid in $(ps -eo pid,args --no-headers | grep "[r]un_matrix.py" | awk '{print $1}'); do kill "$pid" 2>/dev/null; done
sleep 4
echo "3/3 · limpando queries órfãs no banco..."
bash scripts/campanhas/kill_orphans.sh
echo
bash scripts/campanhas/health_check.sh

# ── COMO RETOMAR ────────────────────────────────────────────────────────────────────────────────
# Impresso, e não só comentado, porque o erro de 23/08 foi exatamente esquecer a segunda linha.
cat <<'RESUME'

═══ PARA RETOMAR — lançar AS DUAS (elas se alternam sozinhas, nunca colidem) ═══

  ☁️  nuvem (roda FORA do pico):
      setsid --fork bash scripts/campanhas/queue_c1c2.sh >/dev/null 2>&1 &

  🌙  local (roda DENTRO do pico, é de graça):
      setsid --fork bash scripts/campanhas/queue_p1_peak.sh >/dev/null 2>&1 &

  Pico = 01:00-04:00 e 06:00-10:00 UTC (22:00-01:00 e 03:00-07:00 local).
  Fora do pico o DeepSeek custa METADE, e 96% da conta é token de saída.
  Cada supervisor espera a sua janela: lançar os dois agora é seguro e é o estado correto.

  Conferir depois de lançar:  bash scripts/campanhas/health_check.sh
RESUME
