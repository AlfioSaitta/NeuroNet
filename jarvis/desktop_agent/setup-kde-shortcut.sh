#!/usr/bin/env bash
# Configura la scorciatoia globale di KDE Plasma per Jarvis Agent
# Crea un custom shortcut che esegue jarvis-trigger.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRIGGER="$SCRIPT_DIR/jarvis-trigger.sh"
SHORTCUT_NAME="Jarvis - Parla"
SHORTCUT_COMMENT="Attiva Jarvis Agent (registrazione vocale)"

if ! command -v kwriteconfig5 &>/dev/null; then
    echo "kwriteconfig5 non trovato. Installare plasma5-workspace o kf6-config."
    echo "Configura manualmente: Impostazioni Sistema → Scorciatoie → Scorciatoie personalizzate"
    exit 1
fi

# Verifica che il trigger sia eseguibile
chmod +x "$TRIGGER"

# Crea la scorciatoia in Plasma
# Usiamo kwriteconfig5 per Plasma 5 / 6
kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data" \
    --key "Count" "2"

# La prima entry è la root
kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_1" \
    --key "Type" "ACTION_DATA_GROUP"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_1" \
    --key "Name" "Jarvis Agent"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_1" \
    --key "Comment" "Scorciatoie per Jarvis Desktop Agent"

# Seconda entry: la scorciatoia per parlare
kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Type" "ACTION_TRIGGER_CALLBACK"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Name" "$SHORTCUT_NAME"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Comment" "$SHORTCUT_COMMENT"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Remote" ""

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "RemoteComment" ""

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Triggers" "Meta+V"

kwriteconfig5 --file "$HOME/.config/khotkeysrc" \
    --group "Data_2" \
    --key "Actions" "$TRIGGER toggle"

echo "Scorciatoia creata: Meta+V → $TRIGGER toggle"
echo ""
echo "Per applicare le modifiche:"
echo "  Riavvia KDE Plasma (Alt+F2 → kquitapp5 plasmashell && kstart5 plasmashell)"
echo "  Oppure: Impostazioni Sistema → Scorciatoie → Ricarica"
echo ""
echo "Oppure configura manualmente:"
echo "  1. Impostazioni Sistema → Scorciatoie da tastiera"
echo "  2. Scorciatoie personalizzate → Modifica → Nuovo → Comando personalizzato"
echo "  3. Nome: Jarvis - Parla"
echo "  4. Comando: $TRIGGER toggle"
echo "  5. Trigger: Meta+V (o la combinazione che preferisci)"
