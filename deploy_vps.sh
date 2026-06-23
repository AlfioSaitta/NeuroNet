#!/bin/bash

# ==============================================================================
# Script di Deploy di Jarvis su VPS OVH (Architettura Master-Worker Edge/Cloud)
# ==============================================================================

# Colori per output testuale
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

if [ -z "$1" ]; then
    echo -e "${RED}Errore: Nessun IP fornito.${NC}"
    echo "Uso: ./deploy_vps.sh <INDIRIZZO_IP_VPS>"
    exit 1
fi

VPS_IP=$1
VPS_USER="debian"
SSH_KEY="/home/alfio/.ssh/ovh_rsa"
REMOTE_DIR="/home/debian/ai-ecosystem"

echo -e "${YELLOW}🚀 Inizio procedura di deploy su $VPS_IP...${NC}"

# 1. Test connessione SSH e creazione cartella di destinazione
echo -e "${YELLOW}🔗 Test connessione SSH e setup cartella remota...${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$VPS_USER@$VPS_IP" "mkdir -p $REMOTE_DIR"
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Connessione SSH fallita. Verifica IP e chiave RSA.${NC}"
    exit 1
fi

# 2. Sincronizzazione dei file (RSYNC)
echo -e "${YELLOW}📦 Sincronizzazione dei file locali (Rsync) ignorando i container e i file temporanei...${NC}"
rsync -avz --delete \
    -e "ssh -i $SSH_KEY" \
    --exclude '.git' \
    --exclude 'data/' \
    --exclude 'qdrant_storage/' \
    --exclude '__pycache__/' \
    --exclude 'node_modules/' \
    --exclude '.env' \
    /home/alfio/Projects/ai-ecosystem/ "$VPS_USER@$VPS_IP:$REMOTE_DIR/"

# 3. Trasferimento file .env
echo -e "${YELLOW}🔐 Trasferimento file .env (assicurati di aggiungere EXTERNAL_GPU_URL in futuro)...${NC}"
if [ -f "/home/alfio/Projects/ai-ecosystem/.env" ]; then
    scp -i "$SSH_KEY" /home/alfio/Projects/ai-ecosystem/.env "$VPS_USER@$VPS_IP:$REMOTE_DIR/.env"
else
    echo -e "${RED}⚠️ Nessun file .env trovato localmente, ricordati di crearlo sul VPS!${NC}"
fi

# 4. Esecuzione comandi sul VPS (Installazione Docker e avvio stack)
echo -e "${YELLOW}⚙️ Installazione Docker e avvio stack su VPS...${NC}"
ssh -i "$SSH_KEY" "$VPS_USER@$VPS_IP" << 'EOF'
    echo "Installando Docker se non presente..."
    if ! command -v docker &> /dev/null; then
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker $USER
        rm get-docker.sh
    fi
    if ! command -v docker-compose &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y docker-compose-plugin
    fi



    echo "Avviando i container..."
    cd /home/debian/ai-ecosystem
    # Ricreiamo le cartelle dati se non esistono
    mkdir -p data/mem0 data/qdrant models
    
    # Riavvio dello stack
    sudo docker compose down
    sudo docker compose up -d --build
    
    echo "Pulizia immagini Docker inutilizzate..."
    sudo docker image prune -f
EOF

echo -e "${GREEN}✅ Deploy completato con successo!${NC}"
echo -e "Ora la tua VPS funge da Mente. Ricordati di aggiungere nel file .env della VPS la variabile:"
echo -e "per abilitare il forwarding delle inferenze alla tua GPU locale."
