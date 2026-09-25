#!/usr/bin/env bash
# P2 PROBES — two cheap decisions before spending the campaign.
#
#   0a  PLAN probe   (US$ ~1.05) — q69 + q28, plan ON x OFF, in the FINAL composition.
#       Protects the US$ 8.38 of cells B + B2: if the plan no longer unlocks these two, the
#       July ablation depended on the old config (no masking, no schema-linking, with rules)
#       and the argument must be reframed BEFORE paying for the full ablation.
#
#   0b  WRITER probe (US$ ~3.03) — q7, q27, q63 and q1 on BOTH tiers, same composition.
#       Protects the US$ 18.94 saved by switching to flash: the flash-vs-pro comparison we have
#       was measured with rules ON and the masking leaking — a configuration we no longer ship.
#       q1 is the CONTROL (5/5 on both, identical gain): if it diverges, something in the new
#       composition changed and no other conclusion from this probe is trustworthy.
#
# Nothing is wasted: every `flash` run lands in the SAME store cells A/A2/B/B2 will use, so
# --resume picks them up later. Only the `pro` half is exclusive to the probe.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
F=scripts/campanhas/final_battery.sh
run(){ echo "### $*"; bash "$F" "$@" || echo "### FAILED: $*"; }

# 0a — plan
run deepseek-v4-flash workload postgres none on "--only q69" on
run deepseek-v4-flash workload postgres none on "--only q69" off
run deepseek-v4-flash fresh    postgres none on "--only q28" on
run deepseek-v4-flash fresh    postgres none on "--only q28" off
# 0b — writer (same 4 queries, both tiers)
run deepseek-v4-flash workload postgres none on "--only q7 q27 q63 q1" on
# ⛔ PERNA PRO DESATIVADA em 09/09/2026 — decisão da usuária: "não vamos mais produzir com pro".
#    O `deepseek-v4-pro` foi aposentado em 14/09/2026 e agora devolve Flash com o nome pro, então
#    esta sonda mediria Flash acreditando medir Pro. O par pro x flash que JÁ temos
#    (`system_pro_*` x `system_flash_*`, n=5) deu INDISTINGUÍVEL — alcance 14x12 no workload e
#    6x6 no fresh — e é evidência que não pode mais ser coletada. Usar aquela, não refazer.
# run deepseek-v4-pro   workload postgres none on "--only q7 q27 q63 q1" on
echo "### SONDAS COMPLETAS"
