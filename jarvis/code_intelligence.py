"""
Hybrid Code Intelligence — fonde RAG vettoriale (Qdrant) + Synaptiq (grafo strutturale).

Reindirizza a synaptiq_bridge per l'implementazione effettiva.
Mantiene la stessa API pubblica (hybrid_code_search) per retrocompatibilità.

Usage:
    from code_intelligence import hybrid_code_search
    ctx = await hybrid_code_search("come funziona il telemetry collector?", project_name="NeuroNet")
"""

from synaptiq_bridge import hybrid_code_search  # noqa: F401  — re-export per retrocompat

# Il resto dell'implementazione è delegato a synaptiq_bridge.
# Questa funzione è identica a synaptiq_bridge.hybrid_code_search:
#   - RAG search via Qdrant
#   - Synaptiq structural search (simboli, callers, callees, blast radius)
