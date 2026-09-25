import json
import logging
import os
import re

from src.pipeline.backends import get_backend
from src.pipeline.state import PipelineState

log = logging.getLogger(__name__)

WORKLOAD_REGISTRY_PATH = os.getenv("WORKLOAD_REGISTRY_PATH", "queries/workload_registry.json")


def _spec_columns(spec: dict) -> list[str]:
    """Parse the column list from a spec property: 'col', 'col1, col2' or '(col1, col2)'."""
    return [c for c in re.split(r"[,\s()]+", str(spec.get("property", ""))) if c]


def _workload_for_table(table: str, exclude_sql: str) -> list[dict]:
    """Registry queries that touch this table — the workload the candidate index must not harm.
    The query being optimized is excluded (it is already the primary benefit measurement)."""
    try:
        with open(WORKLOAD_REGISTRY_PATH) as f:
            registry = json.load(f)
    except Exception:
        return []
    table = table.strip().lower()
    exclude = " ".join(exclude_sql.split()).lower()
    out = []
    for entry in registry.get("workload", []):
        tables = [t.lower() for t in entry.get("tables", [])]
        sql = entry.get("sql", "")
        if table in tables and " ".join(sql.split()).lower() != exclude:
            out.append({"source": entry.get("source", "?"), "sql": sql})
    return out


def index_simulation_validator(state: PipelineState) -> PipelineState:
    index_specs = state.get("index_specs", [])
    raw_sql = state["raw_sql"]
    # Simulate against the query that will run: the approved rewrite when combining (its plan is what
    # the index must help), else the original. Workload EXCLUSION stays keyed on the original identity
    # (the registry holds original queries), so the candidate is still measured across the workload.
    target_sql = state.get("best_approved_sql") or raw_sql

    if not index_specs:
        return {**state, "simulation_reports": [], "status": "no_index_to_simulate"}

    backend = get_backend()
    reports = []

    for spec in index_specs:
        try:
            # Catalog gate — an existing index already covering these columns makes the
            # candidate a duplicate. Simulating it can even produce phantom gains (hypopg
            # estimates the virtual index smaller than the bloated real one), so skip it
            # before any simulation.
            existing = backend.existing_index_covering(
                spec.get("target", ""), _spec_columns(spec), spec.get("index_type", "btree")
            )
            if existing:
                log.info(
                    f"[index_simulation_validator] {spec['target']}.{spec['property']}: "
                    f"skipped — already covered by existing index '{existing}'"
                )
                reports.append({
                    "table": spec.get("target", "unknown"),
                    "column": spec.get("property", "unknown"),
                    "index_type": spec.get("index_type", "unknown"),
                    "ddl": spec.get("ddl", ""),
                    "already_indexed": existing,
                    "original_plan_cost": None,
                    "optimized_plan_cost": None,
                    "estimated_read_benefit_pct": None,
                    "write_overhead_pct": None,
                    "recommendation": (
                        f"skipped — existing index '{existing}' already covers these columns; "
                        "a duplicate would add maintenance cost with no read gain"
                    ),
                })
                continue

            workload = _workload_for_table(spec.get("target", ""), raw_sql)
            if workload:
                log.info(
                    f"[index_simulation_validator] workload-aware: evaluating "
                    f"{spec['target']}.{spec['property']} against {len(workload)} registry quer(ies): "
                    f"{[w['source'] for w in workload]}"
                )
            report = backend.simulate_index(spec, target_sql, workload=workload)
            reports.append(report)
            log.info(
                f"[index_simulation_validator] {spec['target']}.{spec['property']}: "
                f"cost {report.get('original_plan_cost')} → {report.get('optimized_plan_cost')} | "
                f"{report.get('recommendation', '')}"
            )
        except Exception as e:
            log.warning(
                f"[index_simulation_validator] failed for "
                f"{spec.get('target', '?')}.{spec.get('property', '?')}: {e}"
            )
            reports.append({
                "table": spec.get("target", "unknown"),
                "column": spec.get("property", "unknown"),
                "index_type": spec.get("index_type", "unknown"),
                "ddl": spec.get("ddl", ""),
                "original_plan_cost": None,
                "optimized_plan_cost": None,
                "estimated_read_benefit_pct": None,
                "write_overhead_pct": None,
                "recommendation": f"simulation error — {e}",
            })

    return {
        **state,
        "simulation_reports": reports,
        "status": "index_simulation_complete",
    }
