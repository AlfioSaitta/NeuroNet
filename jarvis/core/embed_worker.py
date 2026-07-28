"""
Embedding Worker — Subprocess isolato per il modello embedding.

STRATEGIA:
Il modello embedding (Qwen3-Embedding-0.6B) viene caricato in un subprocess
con CUDA_VISIBLE_DEVICES=-1, impedendo a llama-cpp-python di inizializzare
CUDA. Questo evita la frammentazione della VRAM (457 MiB di CUDA context)
che altrimenti impedirebbe l'allocazione contigua dei pesi del chat model
full GPU (3334 MiB).

Il subprocess comunica via JSON lines su stdin/stdout:
  Request:  {"id": 1, "texts": ["testo1", "testo2"]}
  Response: {"id": 1, "data": [{"embedding": [0.1, 0.2, ...]}, ...], "error": null}

Il worker padre (EmbedWorker) gestisce lifecycle, restart, timeout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import select
import subprocess
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Script eseguito dal subprocess ──────────────────────────────

_EMBED_WORKER_SCRIPT = r"""
import json, os, sys

# Impedisce a llama-cpp-python di vedere la GPU
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

from llama_cpp import Llama

MODEL_PATH = {model_path!r}
N_GPU_LAYERS = 0
N_CTX = 8192
N_BATCH = 256
N_THREADS = 4

try:
    model = Llama(
        model_path=MODEL_PATH, embedding=True,
        n_gpu_layers=N_GPU_LAYERS, n_ctx=N_CTX,
        n_batch=N_BATCH, n_threads=N_THREADS, verbose=False,
        pooling=2,
    )
    sys.stderr.write(f"[EMBED_WORKER] Model loaded: {{MODEL_PATH}}\\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write(f"[EMBED_WORKER] FAILED to load model: {{e}}\\n")
    sys.stderr.flush()
    sys.exit(1)

# Warmup: prima chiamata è lenta (cold start CPU), la facciamo subito
try:
    model.create_embedding(["warmup"])
    sys.stderr.write("[EMBED_WORKER] Warmup completato\\n")
    sys.stderr.flush()
except Exception as we:
    sys.stderr.write(f"[EMBED_WORKER] Warmup fallito (non critico): {{we}}\\n")
    sys.stderr.flush()

# Segnala al padre che è pronto (letta da start() stdout.readline())
sys.stdout.write("READY\n")
sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        req_id = req.get("id", 0)
        texts = req.get("texts", [])
        if isinstance(texts, str):
            texts = [texts]
        result = model.create_embedding(texts)
        resp = {{"id": req_id, "data": result.get("data", []), "error": None}}
    except Exception as e:
        resp = {{"id": req_id, "data": [], "error": str(e)}}
    sys.stdout.write(json.dumps(resp) + "\\n")
    sys.stdout.flush()
"""


class EmbedWorker:
    """Gestisce un subprocess per il modello embedding su CPU isolato.

    Il subprocess viene avviato al primo utilizzo (lazy) e resta in
    ascolto su stdin/stdout. Se crasha, viene riavviato automaticamente.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._ready = False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self, timeout: float = 30.0) -> bool:
        """Avvia il subprocess embed worker e attende che sia pronto.

        Legge stderr dal subprocess finché non vede il messaggio di
        conferma caricamento modello. Se il modello non si carica entro
        `timeout` secondi, ritorna False.

        Args:
            timeout: Secondi massimi di attesa per il caricamento.

        Returns:
            True se avviato e pronto, False altrimenti.
        """
        with self._lock:
            if self._proc is not None and self._ready:
                return True
            # Se il processo esiste ma non è ready, kill e riavvia
            if self._proc is not None:
                self._stop()
            try:
                script = _EMBED_WORKER_SCRIPT.format(model_path=self.model_path)
                self._proc = subprocess.Popen(
                    [sys.executable, "-c", script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                # Attende "READY" su stdout dal subprocess (modello caricato)
                # Usa select per timeout — stdout.readline() è bloccante
                try:
                    r, _, _ = select.select([self._proc.stdout], [], [], timeout)
                    ready_line = ""
                    if r:
                        ready_line = self._proc.stdout.readline() or ""
                    if ready_line.strip() == "READY":
                        self._ready = True
                        logger.info(f"✅ Embed Worker pronto (PID {self._proc.pid})")
                        return True
                    # Timeout o risposta inattesa
                    _exit = self._proc.poll()
                    _stderr = ""
                    if self._proc.stderr:
                        try:
                            _stderr = self._proc.stderr.read(500)
                        except Exception:
                            pass
                    logger.error(f"Embed Worker: atteso READY, ricevuto '{ready_line.strip()}' "
                                f"(exit={_exit}, stderr={_stderr[:200]})")
                    self._stop()
                    return False
                except Exception as e:
                    logger.error(f"Embed Worker: errore lettura READY: {e}")
                    self._stop()
                    return False
            except Exception as e:
                logger.error(f"❌ Embed Worker avvio fallito: {e}")
                self._proc = None
                return False

    def _stop(self):
        """Termina il subprocess (senza lock — chiamato da dentro start())."""
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
            self._proc.wait(timeout=2)
        self._proc = None
        self._ready = False

    def stop(self):
        """Termina il subprocess embed worker."""
        with self._lock:
            self._stop()
            logger.info("Embed Worker fermato")

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Embedding ─────────────────────────────────────────────────

    def get_embeddings(self, texts: list[str], timeout: float = 60.0, _retries: int = 1) -> dict[str, Any]:
        """Invia richiesta di embedding al subprocess e attende risposta.

        Args:
            texts: Lista di testi da embeddare.
            timeout: Secondi massimi di attesa per la risposta.
            _retries: Numero di tentativi rimasti (uso interno).

        Returns:
            Dict con chiavi "data" (lista embedding) e "error" (None se OK).
        """
        if not self._ready or not self.is_alive():
            if not self.start():
                return {"data": [], "error": "Embed worker non disponibile"}

        req_id = self._next_id
        self._next_id += 1
        req = json.dumps({"id": req_id, "texts": texts})

        # Scrive richiesta e attende risposta (con timeout)
        _need_retry = False
        _error_msg = ""
        with self._lock:
            try:
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                # Attende risposta con timeout (select evita blocco infinito)
                r, _, _ = select.select([self._proc.stdout], [], [], timeout)
                if not r:
                    logger.warning(f"⏱ Embed worker timeout ({timeout}s) per richiesta {req_id}")
                    _need_retry = True
                    _error_msg = f"Timeout after {timeout}s"
                else:
                    resp_line = self._proc.stdout.readline()
                    if not resp_line:
                        _need_retry = True
                        _error_msg = "Subprocess stdout chiuso"
                        raise BrokenPipeError(_error_msg)
                    resp = json.loads(resp_line)
                    return resp
            except Exception as e:
                logger.warning(f"⚠️ Embed worker errore: {e}, riavvio...")
                _need_retry = True
                _error_msg = str(e)

        # Se c'è stato errore, rilascia lock prima di riavviare
        if _need_retry and _retries > 0:
            self._stop()  # senza lock (lock già rilasciato dall'uscita del with)
            return self.get_embeddings(texts, timeout, _retries - 1)
        return {"data": [], "error": _error_msg}


# ── Helper asincrono ─────────────────────────────────────────────

_worker_instance: EmbedWorker | None = None
_worker_lock = asyncio.Lock()


async def get_embed_worker(model_path: str) -> EmbedWorker | None:
    """Restituisce l'istanza singleton dell'EmbedWorker, avviandola se necessario."""
    global _worker_instance
    if _worker_instance is not None and _worker_instance.is_alive():
        return _worker_instance
    async with _worker_lock:
        if _worker_instance is not None and _worker_instance.is_alive():
            return _worker_instance
        worker = EmbedWorker(model_path)
        if worker.start():
            _worker_instance = worker
            return worker
        return None


async def stop_embed_worker():
    """Ferma l'istanza globale dell'EmbedWorker."""
    global _worker_instance
    if _worker_instance:
        _worker_instance.stop()
        _worker_instance = None
