#!/usr/bin/env python3
"""MANIFESTO DO ARTEFATO — uma linha por store, dizendo o que é e se entra no paper.

⛔ POR QUE EXISTE. O CFP do EDBT 2027 exige a seção `Artifacts` na trilha EA&B. Auditado em 20/09:
   há **3.249 registros** em stores que NÃO aparecem em nenhum doc de célula — quase o dobro do que
   o paper descreve. Publicar o `.cache/` sem explicá-los levanta a pior pergunta possível:
   *"o que mais vocês mediram e não contaram?"*.

⭐ O manifesto transforma isso em força: mostra que sabemos exatamente o que temos, e que o que
   ficou de fora ficou por CRITÉRIO DECLARADO. Nada é apagado — a regra do projeto é mover/declarar,
   nunca deletar (lição do incidente de quarentena).

Gerado dos stores. Regenerar depois de qualquer campanha.
"""
import glob, json, collections, re, statistics

# ── classificação explícita. ⛔ Alvo por NOME, nunca por glob — a lição do incidente de quarentena.
INVIABILIDADE = {
    "p1_deepseek_aided_local_imdb_raw":
        "`deepseek-r1` on JOB — **0 runs in 27 attempts**, median inference **15,397 s (4h17)**. "
        "Cited in **T5** and in **C1-b**: this is the measurement that the model doesn't fit the budget.",
    "p1_llama_aided_local_imdb_raw":
        "`llama` on JOB — **4 runs, 19 timeouts**, median **6,330 s (1h46)**. Cited in **T5** and in **C1-b**.",
    "p1_qwen_aided_local_schemalink_reaoff":
        "`LB` (reasoning OFF) — **6 runs in 51 attempts** (0.17 run/h). The cell was CUT on 19/09 "
        "and with it **claim (iv)**. Record of the infeasibility; the limitation is stated in the paper.",
}
ERA_ANTERIOR = {
    "p1_llama_aided": "`aided` arm with CLOUD writer `pro` — P2 era",
    "p1_llama_aided_fresh0803": "same, *fresh* batch from 03/08 — P2 era",
    "p1_llama_aided_hint": "arm with **injected rules** (`+hint`) — outside the P1 regime",
    "p1_llama_aided_hint_fresh0803": "same, *fresh* batch — outside the P1 regime",
    "p1_qwen_aided_local_PROMPTv1_29ago": "**prompt v1**, superseded on 29/08 — would measure the prompt, not the variable",
    # ── hand-classified on 20/09, the last two that were missing ──
    "control_monolithic_flash":
        "monolithic from the OLD era (`+schemalink+cloudfind`, **plan ON**) — this is not the paper's C3, which is "
        "`control_monolithic_flash_p1` (`+noplan`). **Goes in the artifact anyway**: §4.6 cites three "
        "measurements of it in the `q38` counter-example, and a cited store must be checkable. Not counted in the 1849.",
    "ceiling_tpcds_zerogain":
        "ceiling probe with zero gain (01-02/08) — cited in the **P2 paper**, not P1.",
}
FRAGMENTOS = {
    "p1_mistral_aided_local_dscoder": "7 runs across 4 queries, abandoned on 25/08 — never became a cell",
    "probe_warmup": "warm-up probe, 1 run",
    "probe_hintparity_q69": "hint-parity probe, 6 runs",
}


# ⚠️ STORES MISTOS: o mesmo diretório guarda DUAS células. Contar sem filtrar infla o total do
#    paper — foi o que deu 1.909 em vez de 1.847 na primeira versão deste manifesto.
FILTRO = {"tpcds_mysql": "noplan"}


def conta(d, store):
    n = t = 0
    fl = FILTRO.get(store)
    for f in glob.glob(d + "*.json"):
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if fl and fl not in str(j.get("model")):
            continue
        w = j.get("writer"); w = w.get("model") if isinstance(w, dict) else w
        if j.get("outcome") == "timeout" and not w:
            t += 1
        else:
            n += 1
    return n, t


def main():
    docs = {}
    for dp in glob.glob("documentation/experiments/final/p1/*.md"):
        txt = open(dp, encoding="utf-8", errors="ignore").read()
        for m in re.findall(r"Cell `([a-z0-9_\-]+)`", txt):
            docs[m] = dp.split("/")[-1]

    grupos = collections.OrderedDict([
        ("IN THE PAPER — cell documented", []),
        ("INFEASIBILITY MEASUREMENT — cited, no cell", []),
        ("EARLIER ERA / P2 — outside the P1 regime", []),
        ("FRAGMENT / PROBE — not a cell", []),
        ("UNCLASSIFIED — review before publishing", []),
    ])
    tot = collections.Counter()
    for d in sorted(glob.glob(".cache/suggestions_*/")):
        s = d.split("suggestions_")[1][:-1]
        n, t = conta(d, s)
        if n + t == 0:
            continue
        if s in docs:
            g, nota = "IN THE PAPER — cell documented", f"[`{docs[s]}`](experiments/final/p1/{docs[s]})"
        elif s in INVIABILIDADE:
            g, nota = "INFEASIBILITY MEASUREMENT — cited, no cell", INVIABILIDADE[s]
        elif s in ERA_ANTERIOR:
            g, nota = "EARLIER ERA / P2 — outside the P1 regime", ERA_ANTERIOR[s]
        elif s in FRAGMENTOS:
            g, nota = "FRAGMENT / PROBE — not a cell", FRAGMENTOS[s]
        elif re.match(r"^(system_|final_|imdb_|masking_|flash_ab|cache_|op2c|iia_|heldout|"
                      r"crossengine|guided_|scaffv4|tpcds_mysql_aided|aided|alllocal|"
                      r"qwen_aided_hint|6_fresh|iv_job|q11_verify|probe_)", s):
            g = "EARLIER ERA / P2 — outside the P1 regime"
            nota = ("earlier era — regime different from P1 (writer `pro`/`chat`, or `+hint`, or "
                    "`masking`, or plan on). Not counted in any paper number")
        else:
            g, nota = "UNCLASSIFIED — review before publishing", "**classify by hand**"
        grupos[g].append((s, n, t, nota))
        tot[g] += n

    print("# ARTIFACT MANIFEST — P1\n")
    print("> **Generated by `scripts/gen_artifact_manifest.py`.** One line per execution store,")
    print("> stating **what it is** and **whether it's in the paper**. Regenerate after any campaign.")
    print(">")
    print("> **Why it exists.** The artifact contains more data than the paper describes — 28 cells")
    print("> go into the results, and the rest is earlier era, probe, or infeasibility measurement.")
    print("> **Nothing was deleted** (project rule: move and declare, never delete). This file states")
    print("> exactly what each thing is, so the difference is a **declared criterion** and not an")
    print("> open question.\n")
    for g, linhas in grupos.items():
        if not linhas:
            continue
        print(f"\n## {g}  — {len(linhas)} stores · {tot[g]} runs\n")
        print("| store | runs | +timeouts | what it is |")
        print("|---|---|---|---|")
        for s, n, t, nota in sorted(linhas, key=lambda x: -x[1]):
            print(f"| `{s}` | {n} | {t} | {nota} |")
    print(f"\n\n## Totals\n")
    for g in grupos:
        if grupos[g]:
            print(f"- {g}: **{tot[g]} runs** across {len(grupos[g])} stores")
    print(f"\n**Only the first group counts toward the paper's numbers** (**{tot['IN THE PAPER — cell documented']} runs**).")


if __name__ == "__main__":
    main()
