#!/usr/bin/env bash
# FILA DA NUVEM — FECHAR O C4 PRIMEIRO (06/09).
#
#   POR QUE ESTA CADEIA EXISTE. O C4 saiu com `exit=2` às 07:02 — a guarda de 25/08 funcionando:
#   "NENHUM run foi medido". ⛔ Mas a cadeia anterior SEGUIU para o P1-1b (cobertura) mesmo assim,
#   deixando uma MANCHETE incompleta para trás. Isso inverte a regra da usuária:
#       "se o saldo apertar, o que fica de fora é COBERTURA, nunca MANCHETE."
#   ⚠️ LIÇÃO DE DESENHO: propagar o exit code não basta se a CADEIA não age sobre ele. O
#      `p1_cloud.sh` avisou corretamente ("A CÉLULA NÃO CONCLUIU (rc=2)") e a cadeia registrou
#      `C4 exit=2` — e seguiu. Guarda que só LOGA não é guarda.
#
#   ⚠️ A CAUSA DO exit=2 NÃO FOI O MODELO: o contêiner `tpcds-mysql` **CRASHOU às 06:31**
#      (`Exited (2)`, InnoDB "difficult to find free blocks in the buffer pool"). Sem banco, o
#      `schema_analyst` não reconstrói o `schema_cache.json` e toda run devolve HTTP 500 em 0s.
#      As 10 falhas são de INFRAESTRUTURA — nenhuma é evidência sobre o modelo.
#      ✅ Contêiner reiniciado 06/09 09:50; `store_sales` com 57.598.932 linhas, íntegro.
#
#   ⛔ E o crash passou 6 HORAS despercebido porque nada o detectava: o `health_check.sh` conta
#      "contêineres no ar", e um contêiner `Exited` simplesmente NÃO É CONTADO — some da conta em
#      vez de disparar alarme. Por isso a guarda `_banco_ok` abaixo existe.
#
#   FALTAM 10 runs em 4 queries: q27 (1) · q63 (3) · q85 (3) · q96 (3).
set -u
cd "$(dirname "$0")/../.." || exit 1
LOG=logs/campanhas/fila_nuvem_c4_fechar.log
log(){ echo "[$(date '+%d/%m %H:%M:%S')] $*" | tee -a "$LOG"; }
O=scripts/campanhas/offpeak.sh
P=scripts/campanhas/p1_cloud.sh

_saldo(){
  local k; k=$(grep -m1 '^KEY_DEEPSEEK=' .env | cut -d= -f2- | tr -d '"'"'"' ')
  curl -s --max-time 20 https://api.deepseek.com/user/balance -H "Authorization: Bearer $k" \
    | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['balance_infos'][0]['total_balance'])" 2>/dev/null
}
# ⛔ piso US$ 1,00 — um timeout custa US$ 0,60 (medido 02/09); piso menor estoura no meio da célula.
_check_saldo(){
  local s; s=$(_saldo); log "   saldo: US\$ ${s:-?}"
  if [ -n "${s:-}" ] && [ "$(echo "$s < 1.00" | bc -l 2>/dev/null)" = "1" ]; then
    log "⛔ SALDO ABAIXO DE US\$ 1,00 — parando ANTES de gastar. Recarregue e relance."; exit 9
  fi
}
_ocupada(){
  for p in $(pgrep -f "venv/bin/python( -[^ ]+)* scripts/run_matrix" 2>/dev/null); do
    st=$(ps -o state= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$st" ] && [ "$st" != "T" ] && return 0
  done
  return 1
}
# ⛔ GUARDA DE BANCO (06/09) — conferir que o MySQL RESPONDE antes de gastar.
_banco_ok(){
  local pw; pw=$(docker inspect tpcds-mysql --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
  docker start tpcds-mysql >/dev/null 2>&1
  local i
  for i in 1 2 3 4 5 6; do
    docker exec tpcds-mysql mysqladmin -uroot -p"$pw" ping >/dev/null 2>&1 && return 0
    sleep 10
  done
  return 1
}

log "###### FECHAR O C4 (manchete) antes da cobertura ######"
while _ocupada; do log "aguardando a máquina"; sleep 300; done
if _banco_ok; then log "✅ tpcds-mysql responde"; else
  log "⛔ tpcds-mysql NÃO responde — abortando ANTES de gastar"; exit 8
fi

_check_saldo
log "1/3 ⭐ C4 · TPC-DS · MySQL — fechar q27 q63 q85 q96 (10 runs)"
ONLY="q27 q63 q85 q96" bash "$O" bash "$P" qwen deepseek-v4-flash 3 mysql
_RC=$?
log "   C4 exit=$_RC  · saldo: US\$ $(_saldo)"
# ⛔ AGIR sobre o código, não só registrá-lo — é o defeito que criou esta cadeia.
if [ "$_RC" -ne 0 ]; then
  log "###### ⛔ O C4 FALHOU DE NOVO (rc=$_RC) — PARANDO ######"
  log "   A cobertura NÃO roda com manchete incompleta. Investigar antes de relançar:"
  log "   conferir o contêiner tpcds-mysql e .cache/matrix_failures.log"
  exit "$_RC"
fi

_check_saldo
log "2/3 P1-1b — mistral acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" mistral deepseek-v4-flash 3
log "   P1-1b exit=$?"

_check_saldo
log "3/3 P1-1c — deepseek-r1 acha · flash escreve (COBERTURA)"
bash "$O" bash "$P" deepseek deepseek-v4-flash 3
log "   P1-1c exit=$?"
log "###### CONCLUÍDA ######"
