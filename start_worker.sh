#!/bin/bash

# ==============================================================================
# Script di avvio per il Worker Locale (GPU Node)
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}  Avvio JARVIS - GPU WORKER NODE                    ${NC}"
echo -e "${CYAN}====================================================${NC}"

# Avvia il container in versione Worker (senza DB)
echo -e "${YELLOW}📦 Avvio container Jarvis (Modalità Worker)...${NC}"
docker compose -f docker-compose.worker.yml down
docker compose -f docker-compose.worker.yml up -d

echo -e "${GREEN}✅ Container avviato sulla porta 8000.${NC}"
