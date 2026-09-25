#!/usr/bin/env python3
"""What would the rule discovery yield TODAY? — a DRY report, nothing is injected.

WHY THIS EXISTS (18/08, user's question: "não conseguimos descobrir mais também?")
    The guided arm injects exactly TWO rules — `early_filter_shared_dimension` and
    `consolidate_repeated_scalar_aggregates` — while the FIND names 3,081 distinct technique strings
    across the corpus (≈10 families after collapsing paraphrases). Two is a small yield, and the
    natural question is whether the corpus would now support more.

    The limiting factor turned out to be structural: `discover_clusters` reads `load_all()`, which
    reads ONE directory — `SUGGESTION_STORE_DIR`. The two rules were distilled from a single store,
    back when the corpus was small. The corpus is now ~2,700 writer runs across ~100 stores, and the
    discovery pass has never seen more than one of them at a time.

⛔ WHY IT IS DRY, AND MUST STAY DRY UNTIL THE CAMPAIGN ENDS
    The contour law compares llama / qwen / mistral receiving THE SAME rules. Discovering new rules
    now would give mistral a different set from the other two, and the curve would measure "how many
    rules" instead of "capacity of the receiver". The frozen set of 2 is what makes the three points
    comparable. `run_auto_feed.py` says it itself: run at the END of a discovery phase, never per-run.

WHAT THE REPORT ANSWERS
    How many clusters exist, and WHERE each one dies: `rejected_criteria` (not frequent / not
    cross-model / no gain), `not_teachable` (nobody landed it, or everybody already does it),
    `skipped_duplicate` (a paraphrase of a rule already injected). That distribution is itself a
    result worth reporting — the distillation yield of an LLM discovery loop is a number nobody
    publishes.

USAGE
    python scripts/discovery_yield_report.py [--store <name>] [--out <file.md>]
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, "/home/laurapetrola/projects/Athena-2.0")
from dotenv import load_dotenv

load_dotenv("/home/laurapetrola/projects/Athena-2.0/.env")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="aided", help="store name without the 'suggestions_' prefix")
    ap.add_argument("--out", default="documentation/experiments/final/discovery_yield.md")
    ap.add_argument("--emit-ruleset", dest="emit_ruleset", default=None,
                    help="grava as candidatas APROVADAS num ruleset JSON separado. ⛔ NÃO toca no "
                         "learned_rules — o braço que usar o arquivo é ADITIVO e a lei do contorno "
                         "(llama e qwen com 2 regras) fica intacta.")
    a = ap.parse_args()

    # Point the discovery at the chosen store BEFORE importing anything that reads STORE_DIR.
    os.environ["SUGGESTION_STORE_DIR"] = f".cache/suggestions_{a.store}"

    from src.pipeline.backends import get_backend                       # noqa: E402
    from src.pipeline.discovery import auto_feed as af                  # noqa: E402
    from src.pipeline.discovery.learned_rules import load_rules         # noqa: E402

    before = load_rules()
    print(f"store        : {a.store}")
    print(f"regras HOJE  : {len(before)} -> {[r.get('name') if isinstance(r, dict) else r for r in before]}")
    print("rodando a passada em DRY-RUN (nada é injetado)...\n", flush=True)

    report = af.auto_feed(get_backend(), force=True, dry_run=True)

    # ⛔ RULESET SEPARADO — não toca no `learned_rules`. O braço que usar este arquivo é ADITIVO, e a
    # lei do contorno (llama e qwen receberam 2 regras) fica INTACTA. É assim que se testa um conjunto
    # novo sem invalidar o antigo: `LEARNED_RULES_FILE=rulesets/discovered_v2.json`, como já se faz
    # com o `handcrafted_prior_work.json`.
    if a.emit_ruleset:
        import json as _json
        regras = [{"name": (r.get("name") or f"discovered_{i}"), "rule": r.get("hint")}
                  for i, r in enumerate(report)
                  if r.get("status") in ("would_inject", "injected") and r.get("hint")]
        os.makedirs(os.path.dirname(a.emit_ruleset) or ".", exist_ok=True)
        with open(a.emit_ruleset, "w") as fh:
            _json.dump(regras, fh, indent=2, ensure_ascii=False)
        print(f"\n  ruleset com {len(regras)} regras -> {a.emit_ruleset}")
        for r in regras:
            print(f"    · {r['name']}: {str(r['rule'])[:76]}")

    after = load_rules()
    assert len(after) == len(before), "DRY-RUN MUTOU O learned_rules — abortar e investigar"

    status = Counter(r.get("status", "?") for r in report)
    L = [f"# Rendimento da descoberta de regras — store `{a.store}`\n",
         "> Gerado por `scripts/discovery_yield_report.py` · **DRY-RUN: nada foi injetado.**",
         f"> Verificado: `learned_rules` continua com {len(after)} regras antes e depois.\n",
         "## Por que a pergunta existe\n",
         "O braço guiado injeta **2 regras**, enquanto o FIND nomeia **3.081 strings distintas** de",
         "técnica no corpo (≈10 famílias depois de colapsar paráfrases). A descoberta lê **um único**",
         "diretório por vez (`SUGGESTION_STORE_DIR`), e as 2 regras saíram de um store só, quando o",
         "corpo era pequeno.\n",
         "⚠️ **A coluna `motivo` mostra apenas os testes que FALHARAM** (`✗`). Uma versão anterior "
         "imprimia todos os nomes de critério por um bug de leitura do dicionário — se você viu "
         "`frequency; cross_model; gain` sem `✗`, era isso.\n",
         "## Onde cada candidato morre\n",
         "| status | clusters |", "|---|---|"]
    for s, n in status.most_common():
        L.append(f"| `{s}` | {n} |")
    L += ["", "**Legenda:** `would_inject` passaria · `rejected_criteria` = falha frequência (≥4 runs), "
              "cross-model (≥2 modelos) ou ganho (≥1%) · `not_teachable` = ninguém landou (não há de onde "
              "destilar) **ou** todos já fazem (não há o que ensinar) · `skipped_duplicate` = paráfrase de "
              "regra já injetada.\n",
          "## Os candidatos, um a um\n",
          "| representante | status | motivo |", "|---|---|---|"]
    for r in report:
        rep = str(r.get("representative", "?"))[:70].replace("|", "/")
        # ⚠️ `reasons` é um DICT {nome_do_teste: passou?} — não uma lista de falhas. A primeira versão
        # fazia "; ".join(reasons), que junta as CHAVES e imprimia os quatro nomes de critério
        # independentemente do resultado, tornando a coluna inútil (achado 19/08). Agora mostra só os
        # que FALHARAM, com ✗.
        rs = r.get("reasons")
        if isinstance(rs, dict):
            failed = [k for k, v in rs.items() if not v]
            why = " · ".join(f"✗ {k}" for k in failed) if failed else "—"
        elif rs:
            why = "; ".join(str(x) for x in rs)
        else:
            why = "—"
        L.append(f"| {rep} | `{r.get('status')}` | {why[:90].replace('|', '/')} |")
    L += ["", "## ⛔ O que NÃO fazer com este relatório\n",
          "**Não injetar nada até a campanha fechar.** A lei do contorno compara llama / qwen / mistral",
          "recebendo **as mesmas** regras. Injetar agora daria ao mistral um conjunto diferente, e a curva",
          "passaria a medir *quantas regras* em vez de *capacidade de quem recebe*. Se decidirmos adotar um",
          "conjunto novo, os **três** pontos têm de ser refeitos.\n"]

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("\n".join(L))
    print(f"\n{dict(status)}\nrelatório em {a.out}")


if __name__ == "__main__":
    main()
