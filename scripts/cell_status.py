#!/usr/bin/env python3
"""Status de uma célula — contando runs do MESMO jeito que o `--resume` conta.

⚠️ POR QUE ESTE SCRIPT EXISTE (21/08)
    Eu vinha escrevendo um contador ad-hoc a cada checagem, e ele somava TODO registro com campo
    `outcome` — inclusive os `timeout`, que são tentativas em que a ORIGINAL estourou o orçamento e o
    pipeline nunca rodou (ficam sem `writer`). Resultado: eu reportava "17f 8/5" e "13d 6/5", números
    que não existem. O runner ignora esses registros e repete a query até juntar N runs REAIS —
    é o comportamento certo (`_done_counts` no run_matrix.py), e o meu contador é que divergia.

    Regra: **run real = registro com writer** (ou braço raw, onde não há writer por construção).
    Timeout de baseline = arquivo no disco, NÃO é run.

USO
    python scripts/cell_status.py <célula> [<célula> ...]
"""
import glob
import json
import re
import os
import sys
from collections import Counter, defaultdict

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


def _first_n(runs, n):
    """Os N PRIMEIROS runs reais de uma query, em ordem cronológica.

    Por que existe (25/08): a mesma célula serve a DUAS leituras. O raw do qwen sobe a n=5 porque é
    degrau da escada da manchete, mas continua sendo ponto do painel cross-model, onde os outros
    modelos estão a n=3. Comparar alcance de n=5 contra n=3 é viés MECÂNICO — mais runs, mais chance
    de landar ao menos uma vez — então o painel lê os 3 primeiros de TODAS as células.
    Cronológico e não amostra aleatória: os 3 primeiros são literalmente os que já existiam antes do
    topup, então a leitura do painel não muda quando uma célula é completada.
    """
    return sorted(runs, key=lambda r: r.get("timestamp") or "")[:n]


# O CORPUS DO P1 — as 21 queries de TPC-DS. Escrito aqui porque em 02/09 o store do C3 apareceu com
# **q28 e q88**, que não são do corpus: o `control_monolithic.sh` varria `queries/heldout_op2/` (o
# conjunto "fresh" da época do P2) para alcançar a q28, que estava na lista daquela fase. Ninguém
# notou porque nada avisava. O efeito não foi "20 de 21": foram **18 do corpus + 2 estranhas**, e o
# C2xC3 comparou CONJUNTOS DE QUERIES DIFERENTES. Tirando as duas, o alcance do C3 caiu de 16 p/ 14.
#   Agora toda leitura de célula avisa. ⚠️ É AVISO, não filtro: o registro continua contado, para que
# o número impresso nunca discorde silenciosamente dos arquivos em disco. Quem lê decide — o
# procedimento é mover para .cache/_quarentena_p2_fora_corpus/.
#   ⛔ Só vale para TPC-DS (nomes `qNN`). IMDb usa `17f`/`1a`, e nos stores da bateria fresh
# (heldout_op2_pg, system_*_fresh, op2c_hint) as queries "de fora" são o PONTO da célula, não resíduo.
CORPUS_P1 = set(
    "q1 q3 q5 q7 q9 q11 q18 q25 q27 q30 q38 q40 q50 q51 q63 q67 q69 q73 q81 q85 q96".split()
)


# ⛔⛔ `mechanics_failed` AGREGA DUAS COISAS DIFERENTES (06/09, pedido da usuária).
#
#   O desfecho `mechanics_failed` significa "a reescrita não passou pela mecânica". Mas a INSPEÇÃO DAS
#   MENSAGENS mostra que ele mistura casos de natureza oposta:
#
#     (a) TIMEOUT DE EXECUÇÃO — erro 3024 no MySQL / cancelamento no PostgreSQL. O SQL é **válido**;
#         ele simplesmente não termina no orçamento. ⛔ Isso NÃO é falha de escrita.
#     (b) SQL INVÁLIDO — fonte/coluna inexistente, sintaxe, only_full_group_by. Falha de escrita real.
#
#   POR QUE IMPORTA. No C4 (TPC-DS/MySQL) o `mech_f` de 25% é o número que sustenta "este é o
#   quadrante difícil". Decomposto: **9 dos 13 são TIMEOUT**, não erro de escrita. A leitura muda de
#   "o modelo escreve mal aqui" para "a carga não termina aqui" — que é *execution-bound*, e conversa
#   com os 40 timeouts da query ORIGINAL na mesma célula.
#   ⭐ E o contraste com o aided-local é o oposto: no SL1, 36 de 58 são **fonte inexistente** — ali o
#     `mech_f` mede mesmo incapacidade de escrever.
#
#   ⚠️ ISTO É LEITURA, NÃO RE-ROTULAGEM: o desfecho gravado continua `mechanics_failed`. A decomposição
#   é derivada do campo `errors`, que já está em disco — vale retroativamente, sem re-rodar nada.
#   ⚠️ "outro / sem mensagem" é honesto: parte dos registros antigos não guardou `errors` legível.
def causa_mechanics(errs):
    """Separa TIMEOUT DE EXECUÇÃO (SQL válido que não termina) de SQL INVÁLIDO."""
    t = " | ".join(str(e) for e in (errs or [])).lower()
    # FIRST of all: a duplicate's record usually drags along the previous attempt's error, and
    # classifying by that text would assign the wrong cause. It is not a missing message: it is the
    # loop exhausting itself (the writer re-emitted SQL already rejected in that session). Same
    # ordering in gen_table_T1.py — the two scripts must agree.
    if "duplicate_attempt" in t:
        return "repeated rejected rewrite"
    if re.search(r"3024|maximum statement execution time|statement timeout|canceling statement", t):
        return "execution timeout"
    if re.search(r"1146|unknown table|unknown sources|does not exist|undefined ?table|"
                 r"missing from.clause", t):
        return "nonexistent source"
    if re.search(r"1054|unknown column|undefined ?column", t):
        return "nonexistent column"
    if re.search(r"1064|syntax error", t):
        return "syntax"
    if re.search(r"1055|only_full_group_by", t):
        return "only_full_group_by"
    return "other / no message"


def fora_do_corpus(queries):
    """As queries TPC-DS presentes que NÃO pertencem ao corpus do P1."""
    return sorted(
        q for q in queries
        if q.startswith("q") and q[1:].isdigit() and q not in CORPUS_P1
    )


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    first_n = None
    for i, a in enumerate(sys.argv):
        if a == "--first-n" and i + 1 < len(sys.argv):
            first_n = int(sys.argv[i + 1])
            args = [x for x in args if x != sys.argv[i + 1]]
    if not args:
        sys.exit("usage: python scripts/cell_status.py [--first-n N] <cell> [...]")
    names = qnames()
    for cell in args:
        real = defaultdict(list)
        timeouts = Counter()
        for f in glob.glob(f".cache/suggestions_{cell}/*.json"):
            try:
                r = json.load(open(f))
            except Exception:
                continue
            if not r.get("outcome"):
                continue
            q = names.get(r["query_hash"][:8], "?")
            # ⚠️ BUG CORRIGIDO 25/08 — esta condição era `r.get("writer") or r.get("arm") == "raw"`.
            # Em célula RAW não existe writer por construção, então o `or` fazia TODO registro contar
            # como run real — inclusive os `timeout` de baseline. Efeito: `p1_qwen_raw` era reportado
            # com 89 runs quando tem 62, e `p1_deepseek_raw` como 63/63 (completo) quando tem 50 e
            # TRÊS queries sem dado nenhum. O painel cross-model ficou documentado como fechado por
            # causa disto.
            #   A regra agora é EXATAMENTE a do `_done_counts` em run_matrix.py, que é quem o
            # `--resume` obedece: timeout SEM writer não é run, em qualquer braço.
            if r.get("outcome") == "timeout" and not r.get("writer"):
                timeouts[q] += 1
            else:
                real[q].append(r)
        if first_n:
            real = defaultdict(list, {q: _first_n(v, first_n) for q, v in real.items()})
        if not real:
            print(f"\n  {cell}: no runs")
            continue
        # ⛔⛔ HOMOGENEIDADE DE CONFIG DENTRO DA CÉLULA (04/09) — a 3ª forma da mesma família.
        #
        #   As duas primeiras já tinham avisos: query fora do corpus (#20) e matriz viva divergente
        #   (guarda do p1_cloud/p1_local). Faltava a de DENTRO: uma célula cujos runs não foram todos
        #   medidos sob a MESMA configuração.
        #
        #   O CASO QUE FORÇOU ISTO: o SL1 (`qwen aided-local COM schema`) tinha **1 run em 115 sem
        #   `+schemalink`** — o primeiro run da célula, de 29/08, antes de o `SL=on` pegar. A contagem
        #   não denunciava: 102 runs, 21 queries, tudo parecendo saudável. E ele estava numa célula
        #   cujo PONTO é medir o efeito do schema — o único run sem schema contaminava exatamente o
        #   eixo em questão. ⚠️ Foi achado por uma PERGUNTA da usuária ("quem está achando no SL1 e
        #   no W1?"), não por auditoria — é o tipo de defeito que só aparece se alguém olhar.
        #
        #   ⚠️ AVISO, não filtro: os runs seguem contados, para o número impresso nunca discordar do
        #   disco. Quem lê decide — o procedimento é mover para `.cache/_quarentena_regime_misto/`.
        _cfg = Counter()
        for _v in real.values():
            for _r in _v:
                _cfg[(_r.get("model"), _r.get("writer"))] += 1
        if len(_cfg) > 1:
            _dom, _dn = _cfg.most_common(1)[0]
            print(f"\n  {cell}: MIXED CONFIG — {len(_cfg)} distinct (model, writer) combinations")
            for (_m, _w), _c in _cfg.most_common():
                _mark = "  ← dominant" if (_m, _w) == _dom else "  MINORITY"
                print(f"       {_c:>4}x  model={_m}")
                print(f"             writer={_w}{_mark}")
            print("     A cell must measure ONE configuration. Before reporting, move the")
            print("     minority ones to .cache/_quarentena_regime_misto/ — otherwise the number mixes")
            print("     regimes and the axis the cell exists to measure gets contaminated.")

        _fora = fora_do_corpus(real)
        if _fora and len(_fora) == len(real):
            # TODAS as queries são de fora: esta célula é de OUTRO corpus (bateria fresh, op2...),
            # não é resíduo. Nada a mover — o aviso existe só para que ela nunca seja lida como
            # se fosse uma célula do P1.
            print(f"\n  {cell}: cell from ANOTHER corpus ({', '.join(_fora)}) — not from P1.")
            print("     Do not compare with P1 cells: different query sets.")
        elif _fora:
            # MIXTURE: part of the P1 corpus, part from outside. This is the dangerous case — the
            # number comes out inflated by queries the paired cell doesn't have. This is what
            # happened with C3 on 02/09.
            _n = sum(len(real[q]) for q in _fora)
            print(f"\n  {cell}: MIXTURE — {_n} runs on queries outside the corpus: {', '.join(_fora)}")
            print("     The numbers below INCLUDE these runs. Before comparing this cell with")
            print("     another, move them to .cache/_quarentena_p2_fora_corpus/ — otherwise the comparison")
            print("     is between different query sets (documentation/fixed_bugs.md #20).")
        tot = sum(len(v) for v in real.values())
        reach = sum(1 for v in real.values() if any(x["outcome"] == "rewrite_correct" for x in v))
        oc = Counter(x["outcome"] for v in real.values() for x in v)

        # CONSISTENCY IS ONLY DEFINED AT n=5 (decision 2026-08-23). The >=3 threshold is a MAJORITY of
        # five; applied to an n=3 cell it silently becomes UNANIMITY, and the label "(>=3/5)" then lies
        # twice — wrong bar, wrong name. Worse, an n=4 cell reported 0 consistent where one query had
        # actually landed in the majority of its runs.
        #   The rule the project settled on: n=5 cells are COMPARISONS (the headline pairs) and earn the
        # consistency ruler; n=3 cells are COVERAGE and report REACH only. Lowering the bar to "majority
        # of n" was rejected: it would make "consistent" mean 2/3 in one cell and 3/5 in another, which
        # re-imports the very weakness the second ruler was introduced to remove.
        # The denominator is the TYPICAL runs-per-query (mode), not the max: a query the runner had to
        # retry accumulates extra records (q38 reached 8 in a cell whose target was 5) and would print
        # a denominator that never existed.
        n_typ = Counter(len(v) for v in real.values()).most_common(1)[0][0]
        line = f"     {tot} REAL runs across {len(real)} queries · reach {reach}"
        if n_typ >= 5:
            cons = sum(1 for v in real.values()
                       if sum(1 for x in v if x["outcome"] == "rewrite_correct") >= 3)
            line += f" · consistent (>=3 lands, typical n={n_typ}) {cons}"
        else:
            line += f" · consistency N/A (typical n={n_typ}; the ruler only exists at n=5 - COVERAGE cell)"
        print(f"\n  == {cell}" + (f"   [PANEL reading: the first {first_n} runs per query]" if first_n else ""))
        print(line)
        if timeouts:
            print(f"     + {sum(timeouts.values())} baseline timeouts (NOT runs; --resume ignores them)"
                  f" across {dict(timeouts)}")
        print(f"     outcomes: {dict(oc.most_common())}")
        # ⭐ DECOMPOSE `mechanics_failed` — see `causa_mechanics` above. Without this, a high `mech_f`
        #    read as "the model writes badly" might actually be "the query doesn't finish".
        _mf = [x for v in real.values() for x in v if x["outcome"] == "mechanics_failed"]
        if _mf:
            _c = Counter(causa_mechanics(x.get("errors")) for x in _mf)
            _to = _c.get("execution timeout", 0)
            print(f"     ├─ mechanics_failed by CAUSE: {dict(_c.most_common())}")
            if _to:
                _pct = 100 * _to / len(_mf)
                print(f"     └─ {_to} of {len(_mf)} ({_pct:.0f}%) are EXECUTION TIMEOUT — VALID SQL that")
                print(f"           doesn't finish within budget. NOT a writing failure; do not sum as one.")
        for q in sorted(real):
            L = sum(1 for x in real[q] if x["outcome"] == "rewrite_correct")
            extra = f" (+{timeouts[q]} to)" if timeouts.get(q) else ""
            print(f"       {q:>5}: {len(real[q])} runs{extra} · {L} lands")


if __name__ == "__main__":
    main()
