#!/bin/bash

# ==============================================================================
# Script di avvio per il Master Node (VPS Cloud)
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}  Avvio JARVIS - MASTER CLOUD NODE                  ${NC}"
echo -e "${CYAN}====================================================${NC}"

# Avvia l'intero stack (DB, Mem0, SearxNG, Crawl4Ai, Jarvis)
echo -e "${YELLOW}📦 Avvio dell'intero ecosistema Docker...${NC}"
docker compose -f docker-compose.vps.yml down
docker compose -f docker-compose.vps.yml up -d

echo -e "${GREEN}✅ Ecosistema Master online.${NC}"
