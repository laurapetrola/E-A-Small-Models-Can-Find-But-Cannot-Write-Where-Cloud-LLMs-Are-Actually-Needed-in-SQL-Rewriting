#!/usr/bin/env python3
"""CANARY — validate the FIRST saved run of a cell before letting it spend the rest.

WHY THIS EXISTS
    Every expensive mistake in this campaign had the same shape: the cell ran to completion with the
    WRONG configuration and we found out afterwards. Examples that actually happened:
      * the masking layer leaked the schema catalogue -> 3 cells unusable for the privacy claim;
      * `writer_inference_ms` arrived as 0.0 for weeks (field not declared in PipelineState) -> the
        writer ablation had to be decided by a separate benchmark;
      * `--resume` counted baseline timeouts as runs -> 3 queries silently under-sampled;
      * a cell was launched pointing at the previous cell's store.
    A canary turns "105 wasted runs" into "1 wasted run": it reads the first record and asserts that
    what the record SAYS matches what the caller INTENDED.

    It checks the RECORD, not the .env — .env says what was requested; the record says what the
    server actually applied. That distinction is the whole point.

USAGE
    python scripts/canary_check.py <store> --writer cloud:deepseek-v4-flash --rules off \
        --plan on --index executed [--find qwen-reasoning] [--masking on]

    Exit 0 = the cell is configured as intended. Exit 1 = STOP, do not spend.
"""
import argparse
import glob
import json
import sys


def load_first(store):
    """The earliest record that actually exercised the pipeline (a baseline timeout is not a run)."""
    recs = []
    for p in glob.glob(f".cache/suggestions_{store}/*.json"):
        try:
            r = json.load(open(p))
        except Exception:
            continue
        if r.get("outcome") == "timeout" and not r.get("writer"):
            continue
        recs.append(r)
    return sorted(recs, key=lambda r: r.get("timestamp", ""))[0] if recs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--writer")   # expected writer label, e.g. cloud:deepseek-v4-flash
    ap.add_argument("--find")     # substring expected in the model label
    ap.add_argument("--rules", choices=["on", "off"])
    ap.add_argument("--plan", choices=["on", "off"])
    ap.add_argument("--masking", choices=["on", "off"])
    ap.add_argument("--index", choices=["executed", "simulated"])
    ap.add_argument("--single-agent", action="store_true",
                    help="cell 0d: assert the record carries NO FIND strategy (the cloud model works "
                         "alone, the LITHE-shaped monolithic baseline)")
    a = ap.parse_args()

    r = load_first(a.store)
    if r is None:
        print(f"CANARY: no run recorded yet in {a.store} — nothing to check")
        return 0

    fails = []
    model = str(r.get("model") or "")
    writer = str(r.get("writer") or "")

    if a.writer and writer != a.writer:
        fails.append(f"writer is {writer!r}, expected {a.writer!r}")
    if a.find and a.find not in model:
        fails.append(f"model label {model!r} does not contain {a.find!r}")

    # 0d — the monolithic baseline. Its ENTIRE meaning is the ABSENCE of the weak model's strategy:
    # if TWO_AGENT_MODE failed to turn off, the cell would compare the architecture against ITSELF,
    # produce a tie, and the tie would look like evidence. That is the worst failure mode available
    # here, because it is invisible in the aggregate numbers.
    if a.single_agent:
        strat = (r.get("strategy") or "").strip()
        if strat:
            fails.append(f"single-agent control STILL received a FIND strategy: {strat[:70]!r}")

    # rules: apply_mode is what the server applied; rules_in_prompt PROVES they reached the prompt
    if a.rules:
        applied = bool(r.get("apply_mode"))
        in_prompt = list(r.get("rules_in_prompt") or [])
        if a.rules == "on" and not (applied and in_prompt):
            fails.append(f"rules should be ON: apply_mode={applied} rules_in_prompt={in_prompt}")
        if a.rules == "off" and (applied or in_prompt):
            fails.append(f"rules should be OFF: apply_mode={applied} rules_in_prompt={in_prompt}")

    # plan / masking leave their mark in the model label (noplan / schemalink)
    if a.plan == "off" and "noplan" not in model:
        fails.append(f"plan should be OFF but label has no 'noplan': {model!r}")
    if a.plan == "on" and "noplan" in model:
        fails.append(f"plan should be ON but label says 'noplan': {model!r}")
    if a.masking == "on" and "schemalink" not in model:
        fails.append(f"masking+schema-linking expected but label lacks 'schemalink': {model!r}")

    # index validation: the field only exists when the index was MEASURED by execution
    if a.index:
        idx = _index_validation(a.store)
        # ⚠️ `absent` NÃO é falha (correção 22/08). O eixo de índice é caminho de ESCALADA: só dispara
        # quando a reescrita falha, não muda nada, é marginal, ou a LLM cita índice. Se a query do
        # canário LANDA limpo, não existe registro de índice — e isso é o sistema funcionando, não
        # configuração errada. A primeira versão barrava por isso e matou o C1 e o C2 em 22/08 14:21,
        # cada um depois de 1 run. Só um valor REAL divergente ('estimated' onde se espera 'executed')
        # é motivo para abortar.
        if a.index == "executed" and idx == "absent":
            print(f"CANARY: sem registro de índice ainda em {a.store} — o eixo é de ESCALADA e a run "
                  f"do canário landou; não é erro. Índice não verificado nesta checagem.")
        elif a.index == "executed" and idx != "executed":
            fails.append(f"index validation is {idx!r}, expected 'executed' "
                         f"(the recommendation would rest on a cost ESTIMATE)")

    # writer instrumentation: 0 tokens means the cost/latency fields are being dropped again
    m = r.get("metrics") or {}
    if writer.startswith("cloud:") and not (m.get("writer_tokens_out") or 0):
        fails.append("writer_tokens_out is 0 — cost instrumentation is not reaching the store")

    if fails:
        print(f"CANARY FAILED for {a.store} — STOP, do not spend the cell:")
        for f in fails:
            print(f"   ✗ {f}")
        return 1
    print(f"CANARY OK for {a.store}: writer={writer} · model={model} · "
          f"rules={'on' if r.get('apply_mode') else 'off'} · tokens_out={m.get('writer_tokens_out')}")
    return 0


def _index_validation(store):
    """A célula CONSEGUE validar índice por execução? — checagem de CAPACIDADE, não de sorteio.

    ⛔ O QUE ESTAVA ERRADO (corrigido 07/09). A versão anterior devolvia o rótulo do **PRIMEIRO**
    registro que o `glob` encontrasse — e a ordem do `glob` é arbitrária. Numa célula com registros
    dos dois tipos, o canário aprovava ou reprovava **por sorteio**.
      · Caso real: o `p1cloud_mistral_flash` tem **25 `executed` e 15 `estimated`** — a célula mede
        índice perfeitamente — e mesmo assim o canário a barrou DUAS vezes (07/09 07:03 e 12:07),
        parando a cadeia da nuvem em ambas.
      · O mesmo defeito existia do outro lado: o C1 passava com 54 `estimated` no meio, porque
        calhava de achar um `executed` antes.

    ✅ A REGRA CERTA: o canário existe para responder *"esta configuração CONSEGUE medir índice
    executando?"*. Uma recomendação que não completou no orçamento não torna a célula incapaz — ela
    apenas fica de fora das claims de índice (o `gen_cell_doc.py` já não imprime percentual sem
    medição). Basta UMA medição bem-sucedida para a capacidade estar demonstrada.
    ⛔ Isto NÃO afrouxa a régua do dado: `estimated` continua fora de toda claim de índice. O que muda
    é só o que BLOQUEIA uma célula inteira.
    """
    vistos = set()
    for p in glob.glob(f".cache/index_{store.replace('suggestions_', '')}/*.json"):
        try:
            r = json.load(open(p))
        except Exception:
            continue
        for rec in (r.get("recommendations") or []):
            if "validation" in rec:
                vistos.add(rec["validation"])
    if not vistos:
        return "absent"
    # capacidade demonstrada por QUALQUER medição bem-sucedida
    return "executed" if "executed" in vistos else sorted(vistos)[0]


if __name__ == "__main__":
    sys.exit(main())
