import os
import json
import logging
import asyncssh
import asyncio

logger = logging.getLogger("chameleon.infra")

INFRA_FILE = "/home/alfio/Projects/ai-ecosystem/mem0-proxy/infrastructure.json"

def load_infra():
    if os.path.exists(INFRA_FILE):
        try:
            with open(INFRA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading infrastructure: {e}")
    return {}

def save_infra(data):
    try:
        with open(INFRA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving infrastructure: {e}")

async def run_on_server(server_name, command):
    infra = load_infra()
    if server_name not in infra:
        return f"Errore: Server '{server_name}' non configurato."
    
    server = infra[server_name]
    ip = server.get('ip')
    user = server.get('user')
    key_path = server.get('key_path')
    
    try:
        client_keys = [key_path] if key_path else None
        async with asyncssh.connect(ip, username=user, client_keys=client_keys, known_hosts=None) as conn:
            result = await conn.run(command)
            if result.exit_status == 0:
                return f"[SSH Output ({server_name})]:\n{result.stdout}"
            else:
                return f"[SSH Error ({server_name} - {result.exit_status})]:\n{result.stderr}"
    except Exception as e:
        return f"SSH Connection failed: {str(e)}"
