#!/usr/bin/env python3
"""P1-6 — CONTROLE NEGATIVO DO ÍNDICE: o modelo pequeno acha reescrita E índice, ou só reescrita?

PERGUNTA (da usuária): a parede do modelo leve é específica de ESCREVER SQL, ou ele também não
consegue achar oportunidade de design físico? São capacidades diferentes: propor um índice é nomear
uma coluna; escrever uma reescrita equivalente é produzir SQL correto.

CUSTO ZERO: recomputa das células que JÁ existem. Nenhuma run nova, nenhuma chamada de modelo.

COMO LER: o eixo de índice é caminho de ESCALADA — ele só dispara quando a reescrita falha, não muda
nada, é marginal, ou a LLM cita índice. Então "query com índice proposto" é, por construção, um
subconjunto das queries onde a reescrita NÃO resolveu. A pergunta é o que acontece LÁ DENTRO.
"""
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/home/laurapetrola/projects/Athena-2.0")
from src.pipeline.discovery.suggestion_store import _query_hash  # noqa: E402


def qnames():
    out = {}
    for f in glob.glob("queries/*/*/*.json"):
        try:
            d = json.load(open(f))
            s = d.get("sql") or d.get("query") or ""
            if s:
                out[_query_hash(s)[:8]] = os.path.basename(f).replace(".json", "")
        except Exception:
            pass
    return out


def main():
    """⛔ POR QUE ESTE SCRIPT EXIGE UMA CÉLULA (correção 19/08).

    A primeira versão agregava TODAS as células e imprimia um total. Ele produziu
    "212 de 214 (99%) propôs índice · 97 (45%) ajudou" — um número que soma **29 células em 10
    configurações diferentes**: dois engines, quatro writers, com e sem regras, eras separadas por
    semanas. É exatamente a agregação indevida que invalidou o par de julho do llama e a escada do
    qwen. Na âncora limpa (21 queries, n=5, um writer, uma janela) o mesmo cálculo dá **4 de 10**,
    não 45%.

    ⚠️ E o "benefício" é hoje **100% estimado** pelo planner — 11.785 estimadas contra 22 medidas em
    todo o corpo. Só as células com INDEX_VALIDATION=executed produzem número medido.

    Por isso: uma célula por vez, sem total, e com o aviso de estimado/medido impresso junto.
    """
    if len(sys.argv) < 2:
        sys.exit("uso: python scripts/index_negative_control.py <célula>\n"
                 "     ⛔ este script NÃO agrega células — misturar engines/writers/eras produz\n"
                 "        números que não significam nada (ver docstring de main()).\n"
                 "     ex.: python scripts/index_negative_control.py final_workload_pro")
    only = sys.argv[1]
    names = qnames()
    rows = []
    for sdir in sorted(glob.glob(".cache/suggestions_*")):
        cell = os.path.basename(sdir)[len("suggestions_"):]
        if cell != only:
            continue
        idir = f".cache/index_{cell}"
        if not os.path.isdir(idir):
            sys.exit(f"não há store de índice para a célula {cell}")
        rewrite = defaultdict(list)
        for f in glob.glob(sdir + "/*.json"):
            try:
                r = json.load(open(f))
            except Exception:
                continue
            if r.get("outcome"):
                rewrite[names.get(r["query_hash"][:8], "?")].append(r["outcome"])
        idx = defaultdict(list)
        for f in glob.glob(idir + "/*.json"):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            idx[names.get(d["query_hash"][:8], "?")].extend(d.get("recommendations") or [])
        if not rewrite or not idx:
            continue

        # queries onde a REESCRITA não resolveu (nenhum land)
        failed = {q for q, v in rewrite.items() if "rewrite_correct" not in v}
        # dessas, em quantas o modelo AINDA ASSIM propôs índice com benefício acima do ruído
        proposed = {q for q in failed if idx.get(q)}
        helped = {q for q in proposed
                  if any((r.get("measured_read_benefit_pct")
                          if r.get("measured_read_benefit_pct") is not None
                          else (r.get("estimated_benefit_pct") or 0)) > 1.0 for r in idx[q])}
        rows.append((cell, len(rewrite), len(failed), len(proposed), len(helped)))

    if not rows:
        print("nenhuma célula com store de reescrita E de índice")
        return
    print(f"  {'célula':<40} {'queries':>8} {'sem land':>9} {'c/ índice':>10} {'índice útil':>12}")
    for cell, nq, nf, npr, nh in rows:
        print(f"  {cell[:40]:<40} {nq:>8} {nf:>9} {npr:>10} {nh:>12}")
        if nf:
            print(f"\n  Das {nf} queries em que a REESCRITA falhou nesta célula, o modelo propôs")
            print(f"  índice em {npr} e o benefício passou de 1% em {nh}.")
        print("\n  ⚠️ O eixo de índice é caminho de ESCALADA — ele só dispara quando a reescrita não")
        print("     resolve. 'propôs índice em quase todas' é em boa parte CONSTRUÇÃO do desenho;")
        print("     o número que carrega informação é em quantas o benefício é REAL.")
        print("  ⚠️ Conferir se o benefício é MEDIDO (INDEX_VALIDATION=executed) ou ESTIMADO. Hoje")
        print("     quase tudo no corpo é estimado, e o planner superestima.")


if __name__ == "__main__":
    main()
