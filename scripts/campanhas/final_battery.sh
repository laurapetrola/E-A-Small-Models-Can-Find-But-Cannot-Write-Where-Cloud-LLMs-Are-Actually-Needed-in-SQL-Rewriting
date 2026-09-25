#!/usr/bin/env bash
# FINAL BATTERY — the canonical architecture, re-measured with the CORRECTED masking.
#
#   usage:  bash scripts/campanhas/final_battery.sh <writer-model> <workload|fresh> [engine] [ruleset] [on|off] [extra args] [plan on|off]
#           (5th arg = writer reasoning regime; see WRITER_REASONING in corrector.py for why it matters)
#   e.g.:   bash scripts/campanhas/final_battery.sh deepseek-v4-pro workload
#           bash scripts/campanhas/final_battery.sh deepseek-v4-pro fresh mysql
#           bash scripts/campanhas/final_battery.sh deepseek-v4-pro workload postgres rulesets/handcrafted_prior_work.json
#
# RULESET (4th arg) — the SOURCE-OF-KNOWLEDGE ablation. Default: the rules the system DISCOVERED and
# validated through S=1. Passing a file swaps in a different set (the 11 hand-written heuristics from
# the author's prior work) WITHOUT touching the discovered-rules file, so the two are comparable and
# neither contaminates the other. Everything else stays identical: same FIND, plan, masking, writer.
#
# ⚠️ The hand-written set is an ABLATION ARM, never the product: several of those heuristics NAME the
# technique that solves our target queries ("avoid correlated subqueries" gives away q1/q30). Shipping
# them would destroy the unaided baseline — the discovery claim would stop being falsifiable.
#
# WHY THIS RE-RUN EXISTS
#   The masking layer leaked: the map was built from the QUERY, but schema-linking advertises EVERY
#   column of each relevant table, so the columns the query never used travelled to the cloud in the
#   clear — median 85 real identifiers per payload, 21/21 payloads, including c_email_address.
#   find_leaks could not see it (it only checks names that are IN the map). Fixed 2026-08-15 in
#   masking.extend_mask_map. The cells that ran before the fix (system_pro_tpcds_pg, system_pro_fresh,
#   system_flash_tpcds_pg) are pre-fix and cannot back the paper's privacy claim.
#
# WHAT THIS PAIRS AGAINST — the re-run is not only a repair, it is an experiment
#   Same writer, same composition, only the masking changes. So this forms a CLEAN PAIR with that
#   writer's pre-fix cell and answers the open question: schema-linking exists to mitigate the
#   "placeholders remove the semantic scope signal" effect — how much of that signal came from the
#   real names that were leaking? Lands may DROP. Publishable either way; must be measured, never
#   assumed.
#
# ONE SCRIPT, TWO MODES ON PURPOSE
#   The two batteries must be identical except for the query set. Two separate files drift — that is
#   how a paired cell ends up with a different SCHEMA_LINKING than its baseline.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1

WRITER="${1:-}"
BATTERY="${2:-}"
ENGINE="${3:-postgres}"
RULESET="${4:-}"
# 5th arg: writer reasoning regime (on|off). Default ON — every cell before 2026-08-16 was ON, and
# changing the default would silently make old and new cells incomparable.
WREASON="${5:-on}"
# 6th arg: extra args passed straight to run_matrix — used for the PROBE ("--only q69"), which runs a
# couple of queries into the SAME store the full cell will use, so nothing is wasted: --resume picks
# them up when the complete cell runs later.
EXTRA="${6:-}"
# 7th arg: execution plan on|off — the ablation that justifies contribution 2 of the advisor's list.
# The existing plan ablation (q69, q28) is from July, in a config WITHOUT masking and WITHOUT rules;
# to defend the plan we need the comparison inside the composition the paper calls "the system".
PLAN="${7:-on}"
case "$PLAN" in
  on|off) ;;
  *) echo "plan must be on or off"; exit 2 ;;
esac
# NOTE: the _noplan suffix is applied further down, AFTER the engine block. It used to be applied
# here and the engine block (SUFFIX="" for postgres) erased it, so the plan=off cell pointed at the
# plan=on store, --resume found 5/5 already there and skipped every query — the ablation reported
# "DONE (exit 0)" having run NOTHING. Found 18/08 while checking the q28 probe.
case "$BATTERY" in
  workload) DIRS="curated heldout non-curated"; NQ=21 ;;
  fresh)    DIRS="heldout_op2";                 NQ=11 ;;
  *) echo "usage: $0 <writer-model> <workload|fresh> [postgres|mysql]"; exit 2 ;;
esac
# Engine wiring. The QUADRUPLE checklist lives here so it cannot be forgotten when switching
# quadrants: (1) DB_URI + VERIFY_DB_URI, (2) DB_TYPE, (3) ANALYZE on both, (4) drop the schema cache.
# Forgetting (2) once produced 33 junk runs on 2026-08-08.
case "$ENGINE" in
  postgres) CONTAINERS="tpcds tpcds-pg-sf1"; PORT=5435; VPORT=5437; SUFFIX="" ;;
  mysql)    CONTAINERS="tpcds-mysql tpcds-mysql-sf1"; PORT=3308; VPORT=3309; SUFFIX="_mysql" ;;
  *) echo "engine must be postgres or mysql"; exit 2 ;;
esac
[ -n "$WRITER" ] || { echo "usage: $0 <writer-model> <workload|fresh> [postgres|mysql]"; exit 2; }

PY=.venv/bin/python
SE="$PY scripts/campanhas/setenv.py"
TAG="${WRITER##*-}"                       # pro | flash
# SUFFIX is empty for postgres so the anchor stores keep the names already in use.
#
# ORDER MATTERS: resolve the literal "none" BEFORE the file check. "none" is not a path — it means
# "no rules at all", the leg the P2 architecture actually ships. Checking the file first made every
# rules-off run die with `ruleset not found: none`.
APPLY_RULES=on
if [ "$RULESET" = "none" ]; then
  APPLY_RULES=off; RULESET=""; SUFFIX="${SUFFIX}_norules"
elif [ -n "$RULESET" ]; then
  [ -f "$RULESET" ] || { echo "ruleset not found: $RULESET"; exit 2; }
  SUFFIX="${SUFFIX}_handrules"
fi

case "$WREASON" in
  on)  ;;
  off) SUFFIX="${SUFFIX}_nothink" ;;
  *) echo "writer reasoning must be on or off"; exit 2 ;;
esac
[ "$PLAN" = off ] && SUFFIX="${SUFFIX}_noplan"   # must come AFTER the engine block (see note above)
STORE=".cache/suggestions_final_${BATTERY}_${TAG}${SUFFIX}"
LOG="logs/campanhas/final_${BATTERY}_${TAG}${SUFFIX}.log"
mkdir -p logs/campanhas
: > "$LOG"
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

restart_server(){
  # MUST restart: the masking fix is a CODE change, and the server loads code at startup. Running
  # this battery against a server started before 2026-08-15 would silently reproduce the leak.
  pkill -f "uvicorn src.main:app" 2>/dev/null; sleep 3
  nohup $PY -m uvicorn src.main:app --port 8000 >>"$LOG.server" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':8000' && { log "  server UP (masking fix loaded)"; sleep 2; return 0; }; sleep 1; done
  log "  server did not come up"; return 1
}

health(){
  docker start $CONTAINERS >/dev/null 2>&1
  for i in $(seq 1 40); do
    (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && (exec 4<>/dev/tcp/127.0.0.1/$VPORT) 2>/dev/null && { exec 3>&- 4>&-; log "  databases UP ($ENGINE $PORT/$VPORT)"; return 0; }
    sleep 3
  done
  log "  databases did not come up ($ENGINE)"; return 1
}

# HARD GATE: prove on real payloads that the leak is closed BEFORE spending two days of compute.
# The whole point of this battery is the privacy claim; running it with the leak would waste the run
# and, worse, produce a cell that LOOKS final.
masking_gate(){
  $PY - <<'PYEOF' 2>&1 | tee -a "$LOG"
import glob, json, re, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")   # explicit path: find_dotenv() cannot walk frames when run from a heredoc
from src.pipeline import masking as M
from src.pipeline.backends import get_backend
if not hasattr(M, "extend_mask_map"):
    print("  MASKING GATE: FAIL — extend_mask_map missing (pre-fix code)"); sys.exit(1)
b = get_backend(); d = b.sqlglot_dialect()
seen, worst = set(), 0
for p in sorted(glob.glob(".cache/suggestions_system_pro_tpcds_pg/*.json")):
    r = json.load(open(p)); h = r.get("query_hash")
    if not h or h in seen or not r.get("raw_sql"): continue
    seen.add(h)
    sql = r["raw_sql"]
    mm = M.build_mask_map(sql, dialect=d)
    try: sch = b.relevant_schema_block(sql)
    except Exception: continue
    M.extend_mask_map(mm, M.identifiers_in_schema_block(sch))
    payload = M.mask_text(sch, mm) + "\n" + M.mask_sql(sql, mm, dialect=d)
    worst = max(worst, len(set(re.findall(r"\b[a-z]{1,4}_[a-z_]{3,}\b", payload))))
    if len(seen) >= 8: break
print(f"  MASKING GATE: {len(seen)} payloads checked, worst-case real identifiers = {worst}")
sys.exit(0 if worst == 0 else 1)
PYEOF
  return ${PIPESTATUS[0]}
}

preflight(){
  $PY - "$1" <<'PYEOF' 2>&1 | tee -a "$LOG"
import os,sys
from openai import OpenAI
model=sys.argv[1]
key=os.popen("grep '^KEY_DEEPSEEK' .env | cut -d= -f2").read().strip()
c=OpenAI(api_key=key, base_url="https://api.deepseek.com")
try:
    r=c.chat.completions.create(model=model,messages=[{"role":"user","content":"Return ONLY SQL: select 1"}],temperature=0,max_tokens=400)
    ok=bool((r.choices[0].message.content or "").strip())
    print(f"  preflight {model}:", "OK" if ok else "EMPTY - check balance"); sys.exit(0 if ok else 1)
except Exception as e:
    print(f"  preflight {model} FAILED:", str(e)[:120]); sys.exit(1)
PYEOF
  return ${PIPESTATUS[0]}
}

# ONE SERVER, ONE .env: refuse to start while another cell is running. Without this, a chain that
# fires on a timer (the off-peak supervisor resumes at 01:00) would rewrite .env and restart the
# server UNDER a cell already in flight — the second cell would silently inherit the first one's
# configuration. The symmetric guard lives in p1_local.sh.
if pgrep -f "scripts/run_matrix.py" >/dev/null; then
  log "ABORT: another cell is already running (one server, one .env — never overlap)"; exit 1
fi

log "###### FINAL BATTERY — ${BATTERY} (${NQ}q x5) · engine=${ENGINE} · writer=${WRITER} · rules=${RULESET:-$([ "$APPLY_RULES" = off ] && echo NONE || echo discovered)} · plan=${PLAN} · writer-reasoning=${WREASON} · masking CORRIGIDO ######"
health || exit 1
log "masking gate (proves the leak is closed on real payloads)..."
masking_gate || { log "ABORTING: masking still leaks — do NOT spend the run"; exit 1; }
log "preflight..."; preflight "$WRITER" || { log "aborting: writer unavailable"; exit 1; }

if [ "$ENGINE" = "postgres" ]; then
  PW=$(docker inspect tpcds --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
  URI="postgresql://postgres:${PW}@localhost:${PORT}/tpcds"
  VURI="postgresql://postgres:${PW}@localhost:${VPORT}/tpcds"
else
  PW=$(docker inspect tpcds-mysql --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2)
  URI="mysql+pymysql://root:${PW}@localhost:${PORT}/tpcds"
  VURI="mysql+pymysql://root:${PW}@localhost:${VPORT}/tpcds"
fi
$SE MODEL_FAMILY=qwen-reasoning REASONING=off TWO_AGENT_MODE=on RUN_ARM=aided \
   WRITER_BACKEND=cloud FIND_BACKEND=local "APPLY_HEURISTICS=${APPLY_RULES}" "PLAN_HINTS=${PLAN}" HW_HINTS=off \
   MASKING=on MASK_SCOPE=id_str SCHEMA_LINKING=on \
   DISABLE_SEMANTIC_CACHE=on STRATEGY_CACHE=off ADHERENCE_GATE=off "LEARNED_RULES_FILE=${RULESET}" \
   INDEX_VALIDATION=executed \
   "WRITER_REASONING=${WREASON}" \
   "DB_TYPE=${ENGINE}" "DB_URI=${URI}" "VERIFY_DB_URI=${VURI}" \
   "CLOUD_CODER_MODEL=${WRITER}" \
   "SUGGESTION_STORE_DIR=${STORE}" \
   "INDEX_SUGGESTION_STORE_DIR=.cache/index_final_${BATTERY}_${TAG}${SUFFIX}" | tee -a "$LOG"

bash scripts/campanhas/kill_orphans.sh | tee -a "$LOG"
log "ANALYZE on both instances (planner statistics)..."
$PY scripts/analyze_dbs.py >>"$LOG" 2>&1 && log "  ANALYZE ok" || { log "  ANALYZE FAILED - aborting"; exit 1; }
rm -f .cache/schema_cache.json && log "  schema cache cleared"

# CANARY: spend ONE run, then verify the RECORD matches what we intended. Every expensive mistake in
# this campaign had the same shape — the cell ran to completion with the wrong configuration and we
# found out afterwards (the masking leak, writer_tokens_out=0, a cell pointed at the previous store).
# This turns "105 wasted runs" into "1 wasted run". It reads the RECORD, not .env: .env says what was
# requested, the record says what the server actually applied.
log "running ${NQ} queries x5 (resuming) -> ${STORE}"
restart_server || exit 1
CANARY_ARGS="--writer cloud:${WRITER} --rules $([ "$APPLY_RULES" = on ] && echo on || echo off) --plan ${PLAN} --index executed"
if [ "$(ls "${STORE}"/*.json 2>/dev/null | wc -l)" -eq 0 ]; then
  log "canary: running ONE query first to validate the configuration..."
  $PY scripts/run_matrix.py --runs 1 --resume --dirs $DIRS $EXTRA >>"$LOG" 2>&1
else
  # Store is NOT empty: --resume will count those runs as ours. If they came from a different
  # configuration, the cell silently skips instead of running. Validate them BEFORE resuming.
  log "canary: store already has runs — checking they match THIS configuration before resuming"
fi
# ⚠️ NÃO usar `if ! canary | tee` — o `if` avalia o exit do TEE (sempre 0) e a falha do canário
# some. Descoberto em 20/08: o canário imprimiu "CANARY FAILED" e a cadeia seguiu em frente. A
# salvaguarda existiu por dois dias em CINCO cadeias sem nunca ter bloqueado nada.
_cout=$($PY scripts/canary_check.py "$(basename "$STORE" | sed 's/^suggestions_//')" $CANARY_ARGS 2>&1); _crc=$?
printf '%s\n' "$_cout" | tee -a "$LOG"
if [ "$_crc" -ne 0 ]; then
  log "ABORTING: the canary rejected the configuration. Fix it before spending the cell."
  exit 1
fi
log "canary passed — proceeding with the full cell"
$PY scripts/run_matrix.py --runs 5 --resume --dirs $DIRS $EXTRA >>"$LOG" 2>&1
log "###### FINAL ${BATTERY} DONE (exit $?) ######"
log "next: individual doc (canonical template + the fidelity section) and the PAIRED comparison"
log "      against the pre-fix cell of the SAME writer — that pair measures what the privacy fix cost."
