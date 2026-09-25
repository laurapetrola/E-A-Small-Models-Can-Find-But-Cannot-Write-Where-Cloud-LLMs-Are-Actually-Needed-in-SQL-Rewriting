#!/usr/bin/env bash
# Espera a sonda de paridade terminar e então PAUSA tudo — nada dispara sozinho depois dela.
# Pedido da usuária em 21/08: sair de casa com a situação estável e entendida.
set -u
cd "/home/laurapetrola/projects/Athena-2.0" || exit 1
LOG=logs/campanhas/pause_after_probe.log
log(){ echo "[$(date +%d/%m\ %H:%M:%S)] $*" | tee -a "$LOG"; }

log "aguardando a sonda de paridade terminar..."
while ps -eo args --no-headers | grep -q "[p]robe_hint_parity.sh"; do sleep 60; done
log "sonda terminou — pausando tudo"

# encerra qualquer fila que possa assumir a máquina
for pid in $(ps -eo pid,args --no-headers | grep -E "[q]ueue_p1_peak|[q]ueue_p1_cloud_rest|[q]ueue_plan_verdict|[p]eak_local|[o]ffpeak" | awk '{print $1}'); do
  kill -9 "$pid" 2>/dev/null
done
sleep 3
for pid in $(ps -eo pid,args --no-headers | grep "[r]un_matrix.py" | awk '{print $1}'); do kill "$pid" 2>/dev/null; done
sleep 3
log "estado final:"
.venv/bin/python scripts/cell_status.py probe_hintparity_q69 2>&1 | tee -a "$LOG"
log "###### TUDO PAUSADO — nada dispara sozinho ######"
