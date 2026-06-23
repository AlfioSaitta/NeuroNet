"""
Model profile auto-detection.
Rileva la famiglia del modello caricato dal nome del GGUF e adatta:
- thinking mode (Gemma/DeepSeek/QwQ)
- contesto massimo
- temperatura e parametri consigliati
- compatibilità RAG e embedding
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelProfile:
    family: str
    variant: str
    thinking_support: bool
    default_ctx: int
    max_ctx: int
    default_temperature: float
    default_top_p: float
    default_repeat_penalty: float
    unsloth_optimized: bool
    description: str = ""
    embedding_compatible: bool = True


def detect_model_family(model_path: Optional[str] = None) -> ModelProfile:
    """Rileva la famiglia del modello dal path del GGUF."""
    if not model_path:
        model_path = os.environ.get("LLAMA_MODEL_PATH", "")

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
