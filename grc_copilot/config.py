"""Central settings. Every value can be overridden by env var, CLI flag or the UI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # --- Ollama / models -------------------------------------------------
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: os.getenv("COPILOT_LLM", "qwen3:8b"))
    embed_model: str = field(default_factory=lambda: os.getenv("COPILOT_EMBED", "nomic-embed-text"))
    num_ctx: int = 8192          # Ollama's default is small and silently truncates prompts
    temperature: float = 0.1     # low = consistent audit-style judgements
    think: bool = False          # Qwen3 thinking mode; off = faster, JSON stays clean
    request_timeout: int = 600

    # --- ingestion / retrieval ------------------------------------------
    chunk_words: int = 200
    chunk_overlap: int = 40
    top_k: int = 4               # excerpts shown to the model per control
    min_cosine: float = 0.42     # a chunk is a candidate if cosine >= this ...
    min_keyword_terms: int = 2   # ... or it shares this many distinct terms (+1 when embeddings are on)

    # --- citation lock ---------------------------------------------------
    min_quote_words: int = 5     # shorter quotes are not accepted as evidence
    quote_threshold: float = 0.90  # word-level similarity needed to count a quote as verbatim

    # --- paths -----------------------------------------------------------
    framework_path: Path = ROOT / "frameworks" / "iso27001_2022.json"
    output_dir: Path = ROOT / "output"
