"""Thin Ollama client: structured-JSON chat + embeddings, using plain HTTP.

The rest of the code only depends on the small `LLM` protocol, so tests can
swap in a fake model and you can swap in another backend later.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

import requests

from .config import Settings

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaError(RuntimeError):
    pass


class LLM(Protocol):
    def chat_json(self, system: str, user: str, schema: dict, task: str = "") -> dict: ...

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]: ...


class OllamaClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.base = settings.ollama_url.rstrip("/")
        self.http = requests.Session()

    # ------------------------------------------------------------------ http
    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self.http.post(self.base + path, json=payload, timeout=self.s.request_timeout)
        except requests.ConnectionError as e:
            raise OllamaError(f"Cannot reach Ollama at {self.base}. Start it with `ollama serve`.") from e
        except requests.Timeout as e:
            raise OllamaError(f"Ollama timed out on {path}") from e
        if r.status_code >= 400:
            raise OllamaError(f"{path} returned HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    # ------------------------------------------------------------------ chat
    def chat_json(self, system: str, user: str, schema: dict, task: str = "") -> dict:
        """Ask for an answer that conforms to `schema` (Ollama structured outputs)."""
        payload = {
            "model": self.s.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "think": self.s.think,
            "options": {"temperature": self.s.temperature, "num_ctx": self.s.num_ctx},
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                data = self._post("/api/chat", payload)
                content = _THINK_RE.sub("", data["message"]["content"]).strip()
                return json.loads(content)
            except (json.JSONDecodeError, KeyError) as e:
                last_err = e
                # nudge sampling a little so a retry is not identical
                payload["options"]["temperature"] = min(0.5, self.s.temperature + 0.15 * (attempt + 1))
            except OllamaError as e:
                # older Ollama builds reject the `think` field: retry without it
                if "think" in payload and "think" in str(e).lower():
                    payload.pop("think")
                    payload["messages"][-1]["content"] += "\n/no_think"
                    last_err = e
                    continue
                raise
        raise OllamaError(f"Model did not return valid JSON for task '{task}': {last_err}")

    # ------------------------------------------------------------- embeddings
    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        prefix = ""
        if "nomic" in self.s.embed_model:  # nomic models expect task prefixes
            prefix = "search_query: " if kind == "query" else "search_document: "
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            batch = [prefix + t for t in texts[i : i + 32]]
            data = self._post("/api/embed", {"model": self.s.embed_model, "input": batch})
            out.extend(data["embeddings"])
        return out

    # ------------------------------------------------------------ diagnostics
    def healthcheck(self) -> dict:
        try:
            r = self.http.get(self.base + "/api/tags", timeout=10)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "models": [], "llm": False, "embed": False}
        names = [m.get("name", "") for m in r.json().get("models", [])]

        def has(model: str) -> bool:
            return model in names or f"{model}:latest" in names

        return {
            "ok": True,
            "error": "",
            "models": names,
            "llm": has(self.s.llm_model),
            "embed": has(self.s.embed_model),
        }
