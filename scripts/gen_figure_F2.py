#!/usr/bin/env python3
"""F2 — O CAMINHO DE UMA RUN. Diagrama do harness (nao le store).

⚠️ LARGURA: o .tex poe a F2 em `figure*` com \\textwidth (~7 pol). Por isso o fluxo corre da
   ESQUERDA para a DIREITA e os quatro desfechos empilham a direita: e o formato que ocupa
   a largura disponivel sem crescer em altura.

⭐ O QUE A FIGURA TEM DE ENTREGAR, e por isso nao pode encolher mais:
   1. FIND e WRITE como passos separados — e a tese do paper;
   2. o gate S=1 por EXECUCAO, nao juiz-LLM;
   3. o TIMEOUT DE BASELINE visualmente FORA do fluxo (traco tracejado, acima) — e o ponto
      que mais confunde leitor, e a figura resolve de graca o que o texto gasta um paragrafo
      explicando;
   4. o laco de re-estrategia, pontilhado, voltando ao FIND.

⛔ Sem losangos: as F1/F3/F4/F5 usam caixa arredondada. A bifurcacao se le pelas arestas
   rotuladas, nao pela forma.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = "documentation/article/experimentation_article/figures"
Y = 1.42                                   # linha do fluxo principal

# ⭐ A CONDICAO VAI DENTRO DA CAIXA DE DESFECHO, nao no rotulo da seta. Entre o gate e os
#    desfechos sobram ~0,2 pol: nao cabe texto ali. Assim as setas ficam limpas e cada
#    desfecho carrega o proprio criterio.
#       x     y     w     h    titulo              subtitulo                          fill   tracejado
NOS = {
 "Q":  (0.45, Y,   0.82, 0.50, "ORIGINAL",        "query",                            "white", False),
 "B":  (1.52, Y,   1.02, 0.50, "measure baseline","warm-up + median of 3",            "white", False),
 "TB": (1.52, 2.58,1.34, 0.60, "BASELINE TIMEOUT","NOT a run\nnever in a denominator","0.93", True),
 "F":  (2.72, Y,   1.00, 0.50, "FIND",            "derive the strategy",              "white", False),
 "W":  (3.92, Y,   1.12, 0.50, "WRITE",           "emit the SQL\ncorrector may repair it",   "white", False),
 "S1": (5.10, Y,   1.06, 0.64, "S=1 GATE",        "full-scale execution\nresult hash","0.80", False),
 "MF": (6.45, 2.52,1.28, 0.50, "mechanics_failed","does not execute",                 "0.93", False),
 "EQ": (6.45, 1.86,1.28, 0.50, "eq_semantic",     "hash mismatch",                    "0.93", False),
 "NG": (6.45, 1.00,1.28, 0.50, "no_gain",         "S=1, no gain",                     "0.93", False),
 "LD": (6.45, 0.34,1.28, 0.50, "LAND",            "S=1, faster",                      "white", False),
}
SETAS = [("Q","B",""), ("B","F","ok"), ("F","W",""), ("W","S1",""),
         ("S1","MF",""), ("S1","EQ",""), ("S1","NG",""), ("S1","LD","")]

fig, ax = plt.subplots(figsize=(6.95, 3.02))
ax.set_xlim(0, 7.15); ax.set_ylim(0.02, 3.00); ax.axis("off")

def borda(k):
    x, y, w, h, *_ = NOS[k]; return x, y, w, h

for k, (x, y, w, h, t, sub, fc, dash) in NOS.items():
    ax.add_patch(FancyBboxPatch((x-w/2, y-h/2), w, h,
                 boxstyle="round,pad=0.012,rounding_size=0.05", facecolor=fc,
                 edgecolor="black", lw=1.0, linestyle=(0,(2.5,1.6)) if dash else "solid", zorder=2))
    ax.text(x, y+0.10, t, ha="center", va="center", fontsize=7.0, weight="bold", zorder=3)
    ax.text(x, y-0.13, sub, ha="center", va="center", fontsize=5.4, color="0.25",
            linespacing=1.25, zorder=3)

def seta(p0, p1, rot="", rad=0.0, dotted=False, lab=None, fs=5.4):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=8, lw=0.95,
                 color="0.35" if dotted else "black", shrinkA=1, shrinkB=1,
                 linestyle=(0,(1.6,1.6)) if dotted else "solid",
                 connectionstyle=f"arc3,rad={rad}", zorder=1 if dotted else 4))
    if rot:
        lx, ly = lab if lab else ((p0[0]+p1[0])/2, (p0[1]+p1[1])/2 + 0.12)
        ax.text(lx, ly, rot, ha="center", va="center", fontsize=fs, zorder=5,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"))

for a, b, rot in SETAS:
    xa, ya, wa, _ = borda(a); xb, yb, wb, _ = borda(b)
    if abs(ya - yb) < 0.01:
        seta((xa+wa/2, ya), (xb-wb/2, yb), rot)
    else:                                            # leque do gate: sai da borda direita
        seta((xa+wa/2, ya + (0.16 if yb > ya else -0.16)), (xb-wb/2, yb), rad=0.0)

# ⭐ timeout de baseline: SOBE, tracejado, fora do fluxo principal
seta((1.52, Y+0.25), (1.52, 2.58-0.30), "does not finish in budget", lab=(1.52, 1.98))
# ⭐ re-estrategia: pontilhado, POR BAIXO de tudo, do gate de volta ao FIND
seta((5.10, Y-0.32), (2.72, Y-0.25), "re-strategy (decomposed arms)", rad=-0.55, dotted=True,
     lab=(3.90, 0.50))

fig.tight_layout(pad=0.12)
fig.savefig(f"{OUT}/F2_harness.pdf"); fig.savefig(f"{OUT}/F2_harness.png", dpi=220)
print("  OK 6.95 x 3.02 in")
