#!/usr/bin/env python3
"""Generate results/REPRO_MANIFEST.md — the reproducibility map for the EDBT `Artifacts` section.

WHY THIS EXISTS
    The EDBT CFP makes an `Artifacts` section mandatory (outside the page limit) and states that
    reviewers weigh the supplemental material in their evaluation of scientific quality. A reviewer
    who opens `results/` and asks "how was this produced?" must get an executable answer, not prose.

    This script does not create that answer — it reports, per claim cell, whether the answer exists:
    the raw store, the versioned chain that produced it, the frozen copy under results/, and the
    individual doc. Cells with no versioned chain are printed as a GAP, not quietly omitted.

WHAT COUNTS AS A RUN
    Records whose outcome is `timeout` with no writer are BASELINE timeouts: the ORIGINAL query
    never finished, so the pipeline never ran. They are counted separately and never as runs —
    the same distinction `run_matrix._done_counts` makes for --resume.

USAGE
    python scripts/gen_repro_manifest.py
"""
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, ".")  # run from the repo root, like the other campaign scripts

# The cells backing claims in the papers. Keep in sync with scripts/adherence_structural.py.
from scripts.adherence_structural import CLAIM_CELLS  # noqa: E402

OUT = "results/REPRO_MANIFEST.md"


def _first_file_containing(needle, patterns):
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            try:
                if needle in open(path, errors="ignore").read():
                    return path
            except OSError:
                pass
    return None


def _chain_for(store):
    """The chain that PRODUCES this store — matched on the assignment, never on a comment.

    Matching the bare store name would produce false positives: chains name the cell they PAIR
    AGAINST in their header comments, which once made two unversioned cells look versioned.
    """
    for script in sorted(glob.glob("scripts/campanhas/*.sh")):
        if f"SUGGESTION_STORE_DIR=.cache/{store}" in open(script, errors="ignore").read():
            return script
    return None


def survey(store):
    real = baseline_timeouts = 0
    outcomes = Counter()
    queries = set()
    for path in glob.glob(f".cache/{store}/*.json"):
        try:
            rec = json.load(open(path))
        except Exception:
            continue
        queries.add(rec.get("query_hash"))
        if rec.get("writer"):
            real += 1
            outcomes[rec.get("outcome")] += 1
        else:
            baseline_timeouts += 1
    return {
        "store": store,
        "queries": len(queries),
        "real_runs": real,
        "baseline_timeouts": baseline_timeouts,
        "lands": outcomes["rewrite_correct"],
        "chain": _chain_for(store),
        "frozen": _first_file_containing(store, ["results/stores/*/*/CELL_README.md"]),
        "doc": _first_file_containing(store, ["documentation/experiments/**/*.md"]),
    }


def main():
    rows = [survey(c) for c in CLAIM_CELLS]
    lines = [
        "# 🧾 MANIFESTO DE REPRODUTIBILIDADE — células que sustentam claims",
        "",
        "> **Para a seção `Artifacts` do EDBT** (obrigatória, fora do limite de páginas). Mapeia cada",
        "> célula-claim para: o store cru, a cadeia que a produziu, o congelamento em `results/` e o",
        "> doc individual. **Gerado por `scripts/gen_repro_manifest.py` — não editar à mão.**",
        "",
        "**Como rodar cada uma:** [`documentation/REPRODUCING_CLAIM_CELLS.md`]"
        "(../documentation/REPRODUCING_CLAIM_CELLS.md)",
        "",
        "| célula | queries | runs reais | timeouts da original | lands | cadeia versionada | "
        "store congelado | doc |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        chain = (f"[`{os.path.basename(r['chain'])}`]({r['chain']})" if r["chain"]
                 else "**❌ AUSENTE**")
        lines.append(
            f"| `{r['store'].replace('suggestions_', '')}` | {r['queries']} | {r['real_runs']} | "
            f"{r['baseline_timeouts']} | {r['lands']} | {chain} | "
            f"{'✅' if r['frozen'] else '❌'} | {'✅' if r['doc'] else '❌'} |"
        )
    missing = [r["store"].replace("suggestions_", "") for r in rows if not r["chain"]]
    lines += [
        "",
        f"## ⚠️ Lacuna aberta — {len(missing)} de {len(rows)} células sem cadeia versionada",
        "",
        "As cadeias que geraram estas células viviam em `/tmp` e foram perdidas em dois reboots da",
        "máquina (2026-08-09 e 2026-08-13). O store cru, o congelamento em `results/` e a linha",
        "`Setup:` do doc individual sobrevivem — então elas são **reproduzíveis pela configuração",
        "documentada**, não por script preservado.",
        "",
    ]
    lines += [f"- `{m}`" for m in missing]
    lines += [
        "",
        "**✅ RESOLVIDO (2026-08-14):** o comando de reprodução de cada uma está em",
        "[`documentation/REPRODUCING_CLAIM_CELLS.md`](../documentation/REPRODUCING_CLAIM_CELLS.md),",
        "rotulado `RECONSTRUCTED FROM THE DOCUMENTED SETUP` e com o doc-fonte linkado por célula",
        "para que a tradução seja conferível. A seção `Artifacts` do paper deve declarar essa",
        "distinção — reprodução por configuração documentada, não por script preservado. Omiti-la",
        "seria o artefato afirmar mais do que sabe.",
        "",
    ]
    os.makedirs("results", exist_ok=True)
    open(OUT, "w").write("\n".join(lines))
    print(f"wrote {OUT} — {len(rows)} cells, {len(missing)} without a versioned chain")


if __name__ == "__main__":
    main()
