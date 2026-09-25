#!/usr/bin/env python3
"""F1 — OS CINCO BRAÇOS. Diagrama conceitual (não lê store: não há número aqui).

⛔ POR QUE ESTE SCRIPT EXISTE, e não um desenho à mão. As F3/F4/F5 saem do matplotlib em
   escala de cinza, 8,5 pt, traço preto. Uma figura desenhada em outra ferramenta não casa
   com elas no papel impresso — e não se regenera. Aqui a F1 nasce no mesmo estilo.

⚠️ A LARGURA MANDA. O .tex põe a F1 em `\columnwidth` (~3,3 pol). Um layout em FILA (5 caixas
   lado a lado) precisaria encolher 2,7x e o texto viraria 3 pt. Por isso: TRÊS FILEIRAS,
   uma por camada, que é o que cabe numa coluna sem reduzir nada.

⛔ NÃO é um diagrama de arquitetura do sistema. A §1 abre dizendo "we do not propose a new
   optimizer". Isto desenha o ESPAÇO DE PROJETO medido, não um software que construímos.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = "documentation/article/experimentation_article/figures"
BW, BH = 1.06, 0.50          # caixa
GUT    = 0.46                # goteira esquerda: rotulo da camada, fora das caixas

#            x      y     titulo         subtitulo                  preenchimento
CAIXAS = {
    "R":  (1.12, 2.55, "raw",         "1 agent",                  "white"),
    "AL": (2.70, 2.55, "aided-local", "2 agents",                 "white"),
    "AC": (1.90, 1.50, "aided-cloud", "finds local, writes cloud", "0.88"),
    "CE": (1.12, 0.45, "ceiling",     "2 agents",                 "0.74"),
    "MO": (2.70, 0.45, "monolithic",  "1 agent",                  "0.74"),
}
CAMADAS = [(2.55, "LOCAL"), (1.50, "HYBRID"), (0.45, "CLOUD")]
# ⛔ NADA de "+ cloud writer": sugere um TERCEIRO agente. São sempre dois — o que muda
#    é QUEM ocupa cada papel. A legenda promete "each arrow swaps one component"; os
#    rotulos tem de dizer troca, nao soma.
SETAS = [("R","AL","split the writer"), ("AL","AC","writer to cloud"),
         ("AC","CE","finder to cloud"),     ("CE","MO","merge roles")]

fig, ax = plt.subplots(figsize=(3.32, 2.95))
ax.set_xlim(0, 3.32); ax.set_ylim(0.05, 3.00); ax.axis("off")

for y, nome in CAMADAS:                       # rotulo VERTICAL na goteira: nunca colide
    ax.text(0.13, y, nome, fontsize=6.6, weight="bold", color="0.40",
            rotation=90, ha="center", va="center")
    ax.plot([0.30, 0.30], [y - 0.34, y + 0.34], color="0.72", lw=0.8, solid_capstyle="butt")

for x, y, t, sub, fc in CAIXAS.values():
    ax.add_patch(FancyBboxPatch((x - BW/2, y - BH/2), BW, BH,
                 boxstyle="round,pad=0.012,rounding_size=0.05",
                 facecolor=fc, edgecolor="black", lw=1.1, zorder=2))
    ax.text(x, y + 0.09, t, ha="center", va="center", fontsize=8.5, weight="bold", zorder=3)
    ax.text(x, y - 0.12, sub, ha="center", va="center", fontsize=5.9, color="0.25", zorder=3)

for a, b, rot in SETAS:
    xa, ya = CAIXAS[a][0], CAIXAS[a][1]
    xb, yb = CAIXAS[b][0], CAIXAS[b][1]
    if abs(ya - yb) < 0.01:                                  # horizontal, dentro da camada
        p0, p1 = (xa + BW/2, ya), (xb - BW/2, yb)
        # ⚠️ na camada de baixo o rotulo vai ABAIXO da seta: acima ele colidia com o
        #    "+ cloud finder" da diagonal que chega ali.
        acima = ya > 1.0
        ax.text((p0[0] + p1[0])/2, ya + (0.31 if acima else -0.31), rot,
                ha="center", va="bottom" if acima else "top", fontsize=5.9)
        rad = 0
    else:                                                    # desce de camada
        p0, p1 = (xa, ya - BH/2), (xb, yb + BH/2)
        ax.text(max(xa, xb) + 0.18, (ya + yb)/2 + 0.10, rot, ha="left", va="center", fontsize=5.9)
        rad = -0.22
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=8.5, lw=1.0,
                 color="black", shrinkA=1, shrinkB=1,
                 connectionstyle=f"arc3,rad={rad}", zorder=4))

fig.tight_layout(pad=0.12)
fig.savefig(f"{OUT}/F1_concept.pdf")
fig.savefig(f"{OUT}/F1_concept.png", dpi=220)
print("  OK 3.32 x 2.95 in")
