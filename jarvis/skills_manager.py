import os
import yaml
import logging
from typing import List, Dict, Any, Tuple
import subprocess

logger = logging.getLogger("chameleon.skills")

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

def ensure_skills_dir():
    if not os.path.exists(SKILLS_DIR):
        os.makedirs(SKILLS_DIR)
        # Create a sample skill to demonstrate
        sample_skill = {
            "name": "docker_deploy",
            "description": "Esegue il deploy di un progetto tramite docker-compose",
            "parameters": {
                "project_path": {
                    "type": "string",
                    "description": "Il percorso assoluto del progetto contenente docker-compose.yml"
                }
            },
            "commands": [
                "cd {project_path} && docker-compose down",
                "cd {project_path} && docker-compose up -d --build"
            ]
        }
        with open(os.path.join(SKILLS_DIR, "docker_deploy.yaml"), "w") as f:
            yaml.dump(sample_skill, f, sort_keys=False)

def get_skills_schemas() -> List[Dict[str, Any]]:
    ensure_skills_dir()
    schemas = []
    
    for filename in os.listdir(SKILLS_DIR):
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            filepath = os.path.join(SKILLS_DIR, filename)
            try:
                with open(filepath, 'r') as f:
                    skill_data = yaml.safe_load(f)
                    
                if not skill_data or "name" not in skill_data:
                    continue
                    
                properties = {}
                required = []
                for param_name, param_info in skill_data.get("parameters", {}).items():
                    properties[param_name] = {
                        "type": param_info.get("type", "string"),
                        "description": param_info.get("description", "")
                    }
                    if param_info.get("required", True):
                        required.append(param_name)
                        
                schema = {
                    "type": "function",
                    "function": {
                        "name": f"skill_{skill_data['name']}",
                        "description": f"[SKILL DINAMICA] {skill_data.get('description', '')}",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required
                        }
                    }
                }
                schemas.append(schema)
            except Exception as e:
                logger.error(f"Errore nel caricamento della skill {filename}: {e}")
                
    return schemas

def get_skill_definition(skill_name: str) -> Dict[str, Any]:
    # Rimuove il prefisso "skill_" usato nel nome del tool
    if skill_name.startswith("skill_"):
        skill_name = skill_name[6:]
        
    for filename in os.listdir(SKILLS_DIR):
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            filepath = os.path.join(SKILLS_DIR, filename)
            try:
                with open(filepath, 'r') as f:
                    skill_data = yaml.safe_load(f)
                if skill_data and skill_data.get("name") == skill_name:
                    return skill_data
            except Exception:
                pass
    return None

async def execute_dynamic_skill(skill_name: str, kwargs: Dict[str, Any]) -> str:
    """Esegue sequenzialmente i comandi bash definiti in un file YAML Skill."""
    skill_data = get_skill_definition(skill_name)
    if not skill_data:
        return f"❌ Errore: Skill '{skill_name}' non trovata."
        
    commands = skill_data.get("commands", [])
    if not commands:
        return f"⚠️ Skill '{skill_name}' eseguita, ma non contiene comandi."
        
    results = []
    import asyncio
    
    for cmd_template in commands:
        try:
            # Sostituisce i parametri template nel comando
            # Usiamo .format() per {parametro}
            cmd = cmd_template.format(**kwargs)
            
            # Esecuzione comando
            logger.info(f"Esecuzione Skill Command: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            out = stdout.decode().strip()
            err = stderr.decode().strip()
            
            # Troncamento log
            if len(out) > 2000: out = out[:1000] + "\n...[TRUNCATED]...\n" + out[-1000:]
            if len(err) > 2000: err = err[:1000] + "\n...[TRUNCATED]...\n" + err[-1000:]
            
            if proc.returncode == 0:
                results.append(f"✅ [{cmd}]:\n{out}")
            else:
                results.append(f"❌ [{cmd}] FALLITO:\nOut: {out}\nErr: {err}")
                results.append("Interruzione sequenza skill per errore.")
                break
                
        except KeyError as e:
            results.append(f"❌ Errore template: Manca il parametro {e} nel comando '{cmd_template}'")
            break
        except Exception as e:
            results.append(f"❌ Errore imprevisto nell'esecuzione di '{cmd_template}': {e}")
            break
            
    return "\n\n".join(results)
