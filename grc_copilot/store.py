"""Local hybrid retrieval: embeddings (cosine) + BM25 keywords. No external DB."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .config import Settings
from .schemas import Chunk

_STOP = set(
    "the a an and or of to in on for with by is are be as at from that this these those it its "
    "shall should must will may can not no any all each per into than then also such their there "
    "which who whom what when where how has have had been being was were do does done our we us "
    "your you they them if else over under about between".split()
)


def _stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.tf = [Counter(d) for d in docs]
        self.dl = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.dl.mean()) if self.n else 0.0
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n)
        if not self.n or not self.avgdl:
            return out
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
        return out

    def matched(self, query: list[str]) -> np.ndarray:
        """How many distinct query terms each document contains."""
        terms = set(query)
        return np.array([sum(1 for t in terms if t in tf) for tf in self.tf], dtype=float)


@dataclass
class Hit:
    chunk: Chunk
    score: float
    cosine: Optional[float]
    bm25: float


class ChunkStore:
    def __init__(self, llm=None, settings: Optional[Settings] = None):
        self.llm = llm
        self.s = settings or Settings()
        self.chunks: list[Chunk] = []
        self.vectors: Optional[np.ndarray] = None
        self._bm25: Optional[BM25] = None
        self._qcache: dict[str, np.ndarray] = {}

    # ---------------------------------------------------------------- build
    def add(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        self._bm25 = BM25([tokenize(c.text) for c in self.chunks])
        self.vectors = None

    def build_vectors(self) -> None:
        if not self.llm:
            raise RuntimeError("No embedding backend configured")
        vecs = np.array(self.llm.embed([c.text for c in self.chunks], kind="document"), dtype=np.float32)
        self.vectors = vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)

    def prefetch_queries(self, queries: list[str]) -> None:
        """Embed all control queries in one batch (much faster than one by one)."""
        todo = [q for q in dict.fromkeys(queries) if q not in self._qcache]
        if not todo or self.vectors is None:
            return
        vecs = np.array(self.llm.embed(todo, kind="query"), dtype=np.float32)
        vecs = vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)
        for q, v in zip(todo, vecs):
            self._qcache[q] = v

    def _query_vec(self, query: str) -> np.ndarray:
        if query not in self._qcache:
            self.prefetch_queries([query])
        return self._qcache[query]

    # --------------------------------------------------------------- search
    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self.chunks or self._bm25 is None:
            return []
        q_tokens = tokenize(query)
        bm = self._bm25.scores(q_tokens)
        shared = self._bm25.matched(q_tokens)
        bmax = float(bm.max()) if len(bm) else 0.0
        bm_norm = bm / bmax if bmax > 0 else bm

        if self.vectors is not None:
            cos = self.vectors @ self._query_vec(query)
            score = 0.7 * cos + 0.3 * bm_norm
            keep = (cos >= self.s.min_cosine) | (shared >= self.s.min_keyword_terms + 1)
        else:
            cos = None
            score = bm_norm
            keep = shared >= self.s.min_keyword_terms

        order = [int(i) for i in np.argsort(-score) if keep[i]][:k]
        return [
            Hit(self.chunks[i], float(score[i]), float(cos[i]) if cos is not None else None, float(bm[i]))
            for i in order
        ]

    # ------------------------------------------------------------- persist
    @classmethod
    def load(cls, folder: str | Path, llm=None, settings: Optional[Settings] = None) -> "ChunkStore":
        folder = Path(folder)
        store = cls(llm, settings)
        store.add([Chunk(**c) for c in json.loads((folder / "chunks.json").read_text(encoding="utf-8"))])
        vec = folder / "vectors.npy"
        if vec.exists():
            store.vectors = np.load(vec)
        return store

    def save(self, folder: str | Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chunks.json").write_text(json.dumps([c.model_dump() for c in self.chunks]), encoding="utf-8")
        vec = folder / "vectors.npy"
        if self.vectors is not None:
            np.save(vec, self.vectors)
        elif vec.exists():
            vec.unlink()  # never leave stale vectors next to fresh chunks
