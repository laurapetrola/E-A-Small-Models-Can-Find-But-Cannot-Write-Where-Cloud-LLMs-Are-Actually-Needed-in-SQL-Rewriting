#!/usr/bin/env python3
"""Did reversing the schema order change WHICH index the advisor recommends?

WHY THIS EXISTS
    LLM4IA (CIKM '25) show that an LLM handed a long context attends to its beginning and end and
    misses the middle, picking the wrong index because of it. Our index prompt hands the model a
    schema block and asks it to choose columns from it — the same configuration. Since the index axis
    is the NEGATIVE CONTROL for the FIND/WRITE thesis, a positional artifact there would weaken the
    central argument, not a side one.

READING
    same targets  -> capacity: the bias does not operate here, and the threat is DISCHARGED
    different     -> positional bias, now with a MEASURED magnitude instead of an unquantified caveat

⚠️ Compares the SET of recommended (table, column) pairs per query, not the order they came in:
   the order of the answer is not the claim — which columns were chosen is.
"""
import glob
import json
import sys
from collections import defaultdict

sys.path.insert(0, '.')
from src.pipeline.discovery.suggestion_store import _query_hash


def targets(store):
    """query_hash -> set of (table, column) recommended across all runs of that query."""
    out = defaultdict(set)
    for f in glob.glob(f'.cache/index_posbias_{store}/*.json'):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        for rec in r.get('recommendations') or []:
            out[r['query_hash']].add((rec.get('table'), rec.get('column')))
    return out


names = {}
for f in glob.glob('queries/*/*/*.json'):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    q = d.get('sql') or d.get('query')
    if q:
        names[_query_hash(q)] = f.split('/')[-1].replace('.json', '')

a, b = targets('normal'), targets('reverse')
common = sorted(set(a) & set(b), key=lambda h: names.get(h, h))
if not common:
    print("  nenhuma query com os DOIS braços — a sonda ainda não fechou")
    raise SystemExit(0)

iguais = 0
print(f"  {'query':<7} {'normal':>7} {'reverse':>8} {'só normal':>11} {'só reverse':>12}  veredito")
for h in common:
    A, B = a[h], b[h]
    only_a, only_b = A - B, B - A
    same = not only_a and not only_b
    iguais += same
    v = "✅ idênticos" if same else "⚠️ DIVERGEM"
    print(f"  {names.get(h, h[:7]):<7} {len(A):>7} {len(B):>8} {len(only_a):>11} {len(only_b):>12}  {v}")
    if not same:
        for t, c in sorted(only_a):
            print(f"          só na ordem NORMAL : {t}.{c}")
        for t, c in sorted(only_b):
            print(f"          só na ordem REVERSA: {t}.{c}")

print(f"\n  {iguais}/{len(common)} queries com recomendação IDÊNTICA sob as duas ordens")
if iguais == len(common):
    print("  ⇒ VEREDITO: sem viés posicional detectável. A ameaça do LLM4IA fica DESCARTADA, com medição.")
else:
    print(f"  ⇒ VEREDITO: viés posicional em {len(common)-iguais} de {len(common)}. "
          "Declarar na §5.4 COM esta magnitude — e considerar ampliar para as 21 queries.")
