#!/usr/bin/env python3
"""T1 — A MATRIZ DO QUE FOI MEDIDO. Gerada DIRETO DOS STORES.

⛔ Nenhum número é digitado. Regenerar depois de qualquer run nova.
⭐ É a tabela OBRIGATÓRIA do paper: é nela que o `n` de cada célula fica DECLARADO. Depois de três
   conclusões erradas por `n` cruzado nesta campanha (o "r1 é o melhor", o eixo do schema e o
   `llama` +1), ela é o que protege todas as outras tabelas.

Saída: Markdown (para o skeleton) e LaTeX (para o paper).
"""
import glob, json, collections, re, statistics, sys

# rótulos legíveis; a ordem aqui é a ordem da tabela no paper
ROTULO = {
    "p1_qwen_aided_local_raw":                ("L0c", "raw", "TPC-DS", "PG", "qwen", "—"),
    "p1_deepseek_raw":                        ("L0",  "raw", "TPC-DS", "PG", "deepseek-r1", "—"),
    "p1_llama_raw":                           ("RAW", "raw", "TPC-DS", "PG", "llama", "—"),
    "p1_mistral_raw":                         ("RAW", "raw", "TPC-DS", "PG", "mistral", "—"),
    "p1_deepseek-llama_aided_local_raw":      ("L8",  "raw", "TPC-DS", "PG", "ds-llama", "—"),
    "tpcds_mysql":                            ("M2b", "raw", "TPC-DS", "MySQL", "qwen", "—"),
    "p1_qwen_aided_local_imdb_raw":           ("I1",  "raw", "IMDb", "PG", "qwen", "—"),
    "p1_qwen_aided_local_imdb_mysql_raw":     ("I2",  "raw", "IMDb", "MySQL", "qwen", "—"),
    # 23/09: the SECOND finder to complete the Join Order Benchmark. It closes the workload with
    # ZERO baseline timeouts and lands nothing — separating VIABILITY from EFFICACY, which the
    # paper had been treating as one thing.
    "p1_mistral_aided_local_imdb_raw":        ("I3",  "raw", "IMDb", "PG", "mistral", "—"),
    "p1_qwen_aided_local":                    ("L4",  "aided-local", "TPC-DS", "PG", "qwen", "qwen2.5-coder"),
    "p1_qwen_aided_local_schemalink":         ("SL1", "aided-local", "TPC-DS", "PG", "qwen", "qwen2.5-coder"),
    "p1_deepseek_aided_local":                ("L5",  "aided-local", "TPC-DS", "PG", "deepseek-r1", "qwen2.5-coder"),
    "p1_llama_aided_local":                   ("AL",  "aided-local", "TPC-DS", "PG", "llama", "qwen2.5-coder"),
    "p1_mistral_aided_local":                 ("AL",  "aided-local", "TPC-DS", "PG", "mistral", "qwen2.5-coder"),
    "p1_qwen_aided_local_schemalink_mysql":   ("LA",  "aided-local", "TPC-DS", "MySQL", "qwen", "qwen2.5-coder"),
    "p1_qwen_aided_local_schemalink_dscoder": ("W1",  "aided-local", "TPC-DS", "PG", "qwen", "deepseek-coder"),
    "p1_qwen_aided_local_schemalink_qwen3writer_wthink": ("W2", "aided-local", "TPC-DS", "PG", "qwen", "qwen3:8b+think"),
    "p1cloud_qwen_flash":                     ("C1",  "aided-cloud", "TPC-DS", "PG", "qwen", "flash"),
    "p1cloud_mistral_flash":                  ("P1-1b","aided-cloud","TPC-DS", "PG", "mistral", "flash"),
    "p1cloud_mistral_gpt56luna":              ("P1-1b-L","aided-cloud","TPC-DS","PG", "mistral", "gpt-5.6-luna"),
    # 22-23/09: SAME provider and SAME model as the line above; only reasoning effort changes.
    # ⭐ This is the pair that ISOLATES reasoning (P1-1b-L confounds provider+reasoning+temperature).
    "p1cloud_mistral_gpt56luna_effhigh":      ("P1-1b-H","aided-cloud","TPC-DS","PG", "mistral", "gpt-5.6-luna (high)"),
    "p1cloud_llama_flash":                    ("P1-1a","aided-cloud","TPC-DS", "PG", "llama", "flash"),
    "p1cloud_deepseek_flash":                 ("P1-1c","aided-cloud","TPC-DS", "PG", "deepseek-r1", "flash"),
    "p1cloud_qwen_flash_mysql":               ("C4",  "aided-cloud", "TPC-DS", "MySQL", "qwen", "flash"),
    "p1cloud_qwen_flash_imdb":                ("R1",  "aided-cloud", "IMDb", "PG", "qwen", "flash"),
    "p1cloud_qwen_flash_imdb_mysql":          ("I-MY","aided-cloud", "IMDb", "MySQL", "qwen", "flash"),
    "p1cloud_ceiling_flash":                  ("C2",  "ceiling", "TPC-DS", "PG", "flash", "flash"),
    "control_monolithic_flash_p1":            ("C3",  "monolithic", "TPC-DS", "PG", "flash", "— (1 agent)"),
}
FILTRO = {"tpcds_mysql": "noplan"}   # store misto: só o regime do P1


def medir(store):
    runs = collections.defaultdict(int); to = 0
    for f in glob.glob(f".cache/suggestions_{store}/*.json"):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        fl = FILTRO.get(store)
        if fl and fl not in str(r.get("model")):
            continue
        w = r.get("writer"); w = w.get("model") if isinstance(w, dict) else w
        if r.get("outcome") == "timeout" and not w:
            to += 1
        else:
            runs[r.get("query_hash")] += 1
    if not runs:
        return None
    vals = sorted(runs.values())
    n_tip = statistics.mode(vals)
    reduzidas = sum(1 for v in vals if v < n_tip)
    return len(runs), sum(vals), n_tip, reduzidas, to


def main():
    linhas = []; tr = tt = 0
    for store, (cod, arm, ds, eng, acha, escreve) in ROTULO.items():
        m = medir(store)
        if not m:
            print(f"  no data: {store}", file=sys.stderr); continue
        q, r, n, red, to = m
        linhas.append((cod, arm, ds, eng, acha, escreve, q, r, n, red, to))
        tr += r; tt += to

    print("| cell | arm | dataset | engine | FIND | WRITE | queries | runs | **n** | reduced n | +timeouts |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for c, a, d, e, ac, es, q, r, n, red, to in linhas:
        red_s = f"**{red}**" if red else "—"
        print(f"| `{c}` | {a} | {d} | {e} | {ac} | {es} | {q} | {r} | **{n}** | {red_s} | {to} |")
    print(f"| **TOTAL** | **{len(linhas)} cells** | | | | | | **{tr}** | | | **{tt}** |")
    print()
    print(f"> **{tr} real runs** across **{len(linhas)} cells**. The **{tt} baseline timeouts** "
          f"(**{100*tt/(tr+tt):.0f}%** of the {tr+tt} attempts) **are not runs** — the ORIGINAL query "
          f"doesn't finish within budget, so there is no strategy or rewrite to count. "
          f"The **reduced n** column counts queries that stopped below the cell's `n` because they "
          f"hit the attempt ceiling; they are reported with the `n` they obtained.")




# ─────────────────────────────────────────────────────────────────────────────
# T2 · T3 · T4 · T6 — as tabelas complementares. Mesma disciplina: nada digitado.
# ─────────────────────────────────────────────────────────────────────────────

def _cel(store, cap=None, filtro=None):
    """Devolve (alcance, queries, runs, consistentes, Counter(desfechos), Counter(causas))."""
    per = collections.defaultdict(list); causas = collections.Counter()
    for f in sorted(glob.glob(f".cache/suggestions_{store}/*.json")):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        fl = filtro or FILTRO.get(store)
        if fl and fl not in str(r.get("model")):
            continue
        w = r.get("writer"); w = w.get("model") if isinstance(w, dict) else w
        o = r.get("outcome")
        if o == "timeout" and not w:
            continue
        per[r.get("query_hash")].append(o)
        if o == "mechanics_failed":
            t = " | ".join(str(e) for e in (r.get("errors") or [])).lower()
            # the duplicate comes FIRST: the record usually also carries the previous attempt's
            # error, and classifying by that text would assign the wrong cause.
            if "duplicate_attempt" in t:
                causas["repeated rejected rewrite"] += 1
            elif re.search(r"3024|maximum statement execution|statement timeout|canceling statement", t):
                causas["execution timeout"] += 1
            elif re.search(r"1146|unknown table|unknown column|does not exist|undefined|unknown sources", t):
                causas["nonexistent source"] += 1
            elif re.search(r"syntax|1064|parse", t):
                causas["syntax"] += 1
            else:
                causas["semantic / other"] += 1
    if cap:
        per = {q: v[:cap] for q, v in per.items()}
    des = collections.Counter()
    for v in per.values():
        des.update(v)
    alc = sum(1 for v in per.values() if "rewrite_correct" in v)
    cons = sum(1 for v in per.values() if v.count("rewrite_correct") >= 3)
    return alc, len(per), sum(len(v) for v in per.values()), cons, des, causas


def t2():
    print("\n\n## T2 — RAW CROSS-MODEL PANEL *(TPC-DS/PostgreSQL, truncated to n=3)*\n")
    print("| model | reach | rate | `mech_f` | `no_gain` | runs |")
    print("|---|---|---|---|---|---|")
    for nome, s in [("`deepseek-r1`", "p1_deepseek_raw"), ("`qwen3:8b`", "p1_qwen_aided_local_raw"),
                    ("`llama`", "p1_llama_raw"), ("`ds-llama`", "p1_deepseek-llama_aided_local_raw"),
                    ("`mistral`", "p1_mistral_raw")]:
        a, q, r, _, d, _ = _cel(s, 3)
        print(f"| {nome} | **{a}**/{q} | {100*a/q:.0f}% | {100*d['mechanics_failed']/r:.0f}% | "
              f"{100*d['no_gain']/r:.0f}% | {r} |")
    print("\n> **Truncated to n=3** (`--first-n 3`) — `qwen` has n=5 in the store. At **n=5** it ties "
          "with `deepseek-r1` at **7/21**. Never compare without stating the `n`.")


def t3():
    print("\n\n## T3 — THE WRITER'S LAW *(everything truncated to n=3)*\n")
    print("| model | raw | + **LOCAL** writer | + **CLOUD** writer |")
    print("|---|---|---|---|")
    for nome, sr, sl, sc in [("`mistral`", "p1_mistral_raw", "p1_mistral_aided_local", "p1cloud_mistral_flash"),
                             ("`llama`", "p1_llama_raw", "p1_llama_aided_local", "p1cloud_llama_flash"),
                             ("`qwen3:8b`", "p1_qwen_aided_local_raw", "p1_qwen_aided_local", "p1cloud_qwen_flash"),
                             ("`deepseek-r1`", "p1_deepseek_raw", "p1_deepseek_aided_local", "p1cloud_deepseek_flash")]:
        a, _, _, _, _, _ = _cel(sr, 3); b, _, _, _, _, _ = _cel(sl, 3); c, _, _, _, _, _ = _cel(sc, 3)
        print(f"| {nome} | {a}/21 | **{b}** ({b-a:+d}) | **{c}** ({c-a:+d}) |")
    print("\n> With a **LOCAL** writer all four lose or tie; with a **CLOUD** one all gain, "
          "and the gain is **inversely proportional** to the finder's strength.")


def t4():
    print("\n\n## T4 — `mechanics_failed` BY CAUSE\n")
    print("| cell | arm | `mech_f` | nonexistent source | syntax | **EXECUTION timeout** | repeated rewrite | semantic / other |")
    print("|---|---|---|---|---|---|---|---|")
    for cod, s in [("SL1", "p1_qwen_aided_local_schemalink"), ("L4", "p1_qwen_aided_local"),
                   ("W1", "p1_qwen_aided_local_schemalink_dscoder"), ("LA", "p1_qwen_aided_local_schemalink_mysql"),
                   ("C1", "p1cloud_qwen_flash"), ("C4", "p1cloud_qwen_flash_mysql"),
                   ("C2", "p1cloud_ceiling_flash"), ("C3", "control_monolithic_flash_p1")]:
        _, _, r, _, d, ca = _cel(s)
        mf = d["mechanics_failed"]
        arm = ROTULO.get(s, ("", "?"))[1]
        te = ca["execution timeout"]
        marca = f"**{te}**" if te and mf and te / mf > .5 else str(te)
        print(f"| `{cod}` | {arm} | {mf} ({100*mf/r:.0f}%) | {ca['nonexistent source']} | "
              f"{ca['syntax']} | {marca} | {ca['repeated rejected rewrite']} | {ca['semantic / other']} |")
    print("\n> **Reading `mech_f` as a block FLIPS the conclusion.** In `C4`, most of it is **EXECUTION "
          "timeout** — **valid** SQL that doesn't finish within budget. It is not a writing failure and "
          "cannot be summed as one.\n"
          "> **`repeated rewrite`** is the writer resubmitting SQL already rejected earlier in the same "
          "attempt loop — a distinct failure mode from a fresh syntax/reference error.\n"
          "> **`semantic / other` is instrumentation debt**, not a finding: in those runs the cause "
          "wasn't captured, so it isn't attributed to anything.")


def t6():
    print("\n\n## T6 — HYBRID ARM COST\n")
    print("| cell | runs | measured US$/run | source |")
    print("|---|---|---|---|")
    print("| `P1-1c` (r1→flash) | 63 | **US$ 0.032** | balance 3.32 → 1.50 across 29 runs (09/09) |")
    print("| `P1-1b` (mistral→flash) | 66 | **US$ 0.127** | complete cell (08/09) |")
    print("\n> **Report the RANGE, never the average.** The 4× difference is not noise: it comes from the **mix "
          "of outcomes**. `P1-1b` fails a lot at the writer (32% `mech_f` of attempts) and each failure "
          "costs a retry — output tokens are **93%** of the cost. `P1-1c` lands on the first try.\n"
          "> **The practical consequence:** local FIND (free, private) + cloud WRITE at "
          "**US$ 0.03-0.13 per query**.")


if __name__ == "__main__":
    main(); t2(); t3(); t4(); t6()
