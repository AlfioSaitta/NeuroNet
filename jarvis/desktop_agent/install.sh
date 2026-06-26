#!/usr/bin/env bash
# =============================================================================
# Installer - Jarvis Desktop Agent per KDE Plasma
# =============================================================================
# Questo script:
#   1. Installa le dipendenze Python necessarie
#   2. Rende eseguibile lo script trigger
#   3. Configura l'autostart di KDE Plasma
#   4. Mostra le istruzioni per impostare la scorciatoia globale
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
AGENT_PY="$SCRIPT_DIR/jarvis_agent.py"
TRIGGER_SH="$SCRIPT_DIR/jarvis-trigger.sh"
DESKTOP_FILE="$SCRIPT_DIR/jarvis-agent.desktop"
AUTOSTART_DIR="${HOME}/.config/autostart"
VENV_DIR="$PROJECT_DIR/venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }
step()  { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║     Jarvis Desktop Agent - Installer     ║"
echo "║       Integrazione KDE Plasma             ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. Verifica requisiti di sistema ──
step "Verifica requisiti di sistema"

HAVE_PULSE=false
HAVE_ALSA=false
HAVE_SOCKET_TOOL=false

if command -v pactl &>/dev/null; then
    info "PulseAudio/PipeWire trovato"
    HAVE_PULSE=true
else
    warn "pactl non trovato (occorre PulseAudio o PipeWire)"
fi

if command -v arecord &>/dev/null; then
    info "ALSA arecord trovato"
    HAVE_ALSA=true
else
    error "arecord non trovato. Installa alsa-utils."
    exit 1
fi

if command -v aplay &>/dev/null; then
    info "ALSA aplay trovato"
else
    warn "aplay non trovato. Installa alsa-utils."
fi

if command -v paplay &>/dev/null; then
    info "PulseAudio paplay trovato"
fi

if command -v ffplay &>/dev/null; then
    info "ffplay trovato"
elif command -v ffmpeg &>/dev/null; then
    info "ffmpeg trovato (userà ffmpeg per conversione)"
else
    warn "ffmpeg non trovato. Installa ffmpeg."
fi

if command -v nc &>/dev/null; then
    HAVE_SOCKET_TOOL=true
    info "nc (netcat) trovato"
elif command -v socat &>/dev/null; then
    HAVE_SOCKET_TOOL=true
    info "socat trovato"
else
    error "nc o socat necessari per la comunicazione socket."
    exit 1
fi

if python3 -c "
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Notify
try:
    gi.require_version('AppIndicator3', '0.1')
except ValueError:
    gi.require_version('AyatanaAppIndicator3', '0.1')
print('OK')
" 2>/dev/null; then
    info "PyGObject con AppIndicator: OK"
else
    warn "PyGObject/AppIndicator non completi. Prova: zypper install python3-gobject"
    warn "La tray icon non funzionerà senza AppIndicator3 o AyatanaAppIndicator3."
fi

# ── 2. Installa dipendenze Python ──
step "Installazione dipendenze Python"

if command -v pip3 &>/dev/null; then
    info "pip3 trovato"
else
    error "pip3 non trovato"
    exit 1
fi

# Verifica se esiste un virtualenv attivo
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    info "Virtualenv trovato in $VENV_DIR"
    PYTHON="$VENV_DIR/bin/python"
    PIP="$VENV_DIR/bin/pip"
else
    warn "Nessun virtualenv trovato. Uso pip globale."
    PYTHON="python3"
    PIP="pip3"
fi

$PIP install --upgrade faster-whisper gtts Pillow 2>&1 | tail -5
info "Dipendenze Python installate (faster-whisper, gTTS, Pillow)"

# ── 3. Prepara script trigger e shortcut ──
step "Preparazione script"

chmod +x "$TRIGGER_SH"
info "Trigger script reso eseguibile: $TRIGGER_SH"

chmod +x "$SCRIPT_DIR/setup-kde-shortcut.sh"
info "Script shortcut reso eseguibile"

# ── 4. Configura autostart KDE ──
step "Configurazione autostart KDE Plasma"

mkdir -p "$AUTOSTART_DIR"
cp "$DESKTOP_FILE" "$AUTOSTART_DIR/jarvis-agent.desktop"
info "File autostart copiato in $AUTOSTART_DIR/jarvis-agent.desktop"

# ── 5. Imposta scorciatoia globale (opzionale) ──
step "Impostazione scorciatoia globale KDE"

if command -v kwriteconfig5 &>/dev/null; then
    echo "Rilevato KDE Plasma. Vuoi configurare la scorciatoia Meta+V per Jarvis?"
    echo "Premi INVIO per configurare automaticamente, o CTRL+C per saltare."
    read -r -t 5 || true
    bash "$SCRIPT_DIR/setup-kde-shortcut.sh"
else
    echo ""
    echo "Per attivare Jarvis con una combinazione di tasti:"
    echo ""
    echo "  1. Apri Impostazioni di Sistema → Scorciatoie da tastiera"
    echo "     (Oppure: System Settings → Shortcuts → Custom Shortcuts)"
    echo ""
    echo "  2. Crea una nuova scorciatoia globale:"
    echo "     · Clicca 'Modifica' → 'Nuovo' → 'Comando personalizzato'"
    echo "     · Nome: Jarvis - Parla"
    echo "     · Comando: ${TRIGGER_SH} toggle"
    echo "     · Trigger: seleziona una combinazione (es. Meta+V o Meta+Spazio)"
    echo ""
    echo "  3. (Opzionale) Crea una seconda scorciatoia:"
    echo "     · Nome: Jarvis - Scrivi"
    echo "     · Comando: ${TRIGGER_SH} text"
    echo "     · Trigger: es. Meta+Shift+V"
    echo ""
fi

echo ""
echo "Per avviare Jarvis Agent ora:"
echo "  $PYTHON $AGENT_PY &"
echo ""

# ── 6. Avvio ──
step "Avvio Jarvis Agent"

if pgrep -f "jarvis_agent.py" >/dev/null 2>&1; then
    warn "Jarvis Agent è già in esecuzione"
else
    echo "Avvio Jarvis Agent in background..."
    nohup "$PYTHON" "$AGENT_PY" > /tmp/jarvis-agent.log 2>&1 &
    sleep 2
    if pgrep -f "jarvis_agent.py" >/dev/null 2>&1; then
        info "Jarvis Agent avviato (PID: $(pgrep -f jarvis_agent.py))"
    else
        error "Avvio fallito. Controlla /tmp/jarvis-agent.log"
    fi
fi

echo ""
info "Installazione completata!"
echo ""
echo "Jarvis Agent è in esecuzione con icona nella system tray."
echo "Clicca l'icona per parlare, o usa la scorciatoia configurata."
echo ""
echo "Per disinstallare:"
echo "  rm -f ${AUTOSTART_DIR}/jarvis-agent.desktop"
echo "  pkill -f jarvis_agent.py"
echo "  rm -rf ~/.config/jarvis-agent"
echo ""
