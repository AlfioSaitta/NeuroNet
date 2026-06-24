#!/bin/bash
# agy2jarvis — Invia un prompt ad agy, istruisce Jarvis via RAG (file) o Mem0 (ricordo)
# Uso:
#   ./agy2jarvis.sh --rag [--project Nome] <prompt>
#   ./agy2jarvis.sh --mem0 [--project Nome] <prompt>
#
# Esempi:
#   ./agy2jarvis.sh --rag --project NeuroNet \
#     "Cerca documentazione su Qdrant e crea una guida"
#   ./agy2jarvis.sh --mem0 --project NeuroNet \
#     "Alfio preferisce deploy con docker-compose rebuild"
#   echo "prompt" | ./agy2jarvis.sh --mem0

set -euo pipefail

JARVIS_URL="${JARVIS_URL:-http://localhost:8000/api/chat}"
AGY_BIN="${AGY_BIN:-$(command -v agy)}"
DOCS_DIR="${DOCS_DIR:-/home/alfio/Projects/ai-ecosystem/data/documents}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/alfio/Projects}"

usage() {
    echo "Uso: $0 --rag|--mem0 [--project Nome] [--project-path /path] <prompt>"
    echo "     echo 'prompt' | $0 --rag|--mem0"
    echo ""
    echo "Modalità:"
    echo "  --rag             Salva come file → watchdog RAG lo indicizza in Qdrant"
    echo "  --mem0            Salva come ricordo → Mem0 (richiamato nel super-prompt)"
    echo ""
    echo "Opzioni:"
    echo "  --project Nome    Nome progetto per RAG/Mem0"
    echo "  --project-path P  Path radice del progetto (default: PROJECTS_DIR/Nome/docs/rag)"
    echo ""
    echo "Esempi percorsi RAG:"
    echo "  --project NeuroNet              → PROJECTS_DIR/NeuroNet/docs/rag/"
    echo "  --project NeuroNet --project-path /home/alfio/Projects/ai-ecosystem"
    echo "                                  → /home/alfio/Projects/ai-ecosystem/docs/rag/"
    echo ""
    echo "Variabili ambiente:"
    echo "  JARVIS_URL    (default: http://localhost:8000/api/chat)"
    echo "  AGY_BIN       (default: auto-detect)"
    echo "  DOCS_DIR      (default: $DOCS_DIR, usato se --project non specificato)"
    echo "  PROJECTS_DIR  (default: $PROJECTS_DIR)"
    exit 1
}

# ── Parse argomenti ──
mode=""
project=""
project_path=""
prompt_args=()

while [ $# -gt 0 ]; do
    case "$1" in
        --rag|--mem0)
            mode="${1#--}"
            shift
            ;;
        --project)
            project="$2"
            shift 2
            ;;
        --project=*)
            project="${1#*=}"
            shift
            ;;
        --project-path)
            project_path="$2"
            shift 2
            ;;
        --project-path=*)
            project_path="${1#*=}"
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            prompt_args+=("$1")
            shift
            ;;
    esac
done

if [ -z "$mode" ]; then
    echo "❌ Specifica --rag o --mem0"
    usage
fi

if [ -z "$AGY_BIN" ]; then
    echo "❌ agy non trovato. Imposta AGY_BIN o installa agy."
    exit 1
fi

# Prompt: da argomenti o stdin
if [ ${#prompt_args[@]} -ge 1 ]; then
    prompt="${prompt_args[*]}"
elif [ ! -t 0 ]; then
    prompt=$(cat)
else
    usage
fi

# ── Esecuzione agy ──
echo "🧠 Invio ad agy (modalità: $mode${project:+, progetto: $project})..."
agy_output=$("$AGY_BIN" -p "$prompt" 2>/dev/null) || {
    echo "❌ agy è fallito."
    exit 1
}

echo ""
echo "═══════════════════════════════════════════"
echo "📝 Risposta di agy:"
echo "$agy_output"
echo "═══════════════════════════════════════════"

read -r -p $'\nInviare a Jarvis? [Y/n] ' reply
case "$reply" in
    n|N|no|NO)
        echo "❌ Annullato."
        exit 0
        ;;
esac

# ── Modalità RAG: scrive file + salva sommario in Mem0 ──
if [ "$mode" = "rag" ]; then
    safe_name=$(echo "$prompt" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9-')
    safe_name="${safe_name:0:60}"
    ts=$(date +%Y%m%d-%H%M%S)

    if [ -n "$project_path" ]; then
        target_dir="$project_path/docs/rag"
    elif [ -n "$project" ]; then
        target_dir="$PROJECTS_DIR/$project/docs/rag"
    else
        target_dir="$DOCS_DIR"
    fi
    mkdir -p "$target_dir"
    filepath="$target_dir/${ts}-${safe_name}.md"
    # Path visibile dentro il container Docker (via symlink EXTERNAL_PROJECTS)
    if [ -n "$project" ]; then
        container_path="/app/documents/$project/docs/rag/${ts}-${safe_name}.md"
    else
        container_path="$filepath"
    fi

    echo "$agy_output" > "$filepath"
    echo "💾 File scritto: $filepath"
    echo "   → watchdog RAG lo indicizzerà in Qdrant (progetto: ${project:-default})"

    # Invia a Mem0: contenuto troncato (800 char) + riferimento al file
    # Così Jarvis ha risposta immediata via Mem0 e dettagli via RAG
    echo "📤 Invio a Jarvis (Mem0 + RAG)..."
    response=$(curl -s --max-time 30 -X POST "$JARVIS_URL" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json, sys
agy_out = sys.argv[1]
project = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ''
container_path = sys.argv[3]

# Primi 700 char del contenuto come sommario Mem0
summary = agy_out[:700].rstrip()
if len(agy_out) > 700:
    summary += '...'

project_tag = f' (progetto: {project})' if project else ''
memory = (
    f'<MEMORY>📄 Documento: {container_path}{project_tag}\n'
    f'{summary}\n'
    f'Fonte completa disponibile nel RAG.</MEMORY>'
)
body = {
    'model': 'local',
    'messages': [{'role': 'user', 'content': memory}],
    'options': {'skip_rag': True, 'concise': True},
    'stream': False
}
print(json.dumps(body))
" "$agy_output" "$project" "$container_path")") || {
        echo "❌ Jarvis non raggiungibile a $JARVIS_URL"
        exit 1
    }

    echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
content = d.get('message', {}).get('content', '') or d.get('response', '')
if content:
    print('✅ Jarvis:', content[:200])
else:
    print('✅ Memoria salvata')
"
fi

# ── Modalità Mem0: invia a Jarvis come chat con <MEMORY> ──
if [ "$mode" = "mem0" ]; then
    project_tag=""
    if [ -n "$project" ]; then
        project_tag=" (progetto: $project)"
    fi

    echo "📤 Invio a Jarvis (Mem0)..."
    response=$(curl -s --max-time 30 -X POST "$JARVIS_URL" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json, sys
msg = sys.argv[1]
project = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ''
# Wrappa in <MEMORY> per salvataggio automatico in Mem0
memory_text = msg.replace('<MEMORY>', '').replace('</MEMORY>', '')
content = f'<MEMORY>{memory_text}</MEMORY>'
body = {
    'model': 'local',
    'messages': [{'role': 'user', 'content': content}],
    'options': {'skip_rag': True, 'concise': True},
    'stream': False
}
print(json.dumps(body))
" "$agy_output" "$project")") || {
        echo "❌ Jarvis non raggiungibile a $JARVIS_URL"
        exit 1
    }

    echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
content = d.get('message', {}).get('content', '') or d.get('response', '')
if content:
    print('✅ Jarvis:', content[:300])
else:
    print('✅ Inviato (nessuna risposta)')
"
fi
