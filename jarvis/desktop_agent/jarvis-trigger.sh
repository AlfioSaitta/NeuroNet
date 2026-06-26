#!/usr/bin/env bash
# Jarvis Desktop Agent - Trigger Script
# Invia comandi al demone Jarvis via Unix socket.
# Usato dalle scorciatoie globali di KDE Plasma.
# Usage: ./jarvis-trigger.sh [record|toggle|text|stop|status]

set -euo pipefail

SOCKET_PATH="/tmp/jarvis-agent.sock"
COMMAND="${1:-toggle}"

if [ ! -S "$SOCKET_PATH" ]; then
    notify-send -i face-smile "Jarvis Agent" "Il demone non è in esecuzione. Avvia jarvis-agent.py prima." -t 5000
    exit 1
fi

echo -n "$COMMAND" | nc -U -w 2 "$SOCKET_PATH" 2>/dev/null || {
    echo "$COMMAND" | socat - UNIX-CONNECT:"$SOCKET_PATH" 2>/dev/null || {
        notify-send -i face-smile "Jarvis Agent" "Errore comunicazione socket" -t 3000
        exit 1
    }
}
