from src.pipeline.state import PipelineState


def _sql_summary(state: PipelineState) -> dict:
    best_pct = state.get("best_improvement_pct")

    if state.get("best_approved_sql") and best_pct and best_pct > 0:
        return {
            "status": "improved",
            "improvement_pct": round(best_pct, 2),
            "message": f"SQL rewrite reduced execution time by {best_pct:.1f}%.",
        }
    if state.get("approved") and state.get("best_approved_sql") is None:
        return {
            "status": "no_change",
            "improvement_pct": 0.0,
            "message": "No SQL rewrite could improve this query — the original plan is already optimal for this workload.",
        }
    return {
        "status": "failed",
        "improvement_pct": None,
        "message": "SQL rewriting could not improve this query.",
    }


def _write_context(table_writes_total: int, write_overhead_pct: float) -> str:
    if table_writes_total == 0:
        return "static table — write overhead irrelevant"
    if write_overhead_pct < 5.0:
        return f"low write overhead ({write_overhead_pct:.1f}%)"
    if write_overhead_pct < 20.0:
        return f"moderate write overhead ({write_overhead_pct:.1f}%) — review write frequency before creating"
    return f"significant write overhead ({write_overhead_pct:.1f}%) — consider carefully before creating"


def _classify_report(report: dict) -> dict:
    table = report.get("table", "?")
    column = report.get("column", "?")
    index_type = report.get("index_type", "?")
    ddl = report.get("ddl", "")
    benefit_pct = report.get("estimated_read_benefit_pct")
    write_overhead_pct = report.get("write_overhead_pct") or 0.0
    index_size_bytes = report.get("index_size_bytes")
    table_writes_total = report.get("table_writes_total") or 0
    table_live_rows = report.get("table_live_rows") or 0
    recommendation = report.get("recommendation", "")
    estimated_benefit = report.get("estimated_benefit", "")

    write_ctx = _write_context(table_writes_total, write_overhead_pct)

    size_ctx = ""
    if index_size_bytes:
        size_mb = round(index_size_bytes / (1024 * 1024), 1)
        size_ctx = f" Estimated index size: {size_mb} MB."

    if report.get("already_indexed"):
        return {
            "ddl": ddl,
            "table": table,
            "column": column,
            "index_type": index_type,
            "status": "already_indexed",
            "estimated_read_benefit_pct": None,
            "write_overhead_pct": None,
            "index_size_bytes": None,
            "table_writes_total": table_writes_total,
            "table_live_rows": table_live_rows,
            "message": (
                f"Skipped: existing index '{report['already_indexed']}' already covers "
                f"{table}({column}). Creating a duplicate would add maintenance cost with no read gain."
            ),
            "action_required": False,
        }

    workload_impact = report.get("workload_impact")
    workload_ctx = ""
    if workload_impact:
        wi, wr, wu = (workload_impact.get("queries_improved", 0),
                      workload_impact.get("queries_regressed", 0),
                      workload_impact.get("queries_unaffected", 0))
        if wr > 0:
            regressed_names = [q["source"] for q in workload_impact.get("queries", [])
                               if q.get("benefit_pct") is not None and q["benefit_pct"] < -0.5]
            workload_ctx = (
                f" ⚠ Workload check: REGRESSES {wr} other workload quer(ies) "
                f"({', '.join(regressed_names)}) — review before creating."
            )
        else:
            workload_ctx = (
                f" Workload check ({wi + wr + wu} other quer(ies) on this table): "
                f"{wi} improved, {wu} unaffected, none regressed."
            )

    # Workload promotion: a candidate useless for THIS query can still help the workload net
    # (e.g. a GIN index: negligible for the running query but large for another). Gate on
    # DIRECTION (net aggregate benefit > 0, no regression), NOT magnitude — cost is a flag, not a
    # decision (cost≠time, ~10×): a tiny simulated cost delta can be a large real-time win, so a
    # magnitude threshold would discard real wins. The real EXPLAIN ANALYZE decides the magnitude.
    workload_promotes = False
    if workload_impact and workload_impact.get("queries_regressed", 0) == 0:
        aggregate = workload_impact.get("aggregate_benefit_pct") or 0.0
        workload_promotes = aggregate > 0.0

    if benefit_pct is not None:
        if benefit_pct > 0:
            # Direction-positive: the simulation shows this query's cost DROPS with the index.
            # We gate on DIRECTION (cost is a flag, not a decision — cost≠time, ~10×), NOT magnitude:
            # the magnitude (read benefit % and write overhead %) is surfaced as CAVEATED DATA so the
            # DBA confirms with the real EXPLAIN ANALYZE. A small simulated delta can be a big real win.
            status = "simulated_beneficial"
            msg = (
                f"Simulation: query cost drops {benefit_pct:.1f}% (estimate — cost≠time, confirm with "
                f"real EXPLAIN ANALYZE). {write_ctx}.{size_ctx}{workload_ctx} Create?"
            )
            action = True
        elif workload_promotes:
            # Workload-index: doesn't help THIS query, but the workload simulation shows a net
            # positive direction elsewhere. NOT an action for the current per-query task — an
            # opportunity for the offline workload review. Surfaced as information, not action_required.
            status = "workload_opportunity"
            msg = (
                f"Not beneficial for this query ({benefit_pct:.1f}%). "
                f"Informational — the workload simulation shows net gains for other recurring "
                f"queries.{workload_ctx} Logged for workload review (not a recommendation "
                f"for this query)."
            )
            action = False
        else:
            # No simulated cost reduction for this query (≤0%) and no net workload gain. Still RECORDED
            # (index_results keeps everything — cost is a flag); just not flagged as an action.
            status = "simulated_not_beneficial"
            msg = (
                f"Simulation shows no cost reduction for this query ({benefit_pct:.1f}%).{workload_ctx} "
                f"Not flagged — recorded anyway (cost can mislead; confirm with real time if in doubt)."
            )
            action = False
    elif "not supported" in recommendation or "not available" in recommendation:
        status = "not_simulated"
        reason = f"hypothetical-index simulation unavailable for {index_type.upper()} on this backend"
        benefit_detail = estimated_benefit or f"would eliminate a full scan on {table}.{column}"
        msg = (
            f"Cannot simulate: {reason}. "
            f"{benefit_detail}. "
            f"{write_ctx}.{size_ctx} Create?"
        )
        action = True
    else:
        status = "simulation_error"
        msg = "Simulation failed — manual review required."
        action = False

    return {
        "ddl": ddl,
        "table": table,
        "column": column,
        "index_type": index_type,
        "status": status,
        "estimated_read_benefit_pct": benefit_pct,
        "write_overhead_pct": write_overhead_pct,
        "index_size_bytes": index_size_bytes,
        "table_writes_total": table_writes_total,
        "table_live_rows": table_live_rows,
        "workload_impact": workload_impact,
        "message": msg,
        "action_required": action,
    }


def persuasion_layer(state: PipelineState) -> PipelineState:
    simulation_reports = state.get("simulation_reports", [])

    sql_result = _sql_summary(state)
    index_results = [_classify_report(r) for r in simulation_reports]

    # Two distinct consumption models:
    #   query_recommendations — indexes that help the query the DBA just ran (actionable now)
    #   workload_opportunities — indexes useless for this query but valuable for OTHER recurring
    #     queries; not an action for the current task, accumulated for the offline workload review.
    beneficial = [r for r in index_results if r["status"] == "simulated_beneficial"]
    workload_opportunities = [r for r in index_results if r["status"] == "workload_opportunity"]
    not_simulated = [r for r in index_results if r["status"] == "not_simulated"]
    not_beneficial = [r for r in index_results if r["status"] == "simulated_not_beneficial"]
    already_indexed = [r for r in index_results if r["status"] == "already_indexed"]

    parts = []
    if sql_result["status"] == "improved":
        parts.append(f"SQL rewrite: {sql_result['improvement_pct']:.1f}% improvement.")
    elif sql_result["status"] == "no_change":
        parts.append("SQL rewrite: no improvement possible.")
    else:
        parts.append("SQL rewrite: failed.")

    if beneficial:
        regressing = sum(1 for r in beneficial
                         if (r.get("workload_impact") or {}).get("queries_regressed", 0) > 0)
        note = f" ({regressing} with workload regression — review)" if regressing else ""
        parts.append(f"{len(beneficial)} index(es) recommended for this query{note}.")
    if not_simulated:
        parts.append(f"{len(not_simulated)} index(es) recommended — simulation not available, apply and verify.")
    if not_beneficial:
        parts.append(f"{len(not_beneficial)} index(es) simulated but not flagged (no cost reduction — recorded anyway; cost can mislead, confirm with real time).")
    if already_indexed:
        parts.append(f"{len(already_indexed)} candidate(s) skipped — already covered by an existing index.")
    if workload_opportunities:
        parts.append(f"{len(workload_opportunities)} workload opportunit(ies) logged (help other queries, not this one — for workload review).")

    return {
        **state,
        "persuasion_report": {
            "summary": " ".join(parts),
            "sql_result": sql_result,
            # query_recommendations: actionable for the query just run
            "query_recommendations": beneficial + not_simulated,
            # workload_opportunities: informational, for the offline workload review
            "workload_opportunities": workload_opportunities,
            "index_results": index_results,
        },
        "status": "persuasion_complete",
    }
