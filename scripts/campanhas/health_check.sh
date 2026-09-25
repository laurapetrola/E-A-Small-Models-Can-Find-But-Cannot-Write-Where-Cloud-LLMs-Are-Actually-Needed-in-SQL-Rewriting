#!/usr/bin/env bash
# DIAGNÓSTICO DA CAMPANHA — roda em segundos, detecta os erros que já nos custaram runs.
#   uso: bash scripts/campanhas/health_check.sh
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
ok=0; bad=0
chk(){ if [ "$2" -eq 0 ]; then echo "  ✅ $1"; ok=$((ok+1)); else echo "  ⛔ $1 → $3"; bad=$((bad+1)); fi; }

# 1. mais de um run_matrix = duas células medindo ao mesmo tempo (uma corrompe a outra)
n=$(ps -eo args --no-headers | grep -v "^/bin/bash -c\|^bash -c" | grep -c "[r]un_matrix.py" || true)
chk "run_matrix: $n (esperado 0 ou 1)" "$([ "${n:-0}" -le 1 ] && echo 0 || echo 1)" "DUAS células rodando — os tempos de uma contaminam a outra"

# 2. queries órfãs — o erro de 23/08
PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
orf=0
for port in 5435 5437; do
  (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null || continue
  exec 3>&-
  c=$(PGPASSWORD="$PW" psql -h localhost -p $port -U postgres -d postgres -tAc \
      "select count(*) from pg_stat_activity where state='active' and backend_type='client backend'
       and query not like '%pg_stat%' and now()-query_start > interval '10 minutes'" 2>/dev/null | tr -d ' ')
  orf=$((orf+${c:-0}))
done
chk "queries órfãs (>10 min): $orf" "$([ "$orf" -eq 0 ] && echo 0 || echo 1)" "rodar scripts/campanhas/kill_orphans.sh — elas DESTROEM a medição"

# 3. filas duplicadas disputando a máquina
for q in queue_c1c2 queue_p1_cloud_rest queue_p1_peak queue_plan_verdict; do
  # ⚠️ excluir wrappers `bash -c`: a linha de comando de QUEM CHAMA este script costuma conter o
  # nome da fila, e o ps a enxerga — falso positivo da mesma família da auto-referência do pgrep.
  c=$(ps -eo args --no-headers | grep -v "^/bin/bash -c\|^bash -c" | grep -c "[${q:0:1}]${q:1}.sh" || true)
  [ "${c:-0}" -gt 1 ] && { echo "  ⛔ $q: $c instâncias → matar as duplicadas"; bad=$((bad+1)); }
done
[ "$bad" -eq 0 ] && echo "  ✅ sem filas duplicadas" && ok=$((ok+1))

# 4. servidor e bancos
ss -ltn 2>/dev/null | grep -q ':8000' && s=0 || s=1
chk "servidor uvicorn na 8000" "$s" "nenhuma célula consegue rodar"
c=$(docker ps --format '{{.Names}}' | grep -cE "tpcds|imdb" || true)
chk "contêineres de banco no ar: $c" "$([ "${c:-0}" -ge 2 ] && echo 0 || echo 1)" "docker start tpcds tpcds-pg-sf1 ..."

# 5. FILA DO SCRIPT × FILA DOCUMENTADA (25/08)
#
# ⚠️ POR QUE EXISTE: em 25/08 a divergência apareceu TRÊS vezes no mesmo dia, sempre na mesma direção
#    — a célula era documentada no `next_steps` e não era enfileirada no script (L1b, L3b, M2). E uma
#    vez na direção oposta: as células `LA` e `LB` rodavam no script sem estar no planejamento, o que
#    é pior — consomem janela de pico e não entram em estimativa de calendário nenhuma.
#
#    **Documentar a fila NÃO é enfileirar.** Esta checagem existe para que a próxima vez seja pega em
#    segundos, e não descoberta por acaso três horas depois.
Q=scripts/campanhas/queue_p1_peak.sh
N=documentation/next_steps.md
if [ -f "$Q" ] && [ -f "$N" ]; then
  falta=0
  while read -r id; do
    # o rótulo da fila (L1, L3b, M2, LA…) é declarado na descrição do próprio `step`
    lbl=$(grep -m1 "^step $id " "$Q" | sed -n 's/.*" *\([A-Z][A-Z]*[0-9][0-9a-z]*\|[A-Z][A-Z]\) .*/\1/p')
    # ⚠️ item SEM rótulo é problema, não motivo para pular: era assim que `LA` e `LB` passavam
    #    despercebidos. Falha alto.
    [ -z "$lbl" ] && { echo "  ⛔ item «$id» não declara rótulo de fila na descrição — invisível à checagem"; falta=$((falta+1)); continue; }
    grep -q "\*\*$lbl\*\*\|| $lbl |" "$N" || { echo "  ⛔ item «$id» ($lbl) está no SCRIPT e não no next_steps"; falta=$((falta+1)); }
  done < <(grep -oP '^step \K\w+' "$Q")
  if [ "$falta" -eq 0 ]; then
    echo "  ✅ fila do script × next_steps em dia ($(grep -c '^step ' "$Q") itens)"
    ok=$((ok+1))
  else
    bad=$((bad+falta))
  fi
fi

echo
[ "$bad" -eq 0 ] && echo "  ✅ CAMPANHA SAUDÁVEL ($ok checagens)" || echo "  ⛔ $bad PROBLEMA(S) — resolver antes de seguir"
exit 0
