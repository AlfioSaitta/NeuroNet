#!/bin/bash
# ==============================================================================
# SYNC TO MASTER — Sincronizza i dati locali (Worker) verso il Master (VPS)
# ==============================================================================
# Eseguire dal laptop Worker per allineare Qdrant e Mem0 con la VPS prima
# di passare dalla modalità Offline a quella Online.
#
# Prerequisiti:
#   1. Tailscale attivo su entrambi i nodi (o accesso SSH diretto via IP pubblico)
#   2. Chiave SSH configurata: /home/alfio/.ssh/ovh_rsa
#
# Uso:
#   ./sync_to_master.sh             # Usa IP pubblico (pre-Tailscale)
#   MASTER_IP=100.64.0.1 ./sync_to_master.sh   # Usa IP Tailscale (post-Tailscale)
# ==============================================================================

set -e

# Configurazione
MASTER_USER="debian"
MASTER_IP="${MASTER_IP:-51.38.135.179}"      # IP pubblico VPS (default pre-Tailscale)
# MASTER_IP="${MASTER_IP:-100.64.0.1}"        # Decommentare dopo aver configurato Tailscale
MASTER_PATH="/home/debian/ai-ecosystem"
SSH_KEY="/home/alfio/.ssh/ovh_rsa"

echo "======================================================"
echo "🔄 Sincronizzazione dati → Master (${MASTER_USER}@${MASTER_IP})"
echo "======================================================"
echo ""

# Verifica connettività SSH prima di procedere
echo "🔌 Verifica connettività SSH..."
if ! ssh -i "${SSH_KEY}" -o ConnectTimeout=5 -o BatchMode=yes "${MASTER_USER}@${MASTER_IP}" "echo OK" &>/dev/null; then
    echo "❌ ERRORE: Impossibile raggiungere il Master via SSH."
    echo "   Verifica:"
    echo "   1. IP corretto: ${MASTER_IP}"
    echo "   2. Chiave SSH: ${SSH_KEY}"
    echo "   3. Tailscale attivo (se usi IP VPN)"
    exit 1
fi
echo "✅ Connessione SSH OK"
echo ""

# Sincronizza Qdrant (database vettoriale)
echo "📦 Sincronizzazione data/qdrant/ ..."
rsync -avzP --delete \
    -e "ssh -i ${SSH_KEY}" \
    data/qdrant/ \
    "${MASTER_USER}@${MASTER_IP}:${MASTER_PATH}/data/qdrant/"
echo ""

# Sincronizza Mem0 + sessioni Userbot Telegram
echo "🧠 Sincronizzazione data/jarvis_mem0/ ..."
rsync -avzP --delete \
    -e "ssh -i ${SSH_KEY}" \
    data/jarvis_mem0/ \
    "${MASTER_USER}@${MASTER_IP}:${MASTER_PATH}/data/jarvis_mem0/"
echo ""

echo "======================================================"
echo "✅ Sincronizzazione completata!"
echo "   Master aggiornato: ${MASTER_USER}@${MASTER_IP}:${MASTER_PATH}"
echo "======================================================"
