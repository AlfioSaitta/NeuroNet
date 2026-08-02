"""Test articolati per core/hardware.py — Hardware Identity Block.

Copre il piano (03/08):
  1. _run(): esecuzione comandi di sistema con timeout e gestione errori
  2. _detect_gpu(): parsing nvidia-smi (output valido / vuoto / malformato)
  3. _detect_cpu(): parsing /proc/cpuinfo + conteggio threads
  4. _detect_ram(): parsing /proc/meminfo (kB -> GiB) + fallback sysconf
  5. detect_hardware(): cache + mai eccezioni
  6. get_hardware_info(): lazy detection
  7. get_hardware_block(): blocco [HARDWARE IDENTITY] formattato per il prompt

Il modulo usa SOLO stdlib: nessun mock di import necessario.

Run: PYTHONPATH=jarvis python3 -m pytest tests/test_hardware.py -v
"""
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup: package root = jarvis/ ─────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent / "jarvis"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import hardware as HW  # noqa: E402


# ── Fixture: reset cache module-level tra i test ───────────────────────────

@pytest.fixture(autouse=True)
def _reset_cache():
    HW._HW_CACHE = None
    yield
    HW._HW_CACHE = None


# ── Helpers ────────────────────────────────────────────────────────────────

class _ProcResult:
    def __init__(self, stdout: str, stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr


def _fake_open(data: dict[str, str]):
    """Restituisce una funzione open() che serve contenuti per path specifici."""
    def _opener(path, *args, **kwargs):
        if path in data:
            class _F:
                def __enter__(self):
                    return iter(data[path].splitlines(keepends=True))
                def __exit__(self, *a):
                    return False
                def __iter__(self):
                    return iter(data[path].splitlines(keepends=True))
            return _F()
        raise FileNotFoundError(path)
    return _opener


# ── 1. _run() ──────────────────────────────────────────────────────────────

class TestRun:
    def test_success_returns_stripped_stdout(self, monkeypatch):
        monkeypatch.setattr(HW.subprocess, "run", lambda cmd, **kw: _ProcResult("  RTX 3050 Ti  \n"))
        assert HW._run(["nvidia-smi"]) == "RTX 3050 Ti"

    def test_empty_stdout_returns_empty_string(self, monkeypatch):
        monkeypatch.setattr(HW.subprocess, "run", lambda cmd, **kw: _ProcResult(""))
        assert HW._run(["cmd"]) == ""

    def test_exception_returns_empty_string(self, monkeypatch):
        def _boom(*a, **k):
            raise FileNotFoundError("nvidia-smi non esiste")
        monkeypatch.setattr(HW.subprocess, "run", _boom)
        assert HW._run(["nvidia-smi"]) == ""

    def test_timeout_returns_empty_string(self, monkeypatch):
        def _slow(*a, **k):
            raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5)
        monkeypatch.setattr(HW.subprocess, "run", _slow)
        assert HW._run(["nvidia-smi"], timeout=5) == ""

    def test_stderr_is_ignored_when_stdout_present(self, monkeypatch):
        monkeypatch.setattr(HW.subprocess, "run", lambda cmd, **kw: _ProcResult("ok", stderr="ERR"))
        assert HW._run(["cmd"]) == "ok"


# ── 2. _detect_gpu() ───────────────────────────────────────────────────────

class TestDetectGpu:
    def test_valid_nvidia_smi_output(self, monkeypatch):
        monkeypatch.setattr(
            HW, "_run",
            lambda cmd, **kw: "NVIDIA GeForce RTX 3050 Ti Laptop GPU, 4096, 254, 580.159.03",
        )
        out = HW._detect_gpu()
        assert "RTX 3050 Ti" in out
        assert "4096 MiB VRAM" in out
        assert "254 MiB liberi" in out
        assert "driver 580.159.03" in out

    def test_empty_output_falls_back_to_cpu_only(self, monkeypatch):
        monkeypatch.setattr(HW, "_run", lambda cmd, **kw: "")
        assert HW._detect_gpu() == "non rilevata (CPU-only)"

    def test_malformed_output_returns_raw(self, monkeypatch):
        # Meno di 4 campi CSV -> la stringa viene ritornata così com'è.
        monkeypatch.setattr(HW, "_run", lambda cmd, **kw: "solo-una-cosa")
        assert HW._detect_gpu() == "solo-una-cosa"

    def test_query_flags_are_correct(self, monkeypatch):
        captured = {}
        def _run(cmd, **kw):
            captured["cmd"] = cmd
            return "A, 1, 1, 1"
        monkeypatch.setattr(HW, "_run", _run)
        HW._detect_gpu()
        assert "--query-gpu=name,memory.total,memory.free,driver_version" in captured["cmd"]
        assert "--format=csv,noheader,nounits" in captured["cmd"]


# ── 3. _detect_cpu() ───────────────────────────────────────────────────────

class TestDetectCpu:
    def test_parses_model_name_and_threads(self, monkeypatch):
        cpuinfo = "processor : 0\nmodel name : 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz\nprocessor : 1\n"
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/cpuinfo": cpuinfo}))
        monkeypatch.setattr(HW.os, "cpu_count", lambda: 8)
        out = HW._detect_cpu()
        assert "i5-11300H" in out
        assert "8 threads" in out

    def test_no_model_name_returns_nd_with_threads(self, monkeypatch):
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/cpuinfo": "processor : 0\n"}))
        monkeypatch.setattr(HW.os, "cpu_count", lambda: 4)
        assert HW._detect_cpu() == "n/d — 4 threads"

    def test_unreadable_cpuinfo_returns_nd_with_threads(self, monkeypatch):
        monkeypatch.setattr("builtins.open", _fake_open({}))
        monkeypatch.setattr(HW.os, "cpu_count", lambda: 2)
        assert HW._detect_cpu() == "n/d — 2 threads"

    def test_zero_threads_returns_bare_model(self, monkeypatch):
        cpuinfo = "model name : AMD Ryzen 9\n"
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/cpuinfo": cpuinfo}))
        monkeypatch.setattr(HW.os, "cpu_count", lambda: 0)
        assert HW._detect_cpu() == "AMD Ryzen 9"


# ── 4. _detect_ram() ───────────────────────────────────────────────────────

class TestDetectRam:
    def test_meminfo_total_and_available(self, monkeypatch):
        meminfo = "MemTotal:       16191768 kB\nMemFree:         123456 kB\nMemAvailable:    6093028 kB\n"
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/meminfo": meminfo}))
        out = HW._detect_ram()
        assert out.startswith("15.4 GiB totali,")
        assert "5.8 GiB disponibili" in out

    def test_meminfo_without_available_shows_total_only(self, monkeypatch):
        meminfo = "MemTotal:       4194304 kB\n"
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/meminfo": meminfo}))
        assert HW._detect_ram() == "4.0 GiB totali"

    def test_unreadable_meminfo_uses_sysconf_fallback(self, monkeypatch):
        monkeypatch.setattr("builtins.open", _fake_open({}))
        monkeypatch.setattr(HW.os, "sysconf", lambda name: 1024 * 1024 * 8 if "PHYS_PAGES" in name else 4096)
        out = HW._detect_ram()
        assert out.startswith("32.0 GiB")  # 8M pagine * 4KiB = 32 GiB

    def test_unreadable_meminfo_and_sysconf_failure_returns_nd(self, monkeypatch):
        monkeypatch.setattr("builtins.open", _fake_open({}))
        monkeypatch.setattr(HW.os, "sysconf", lambda name: (_ for _ in ()).throw(OSError("no sysconf")))
        assert HW._detect_ram() == "n/d"

    def test_meminfo_total_zero_returns_nd(self, monkeypatch):
        # MemTotal presente ma 0 -> considerato non rilevato.
        meminfo = "MemTotal:       0 kB\nMemAvailable:   0 kB\n"
        monkeypatch.setattr("builtins.open", _fake_open({"/proc/meminfo": meminfo}))
        assert HW._detect_ram() == "n/d"


# ── 5. detect_hardware() ───────────────────────────────────────────────────

class TestDetectHardware:
    def test_returns_all_four_keys(self, monkeypatch):
        monkeypatch.setattr(HW.socket, "gethostname", lambda: "test-host")
        monkeypatch.setattr(HW, "_detect_gpu", lambda: "GPU-X")
        monkeypatch.setattr(HW, "_detect_cpu", lambda: "CPU-X")
        monkeypatch.setattr(HW, "_detect_ram", lambda: "RAM-X")
        info = HW.detect_hardware()
        assert info == {"hostname": "test-host", "gpu": "GPU-X", "cpu": "CPU-X", "ram": "RAM-X"}

    def test_caches_result_between_calls(self, monkeypatch):
        calls = {"n": 0}
        def _detect_gpu():
            calls["n"] += 1
            return f"GPU-{calls['n']}"
        monkeypatch.setattr(HW.socket, "gethostname", lambda: "h")
        monkeypatch.setattr(HW, "_detect_gpu", _detect_gpu)
        monkeypatch.setattr(HW, "_detect_cpu", lambda: "c")
        monkeypatch.setattr(HW, "_detect_ram", lambda: "r")
        first = HW.detect_hardware()
        second = HW.detect_hardware()
        assert first is second  # stessa identica dict (cache)
        assert calls["n"] == 1  # probe eseguito una sola volta

    def test_never_raises_on_probe_failure(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("probe crash")
        monkeypatch.setattr(HW.socket, "gethostname", _boom)
        monkeypatch.setattr(HW, "_detect_gpu", _boom)
        monkeypatch.setattr(HW, "_detect_cpu", _boom)
        monkeypatch.setattr(HW, "_detect_ram", _boom)
        info = HW.detect_hardware()  # deve ritornare il fallback, non lanciare
        assert info["hostname"] == "n/d"
        assert info["gpu"] == "non rilevata"
        assert info["cpu"] == "n/d"
        assert info["ram"] == "n/d"

    def test_partial_failure_keeps_successful_probes(self, monkeypatch):
        # Le probe reali NON lanciano mai: gestiscono internamente e ritornano
        # il fallback ("n/d" / "non rilevata"). Il fallback parziale avviene a
        # livello di singola probe, non di detect_hardware.
        monkeypatch.setattr(HW.socket, "gethostname", lambda: "h")
        monkeypatch.setattr(HW, "_detect_gpu", lambda: "GPU-X")
        monkeypatch.setattr(HW, "_detect_cpu", lambda: "n/d")
        monkeypatch.setattr(HW, "_detect_ram", lambda: "RAM-X")
        info = HW.detect_hardware()
        assert info["hostname"] == "h"
        assert info["gpu"] == "GPU-X"
        assert info["cpu"] == "n/d"
        assert info["ram"] == "RAM-X"


# ── 6. get_hardware_info() ─────────────────────────────────────────────────

class TestGetHardwareInfo:
    def test_lazy_detection_when_cache_empty(self, monkeypatch):
        HW._HW_CACHE = None
        monkeypatch.setattr(HW.socket, "gethostname", lambda: "lazy-host")
        monkeypatch.setattr(HW, "_detect_gpu", lambda: "G")
        monkeypatch.setattr(HW, "_detect_cpu", lambda: "C")
        monkeypatch.setattr(HW, "_detect_ram", lambda: "R")
        info = HW.get_hardware_info()
        assert info["hostname"] == "lazy-host"

    def test_returns_cache_without_redetect(self, monkeypatch):
        HW._HW_CACHE = {"hostname": "cached", "gpu": "G", "cpu": "C", "ram": "R"}
        def _should_not_run():
            raise AssertionError("detect_hardware non deve essere chiamato")
        monkeypatch.setattr(HW, "detect_hardware", _should_not_run)
        assert HW.get_hardware_info() == HW._HW_CACHE


# ── 7. get_hardware_block() ────────────────────────────────────────────────

class TestGetHardwareBlock:
    def _seed(self, **info):
        HW._HW_CACHE = {"hostname": "h", "gpu": "GPU-X", "cpu": "CPU-X", "ram": "RAM-X", **info}

    def test_formats_block_with_all_fields(self, monkeypatch):
        self._seed()
        block = HW.get_hardware_block()
        assert block.startswith("[HARDWARE IDENTITY — REAL hardware of the Jarvis server]")
        assert "- Hostname: h" in block
        assert "- GPU: GPU-X" in block
        assert "- CPU: CPU-X" in block
        assert "- RAM: RAM-X" in block
        assert "Never invent, never deflect." in block

    def test_empty_when_gpu_not_detected_and_hostname_nd(self, monkeypatch):
        self._seed(gpu="non rilevata", hostname="n/d")
        assert HW.get_hardware_block() == ""

    def test_empty_when_gpu_empty_and_hostname_nd(self, monkeypatch):
        self._seed(gpu="", hostname="n/d")
        assert HW.get_hardware_block() == ""

    def test_non_empty_when_gpu_detected_but_hostname_nd(self, monkeypatch):
        # La condizione di vuoto richiede ENTRAMBI: gpu non rilevata E hostname n/d.
        self._seed(gpu="GPU-X", hostname="n/d")
        assert HW.get_hardware_block() != ""

    def test_non_empty_when_hostname_valid_but_gpu_missing(self, monkeypatch):
        self._seed(gpu="non rilevata", hostname="host-ok")
        assert HW.get_hardware_block() != ""

    def test_get_hardware_block_never_raises_on_corrupt_cache(self, monkeypatch):
        HW._HW_CACHE = None
        # Se get_hardware_info fallisce in modo inatteso, il blocco non deve esplodere.
        monkeypatch.setattr(HW, "get_hardware_info", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        assert HW.get_hardware_block() == ""


# ── Integrazione: dati reali coerenti ──────────────────────────────────────

class TestRealEnvironmentIntegration:
    """Su una macchina reale il rilevamento deve produrre un blocco coerente."""

    def test_detect_runs_on_real_system(self):
        # Esegue il rilevamento REALE (senza mock). Non deve mai lanciare
        # e deve popolare la cache con tutte e 4 le chiavi.
        info = HW.detect_hardware()
        assert set(info.keys()) == {"hostname", "gpu", "cpu", "ram"}

    def test_block_on_real_system_is_well_formed(self):
        block = HW.get_hardware_block()
        # Se il rilevamento ha trovato almeno GPU o hostname, il blocco esiste.
        info = HW.get_hardware_info()
        if info["gpu"] != "non rilevata" or info["hostname"] != "n/d":
            assert block.startswith("[HARDWARE IDENTITY")
            assert re.search(r"- (Hostname|GPU|CPU|RAM):", block)

    def test_module_uses_only_stdlib(self):
        # Garanzia del piano: nessuna dipendenza esterna nel modulo.
        src = Path(HW.__file__).read_text(encoding="utf-8")
        assert "import subprocess" in src
        assert "import socket" in src
        assert "import re" in src
        # Nessun import da core.*, llama_cpp, qdrant, mem0...
        for banned in ("from core.", "import llama_cpp", "from rag.", "from memory.", "import qdrant"):
            assert banned not in src, f"import vietato trovato: {banned}"
