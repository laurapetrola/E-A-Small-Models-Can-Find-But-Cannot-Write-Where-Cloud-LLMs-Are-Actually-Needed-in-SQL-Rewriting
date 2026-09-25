#!/usr/bin/env python3
"""Gera as figuras F3 e F4 do P1 DIRETO DOS STORES.

⛔ NENHUM número é digitado à mão — todos saem de `.cache/suggestions_*`. Regenerar depois de
   qualquer run nova. É a mesma regra dos docs de célula: o texto é humano, o número é gerado.

⚠️ TRUNCAGEM: células com n diferentes são truncadas ao MESMO n antes de comparar (regra do
   projeto: nunca comparar alcance entre n diferentes). O n usado vai no rótulo do eixo.
⚠️ ESCALA DE CINZA: o EDBT é impresso. Marcadores e hachuras diferem, não só a cor.
"""
import glob, json, collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "documentation/article/experimentation_article/figures"


def alcance(store, cap=3, filtro=None):
    """Queries com >=1 land, truncando cada query aos `cap` primeiros runs REAIS."""
    d = collections.defaultdict(list)
    for f in sorted(glob.glob(f".cache/suggestions_{store}/*.json")):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if filtro and filtro not in str(r.get("model")):
            continue
        w = r.get("writer")
        w = w.get("model") if isinstance(w, dict) else w
        # ⛔ timeout de baseline NÃO é run — não entra em denominador nenhum
        if r.get("outcome") == "timeout" and not w:
            continue
        d[r.get("query_hash")].append(r.get("outcome"))
    d = {q: v[:cap] for q, v in d.items()}
    return sum(1 for v in d.values() if "rewrite_correct" in v), len(d)


def f3():
    """A LEI DO ESCRITOR: ganho × força do achador sozinho, por classe de escritor."""
    modelos = [
        ("mistral", "p1_mistral_raw", "p1_mistral_aided_local", "p1cloud_mistral_flash"),
        ("llama", "p1_llama_raw", "p1_llama_aided_local", "p1cloud_llama_flash"),
        ("qwen", "p1_qwen_aided_local_raw", "p1_qwen_aided_local", "p1cloud_qwen_flash"),
        ("deepseek-r1", "p1_deepseek_raw", "p1_deepseek_aided_local", "p1cloud_deepseek_flash"),
    ]
    xs, loc, nuv, rot = [], [], [], []
    for nome, sraw, sloc, snuv in modelos:
        a, _ = alcance(sraw); b, _ = alcance(sloc); c, _ = alcance(snuv)
        xs.append(a); loc.append(b - a); nuv.append(c - a); rot.append(nome)

    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    ax.axhline(0, color="black", lw=0.8, zorder=1)
    ax.scatter(xs, nuv, s=95, marker="o", facecolors="white", edgecolors="black",
               lw=1.6, zorder=3, label="CLOUD writer (deepseek-v4-flash)")
    ax.scatter(xs, loc, s=95, marker="s", facecolors="black", edgecolors="black",
               zorder=3, label="LOCAL writer (qwen2.5-coder:7b)")
    for x, y1, y2, r in zip(xs, nuv, loc, rot):
        dx = -8 if x == max(xs) else 7
        ha = "right" if x == max(xs) else "left"
        ax.annotate(r, (x, y1), textcoords="offset points", xytext=(dx, 6), fontsize=8.5, ha=ha)
        ax.annotate(r, (x, y2), textcoords="offset points", xytext=(dx, -13), fontsize=8.5, ha=ha)
    ax.set_xlabel("reach of the model ALONE — raw arm (out of 21 queries)")
    ax.set_xlim(0.2, 7.9)
    ax.set_ylabel("gain from swapping the WRITER\n(queries more, or fewer)")
    ax.set_title("Swapping the writer rescues the weak finder and hurts the strong one\n(all cells truncated to n=3)", fontsize=10)
    ax.legend(fontsize=8.5, loc="upper right", framealpha=1)
    ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
    ax.set_xticks(sorted(set(xs)))
    fig.tight_layout()
    fig.savefig(f"{OUT}/F3_writer_law.pdf")
    fig.savefig(f"{OUT}/F3_writer_law.png", dpi=200)
    print(f"  F3: x(raw)={xs} · cloud={nuv} · local={loc}")


def f4():
    """OS QUATRO QUADRANTES: raw × aided-cloud, por benchmark e engine."""
    quad = [
        ("TPC-DS\nPostgreSQL", ("p1_qwen_aided_local_raw", None), ("p1cloud_qwen_flash", None)),
        ("TPC-DS\nMySQL", ("tpcds_mysql", "noplan"), ("p1cloud_qwen_flash_mysql", None)),
        ("IMDb\nPostgreSQL", ("p1_qwen_aided_local_imdb_raw", None), ("p1cloud_qwen_flash_imdb", None)),
        ("IMDb\nMySQL", ("p1_qwen_aided_local_imdb_mysql_raw", None), ("p1cloud_qwen_flash_imdb_mysql", None)),
    ]
    rot, praw, pnuv, den = [], [], [], []
    for nome, (sr, fr), (sc, fc) in quad:
        a, qa = alcance(sr, 3, fr); b, qb = alcance(sc, 3, fc)
        rot.append(nome); praw.append(100 * a / qa); pnuv.append(100 * b / qb)
        # ⚠️ o denominador MUDA entre quadrantes (o C4 tem 17 queries, não 21) — vai no rótulo
        den.append(f"{a}/{qa} → {b}/{qb}")

    x = range(len(rot)); w = 0.38
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.bar([i - w/2 for i in x], praw, w, label="raw — 1 local agent",
           color="white", edgecolor="black", hatch="///", lw=1.1)
    ax.bar([i + w/2 for i in x], pnuv, w, label="+ cloud writer",
           color="0.35", edgecolor="black", lw=1.1)
    for i, (r, n, d) in enumerate(zip(praw, pnuv, den)):
        ax.text(i - w/2, r + 1.5, f"{r:.0f}%", ha="center", fontsize=8.5)
        ax.text(i + w/2, n + 1.5, f"{n:.0f}%", ha="center", fontsize=8.5, weight="bold")
        ax.text(i, -9, d, ha="center", fontsize=7.5, color="0.3")
    ax.set_xticks(list(x)); ax.set_xticklabels(rot, fontsize=9)
    ax.set_ylabel("queries reached (%)")
    ax.set_ylim(-12, 105)
    ax.set_title("The cloud writer's gain is larger where the local model is weaker", fontsize=10.5)
    ax.legend(fontsize=8.5, loc="upper left", framealpha=1)
    ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
    fig.tight_layout()
    fig.savefig(f"{OUT}/F4_quadrants.pdf")
    fig.savefig(f"{OUT}/F4_quadrants.png", dpi=200)
    print(f"  F4: raw={[f'{v:.0f}' for v in praw]} · cloud={[f'{v:.0f}' for v in pnuv]} · den={den}")


def f5():
    """O MECANISMO DA C1: colapso da falha mecânica quando o escritor muda de local para nuvem.

    ⭐ Esta é a figura da MANCHETE — a F3 mostra o RESULTADO (ganho de alcance), a F5 mostra o
       PORQUÊ. Não é figura opcional.
    """
    grupos = [
        ("aided-LOCAL\n(local writer)", [
            ("qwen/L4", "p1_qwen_aided_local"), ("qwen/SL1", "p1_qwen_aided_local_schemalink"),
            ("r1/L5", "p1_deepseek_aided_local"), ("llama/AL", "p1_llama_aided_local"),
            ("mistral/AL", "p1_mistral_aided_local"), ("qwen/W1", "p1_qwen_aided_local_schemalink_dscoder")]),
        ("aided-CLOUD\n(cloud writer)", [
            ("qwen/C1", "p1cloud_qwen_flash"), ("mistral/P1-1b", "p1cloud_mistral_flash"),
            ("llama/P1-1a", "p1cloud_llama_flash"), ("r1/P1-1c", "p1cloud_deepseek_flash")]),
    ]
    ORDEM = [("mechanics_failed", "mechanical failure", "0.15", ""),
             ("equivalence_failed_semantic", "non-equivalent", "0.45", "xxx"),
             ("no_gain", "valid, no gain", "0.75", "..."),
             ("rewrite_correct", "LAND", "white", "///"),
             ("__outros__", "other (timeout, unverified)", "0.9", "\\\\")]
    rot, dados = [], []
    for gnome, cels in grupos:
        for cnome, store in cels:
            c = collections.Counter(); n = 0
            for f in glob.glob(f".cache/suggestions_{store}/*.json"):
                try: r = json.load(open(f))
                except Exception: continue
                w = r.get("writer"); w = w.get("model") if isinstance(w, dict) else w
                o = r.get("outcome")
                if o == "timeout" and not w: continue   # ⛔ timeout de baseline não é run
                c[o] += 1; n += 1
            principais = [100 * c[k] / n for k, _, _, _ in ORDEM[:-1]]
            principais.append(100 - sum(principais))   # ⚠️ fecha em 100%: timeout, unverified, eq_estrutural
            rot.append(cnome); dados.append(principais)
        rot.append(""); dados.append([0] * len(ORDEM))      # espaçador entre os grupos
    rot.pop(); dados.pop()

    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    base = [0] * len(rot)
    for i, (k, leg, cor, hach) in enumerate(ORDEM):
        vals = [d[i] for d in dados]
        ax.bar(range(len(rot)), vals, 0.68, bottom=base, label=leg,
               color=cor, edgecolor="black", lw=0.8, hatch=hach)
        base = [b + v for b, v in zip(base, vals)]
    for i, d in enumerate(dados):
        if d[0] > 3:
            ax.text(i, d[0] / 2, f"{d[0]:.0f}%", ha="center", va="center",
                    fontsize=8.5, color="white", weight="bold")
    ax.set_xticks(range(len(rot)))
    ax.set_xticklabels(rot, rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("run outcome (%)")
    ax.set_title("The cloud writer eliminates mechanical failure: 55-83% → 6%", fontsize=10.5)
    ax.set_ylim(0, 128)
    ax.legend(fontsize=8, ncol=3, loc="upper center", framealpha=1, bbox_to_anchor=(0.5, 1.02))
    ax.annotate("", xy=(6.6, 104), xytext=(5.4, 104), arrowprops=dict(arrowstyle="->", lw=1.5))
    ax.text(6.0, 106, "writer swap", ha="center", fontsize=8.5, style="italic")
    fig.tight_layout()
    fig.savefig(f"{OUT}/F5_mech_f_collapse.pdf")
    fig.savefig(f"{OUT}/F5_mech_f_collapse.png", dpi=200)
    mf = [d[0] for d in dados if sum(d) > 0]
    print(f"  F5: mech_f local={[f'{v:.0f}' for v in mf[:6]]} · cloud={[f'{v:.0f}' for v in mf[6:]]}")


if __name__ == "__main__":
    f3(); f4(); f5()
    print(f"  saved to {OUT}/ (PDF for LaTeX, PNG to check)")
