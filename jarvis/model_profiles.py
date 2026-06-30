"""
Model profile auto-detection.
Rileva la famiglia del modello caricato dal nome del GGUF o dai metadati GGUF e adatta:
- thinking mode (Gemma/DeepSeek/QwQ)
- contesto massimo
- chat_format per llama-cpp-python
- temperatura e parametri consigliati
- compatibilità RAG e embedding
"""

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional


# ── GGUF binary metadata reader ──
# Legge i metadati dal HEADER del GGUF prima di caricare il modello completo,
# così possiamo determinare chat_format, famiglia, ecc. in fase di config.

_GGUF_MAGIC = b'GGUF'
_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_INT32 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
_GGUF_TYPE_UINT64 = 10
_GGUF_TYPE_INT64 = 11
_GGUF_TYPE_FLOAT64 = 12


def read_gguf_metadata(filepath: str) -> dict:
    """
    Legge i metadati KV da un file GGUF leggendo solo l'header (senza caricare il modello).

    Formato GGUF v3+:
      magic(4) | version(u32) | tensor_count(u64) | metadata_kv_count(u64)
      → per ogni KV: key(string) | value_type(u32) | value(...)

    Returns:
        dict con chiavi tipo ``general.architecture``, ``general.name``, ecc.
        Vuoto se il file non è un GGUF valido o non è leggibile.
    """
    result: dict = {}
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            if magic != _GGUF_MAGIC:
                return result

            version = struct.unpack('<I', f.read(4))[0]
            tensor_count = struct.unpack('<Q', f.read(8))[0]
            kv_count = struct.unpack('<Q', f.read(8))[0]

            for _ in range(kv_count):
                # Key: length-prefixed string
                key_len = struct.unpack('<Q', f.read(8))[0]
                key = f.read(key_len).decode('utf-8', errors='replace')

                # Value type
                val_type = struct.unpack('<I', f.read(4))[0]

                if val_type == _GGUF_TYPE_STRING:
                    val_len = struct.unpack('<Q', f.read(8))[0]
                    value = f.read(val_len).decode('utf-8', errors='replace')
                    result[key] = value

                elif val_type == _GGUF_TYPE_BOOL:
                    result[key] = bool(struct.unpack('<?', f.read(1))[0])

                elif val_type in (_GGUF_TYPE_UINT8, _GGUF_TYPE_INT8):
                    fmt = '<b' if val_type == _GGUF_TYPE_INT8 else '<B'
                    result[key] = struct.unpack(fmt, f.read(1))[0]

                elif val_type in (_GGUF_TYPE_UINT16, _GGUF_TYPE_INT16):
                    fmt = '<h' if val_type == _GGUF_TYPE_INT16 else '<H'
                    result[key] = struct.unpack(fmt, f.read(2))[0]

                elif val_type in (_GGUF_TYPE_UINT32, _GGUF_TYPE_INT32):
                    fmt = '<i' if val_type == _GGUF_TYPE_INT32 else '<I'
                    result[key] = struct.unpack(fmt, f.read(4))[0]

                elif val_type in (_GGUF_TYPE_UINT64, _GGUF_TYPE_INT64):
                    fmt = '<q' if val_type == _GGUF_TYPE_INT64 else '<Q'
                    result[key] = struct.unpack(fmt, f.read(8))[0]

                elif val_type == _GGUF_TYPE_FLOAT32:
                    result[key] = struct.unpack('<f', f.read(4))[0]

                elif val_type == _GGUF_TYPE_FLOAT64:
                    result[key] = struct.unpack('<d', f.read(8))[0]

                elif val_type == _GGUF_TYPE_ARRAY:
                    arr_type = struct.unpack('<I', f.read(4))[0]
                    arr_len = struct.unpack('<Q', f.read(8))[0]
                    arr = []
                    for _ in range(arr_len):
                        if arr_type == _GGUF_TYPE_STRING:
                            s_len = struct.unpack('<Q', f.read(8))[0]
                            arr.append(f.read(s_len).decode('utf-8', errors='replace'))
                        elif arr_type == _GGUF_TYPE_BOOL:
                            arr.append(bool(struct.unpack('<?', f.read(1))[0]))
                        elif arr_type == _GGUF_TYPE_FLOAT32:
                            arr.append(struct.unpack('<f', f.read(4))[0])
                        elif arr_type == _GGUF_TYPE_FLOAT64:
                            arr.append(struct.unpack('<d', f.read(8))[0])
                        elif arr_type in (_GGUF_TYPE_UINT32, _GGUF_TYPE_INT32):
                            fmt_arr = '<i' if arr_type == _GGUF_TYPE_INT32 else '<I'
                            arr.append(struct.unpack(fmt_arr, f.read(4))[0])
                        elif arr_type in (_GGUF_TYPE_UINT64, _GGUF_TYPE_INT64):
                            fmt_arr = '<q' if arr_type == _GGUF_TYPE_INT64 else '<Q'
                            arr.append(struct.unpack(fmt_arr, f.read(8))[0])
                        else:
                            pass  # skip unsupported array element types
                    result[key] = arr

                # else: skip unknown types
    except Exception:
        pass

    return result


@dataclass
class ModelProfile:
    model_name: str = ""                    # Nome reale dal GGUF (es. "Qwen3.5-4B-UD")
    family: str = "unknown"                 # Famiglia logica (qwen, gemma, llama...)
    variant: str = "unknown"                # Variante architettura (qwen2, gemma2...)
    chat_format: Optional[str] = None       # Formato chat per llama-cpp-python
    thinking_support: bool = False
    default_ctx: int = 16384
    max_ctx: int = 32768
    default_temperature: float = 0.7
    default_top_p: float = 0.9
    default_repeat_penalty: float = 1.1
    unsloth_optimized: bool = False
    description: str = ""
    embedding_compatible: bool = True


def detect_model_family(model_path: Optional[str] = None) -> ModelProfile:
    """Rileva la famiglia del modello dal path del GGUF."""
    if not model_path:
        # Chiamato senza argomento da context esterni; default hardcoded
        model_path = "./models/qwen2.5-coder-3b.gguf"

    filename = os.path.basename(model_path).lower()

    if not filename or not os.path.exists(model_path):
        return ModelProfile(
            family="unknown",
            variant="unknown",
            thinking_support=False,
            default_ctx=16384,
            max_ctx=32768,
            default_temperature=0.7,
            default_top_p=0.9,
            default_repeat_penalty=1.1,
            unsloth_optimized=False,
            description="Modello non rilevato — impostazioni generiche"
        )

    unsloth = bool(
        re.search(r'[_-]ud[_-]', filename) or
        re.search(r'[_-]uq[_-]', filename) or
        "unsloth" in filename
    )

    # ── PRIORITÀ 1: rilevamento da metadati GGUF (header binario) ──
    # Legge general.architecture direttamente dal file GGUF senza caricare
    # il modello. Questo è più accurato del filename perché non dipende
    # da convenzioni di naming.
    _gguf_meta = read_gguf_metadata(model_path)
    _arch = (_gguf_meta.get("general.architecture") or "").lower()
    _name = _gguf_meta.get("general.name", "")

    if _arch:
        # Mappa architecture → famiglia (stessa mappa di detect_from_metadata)
        _arch_map = [
            ("qwen2.5", "qwen",  "qwen2.5", False),
            ("qwen2",   "qwen",  "qwen2",   False),
            ("qwen",    "qwen",  "qwen",    False),
            ("gemma2",  "gemma", "gemma2",  True),
            ("gemma",   "gemma", "gemma",   True),
            ("llama",   "llama", "llama",   False),
            ("mistral", "mistral","mistral", False),
            ("mixtral", "mixtral","mixtral", False),
            ("deepseek2","deepseek","deepseek2", True),
            ("deepseek","deepseek","deepseek", True),
            ("phi3",    "phi",   "phi3",    False),
            ("phi",     "phi",   "phi",     False),
            ("command", "command-r","command-r", False),
            ("cohere",  "command-r","command-r", False),
        ]
        for _arch_key, _family, _variant, _thinking in _arch_map:
            if _arch_key in _arch:
                _d = _family_ctx_defaults(_family)
                return ModelProfile(
                    model_name=_name,
                    family=_family,
                    variant=_variant,
                    chat_format=family_to_chat_format(_family, _variant),
                    thinking_support=_thinking,
                    unsloth_optimized=unsloth,
                    default_ctx=_d.get("default_ctx", 16384),
                    max_ctx=_d.get("max_ctx", 65536),
                    default_temperature=_d.get("temperature", 0.7),
                    default_top_p=_d.get("top_p", 0.9),
                    default_repeat_penalty=_d.get("repeat_penalty", 1.1),
                    description=_d.get("desc", f"{_family.capitalize()} — da metadati GGUF"),
                )

    # ── PRIORITÀ 2: fallback su rilevamento da filename ──
    # Se il file non è GGUF (o l'architecture non è nella mappa),
    # usa le euristiche tradizionali sul nome del file.

    if "qwq" in filename:
        return ModelProfile(
            family="qwq",
            variant="qwq",
            thinking_support=True,
            default_ctx=32768,
            max_ctx=65536,
            default_temperature=0.6,
            default_top_p=0.9,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="QwQ — reasoning avanzato"
        )

    if "deepseek" in filename:
        return ModelProfile(
            family="deepseek",
            variant="deepseek",
            thinking_support=True,
            default_ctx=16384,
            max_ctx=65536,
            default_temperature=0.5,
            default_top_p=0.85,
            default_repeat_penalty=1.05,
            unsloth_optimized=unsloth,
            description="DeepSeek — reasoning + coding"
        )

    if "gemma" in filename:
        return ModelProfile(
            family="gemma",
            variant="gemma",
            thinking_support=True,
            default_ctx=8192,
            max_ctx=32768,
            default_temperature=0.6,
            default_top_p=0.85,
            default_repeat_penalty=1.0,
            unsloth_optimized=unsloth,
            description="Gemma — Google LLM"
        )

    if "qwen" in filename:
        return ModelProfile(
            family="qwen",
            variant="qwen",
            thinking_support=False,
            default_ctx=32768,
            max_ctx=65536,
            default_temperature=0.7,
            default_top_p=0.9,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="Qwen — Alibaba Cloud"
        )

    if "llama" in filename:
        return ModelProfile(
            family="llama",
            variant="llama",
            thinking_support=False,
            default_ctx=8192,
            max_ctx=131072,
            default_temperature=0.6,
            default_top_p=0.9,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="Llama — Meta"
        )

    if "mistral" in filename or "mixtral" in filename:
        return ModelProfile(
            family="mistral",
            variant="mistral",
            thinking_support=False,
            default_ctx=32768,
            max_ctx=65536,
            default_temperature=0.6,
            default_top_p=0.9,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="Mistral / Mixtral"
        )

    if "phi" in filename:
        return ModelProfile(
            family="phi",
            variant="phi",
            thinking_support=False,
            default_ctx=4096,
            max_ctx=16384,
            default_temperature=0.5,
            default_top_p=0.85,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="Phi — Microsoft"
        )

    if "command" in filename or "c4ai" in filename or "cohere" in filename:
        return ModelProfile(
            family="command-r",
            variant="command-r",
            thinking_support=False,
            default_ctx=131072,
            max_ctx=256000,
            default_temperature=0.3,
            default_top_p=0.85,
            default_repeat_penalty=1.1,
            unsloth_optimized=unsloth,
            description="Command R+ — Cohere"
        )

    return ModelProfile(
        family="unknown",
        variant="unknown",
        thinking_support=False,
        default_ctx=16384,
        max_ctx=32768,
        default_temperature=0.7,
        default_top_p=0.9,
        default_repeat_penalty=1.1,
        unsloth_optimized=unsloth,
        description="Famiglia non riconosciuta"
    )


def supports_thinking(model_path: Optional[str] = None) -> bool:
    return detect_model_family(model_path).thinking_support


def family_to_chat_format(family: str, variant: str = "") -> Optional[str]:
    """
    Mappa famiglia/variante modello al chat_format corretto per llama-cpp-python.
    
    Il chat_format determina come llama-cpp-python applica il template di chat
    al modello, inclusa la gestione di function calling e tool calls.
    
    Returns:
        str: Nome del chat_format (es. "chatml", "llama-2", "gemma")
        None: Nessun formato specifico (raw, nessun template applicato)
    """
    fmt_map = {
        "qwen": "chatml",
        "qwq": "chatml",
        "gemma": "gemma",
        "deepseek": "chatml",
        "llama": "llama-2",
        "mistral": "llama-2",
        "mixtral": "llama-2",
        "phi": "phi-3",
        "command-r": "cohere",
    }
    return fmt_map.get(family)


def _family_ctx_defaults(family: str) -> dict:
    """Restituisce parametri di default per una famiglia."""
    defaults = {
        "qwen":      {"default_ctx": 32768, "max_ctx": 65536, "temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1, "desc": "Qwen — Alibaba Cloud"},
        "qwq":       {"default_ctx": 32768, "max_ctx": 65536, "temperature": 0.6, "top_p": 0.9, "repeat_penalty": 1.1, "desc": "QwQ — reasoning avanzato"},
        "gemma":     {"default_ctx": 8192,  "max_ctx": 32768, "temperature": 0.6, "top_p": 0.85, "repeat_penalty": 1.0, "desc": "Gemma — Google LLM"},
        "deepseek":  {"default_ctx": 16384, "max_ctx": 65536, "temperature": 0.5, "top_p": 0.85, "repeat_penalty": 1.05, "desc": "DeepSeek — reasoning + coding"},
        "llama":     {"default_ctx": 8192,  "max_ctx": 131072, "temperature": 0.6, "top_p": 0.9, "repeat_penalty": 1.1, "desc": "Llama — Meta"},
        "mistral":   {"default_ctx": 32768, "max_ctx": 65536, "temperature": 0.6, "top_p": 0.9, "repeat_penalty": 1.1, "desc": "Mistral / Mixtral"},
        "mixtral":   {"default_ctx": 32768, "max_ctx": 65536, "temperature": 0.6, "top_p": 0.9, "repeat_penalty": 1.1, "desc": "Mistral / Mixtral"},
        "phi":       {"default_ctx": 4096,  "max_ctx": 16384, "temperature": 0.5, "top_p": 0.85, "repeat_penalty": 1.1, "desc": "Phi — Microsoft"},
        "command-r": {"default_ctx": 131072,"max_ctx": 256000,"temperature": 0.3, "top_p": 0.85, "repeat_penalty": 1.1, "desc": "Command R+ — Cohere"},
    }
    return defaults.get(family, {})


def detect_from_metadata(metadata: dict, fallback: ModelProfile) -> ModelProfile:
    """
    Rileva profilo modello dai metadati GGUF (dopo caricamento).
    Più preciso del filename perché usa general.architecture e general.name.
    
    Args:
        metadata: Dict con metadati GGUF (da Llama.metadata)
        fallback: ModelProfile di fallback (da detect_model_family)
    
    Returns:
        ModelProfile aggiornato con info dai metadati
    """
    if not metadata:
        return fallback

    arch = (metadata.get("general.architecture") or "").lower()
    name = metadata.get("general.name") or fallback.model_name or ""
    unsloth = fallback.unsloth_optimized

    # Architecture → famiglia, variante, thinking_support
    arch_map = [
        ("qwen2.5", "qwen",  "qwen2.5", False),
        ("qwen2",   "qwen",  "qwen2",   False),
        ("qwen",    "qwen",  "qwen",    False),
        ("gemma2",  "gemma", "gemma2",  True),
        ("gemma",   "gemma", "gemma",   True),
        ("llama",   "llama", "llama",   False),
        ("mistral", "mistral","mistral", False),
        ("mixtral", "mixtral","mixtral", False),
        ("deepseek2","deepseek","deepseek2", True),
        ("deepseek","deepseek","deepseek", True),
        ("phi3",    "phi",   "phi3",    False),
        ("phi",     "phi",   "phi",     False),
        ("command", "command-r","command-r", False),
        ("cohere",  "command-r","command-r", False),
    ]

    family, variant, thinking = fallback.family, fallback.variant, fallback.thinking_support
    for arch_key, f, v, t in arch_map:
        if arch_key in arch:
            family, variant, thinking = f, v, t
            break

    # Defaults per la famiglia rilevata
    d = _family_ctx_defaults(family)

    return ModelProfile(
        model_name=name,
        family=family,
        variant=variant,
        chat_format=family_to_chat_format(family, variant),
        thinking_support=thinking,
        unsloth_optimized=unsloth,
        default_ctx=d.get("default_ctx", fallback.default_ctx),
        max_ctx=d.get("max_ctx", fallback.max_ctx),
        default_temperature=d.get("temperature", fallback.default_temperature),
        default_top_p=d.get("top_p", fallback.default_top_p),
        default_repeat_penalty=d.get("repeat_penalty", fallback.default_repeat_penalty),
        description=d.get("desc", fallback.description),
        embedding_compatible=fallback.embedding_compatible,
    )
