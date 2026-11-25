"""Step-wise engine used by the UI: index evidence, assess controls, draft fixes.

The CLI keeps using pipeline.run(); this module exposes the same stages one at a
time so the UI can show progress and let a human review each result.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

from .assessor import assess_control
from .config import Settings
from .ingest import ingest_folder
from .llm import OllamaClient
from .pipeline import control_query
from .remediation import draft_remediation, template_remediation
from .schemas import Assessment, Control, RiskItem
from .store import ChunkStore
from .workspace import Workspace

Progress = Callable[[int, int, str], None]


class Engine:
    def __init__(self, ws: Workspace, settings: Settings, controls: list[Control], llm=None):
        self.ws = ws
        self.s = settings
        self.controls = controls
        self.llm = llm or OllamaClient(settings)
        self.store: Optional[ChunkStore] = None
        self.docs: list[dict] = []
        self.index_info: dict = {}

    # ---------------------------------------------------------------- index
    def fingerprint(self) -> str:
        parts = [self.s.embed_model, self.s.chunk_words, self.s.chunk_overlap]
        for e in self.ws.list_evidence():
            parts += [e["name"], e["size"], e["modified"]]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]

    def _meta_path(self):
        return self.ws.index_dir / "meta.json"

    def load_index(self) -> bool:
        """Load a saved index if it still matches the evidence folder."""
        try:
            meta = json.loads(self._meta_path().read_text(encoding="utf-8"))
            if meta.get("fingerprint") != self.fingerprint():
                return False
            self.store = ChunkStore.load(self.ws.index_dir, self.llm, self.s)
            self.docs, self.index_info = meta["docs"], meta
            return True
        except (OSError, KeyError, json.JSONDecodeError):
            return False

    def is_indexed(self) -> bool:
        return self.store is not None and self.index_info.get("fingerprint") == self.fingerprint()

    def build_index(self, use_embeddings: bool = True, progress: Optional[Progress] = None) -> dict:
        emit = progress or (lambda *_a: None)
        emit(0, 3, "Reading documents")
        chunks, docs = ingest_folder(self.ws.evidence_dir, self.s)
        if not chunks:
            raise ValueError("No readable text in the evidence library. Upload a PDF, DOCX, TXT or MD file first.")
        store = ChunkStore(self.llm if use_embeddings else None, self.s)
        store.add(chunks)
        mode, note = "keyword", ""
        if use_embeddings:
            try:
                emit(1, 3, f"Embedding {len(chunks)} passages with {self.s.embed_model}")
                store.build_vectors()
                mode = "hybrid"
            except Exception as e:  # noqa: BLE001
                store.vectors = None
                note = f"Embeddings unavailable ({str(e)[:120]}). Using keyword search."
        emit(2, 3, "Saving index")
        store.save(self.ws.index_dir)
        self.store, self.docs = store, docs
        self.index_info = {"fingerprint": self.fingerprint(), "mode": mode, "chunks": len(chunks), "docs": docs, "note": note}
        self._meta_path().write_text(json.dumps(self.index_info), encoding="utf-8")
        self.ws.log(f"Indexed {len(docs)} document(s), {len(chunks)} passages ({mode} search)")
        emit(3, 3, "Index ready")
        return self.index_info

    # -------------------------------------------------------------- assessing
    def preflight(self) -> None:
        """Fail early with a clear message instead of saving a page of failed assessments."""
        check = getattr(self.llm, "healthcheck", None)
        if check is None:
            return
        h = check()
        if not h["ok"]:
            raise RuntimeError(f"Cannot reach Ollama at {self.s.ollama_url}. Open the Ollama app or run: ollama serve")
        if not h["llm"]:
            raise RuntimeError(f"Model {self.s.llm_model} is not installed. Run: ollama pull {self.s.llm_model}")

    def assess(self, control_ids: list[str], progress: Optional[Progress] = None) -> list[Assessment]:
        if not self.is_indexed():
            raise RuntimeError("Build the index first.")
        emit = progress or (lambda *_a: None)
        by_id = {c.id: c for c in self.controls}
        # a reviewer's "Not applicable" decision is never overwritten by a model run
        keep = {cid for cid, a in self.ws.assessments().items() if a.status == "Not applicable"}
        todo = [by_id[i] for i in control_ids if i in by_id and i not in keep]
        if self.store.vectors is not None:
            try:
                self.store.prefetch_queries([control_query(c) for c in todo])
            except Exception:  # noqa: BLE001
                self.store.vectors = None  # fall back to keywords rather than failing the run
        results: list[Assessment] = []
        for i, c in enumerate(todo, start=1):
            emit(i - 1, len(todo), f"{c.id} {c.title}")
            hits = self.store.search(control_query(c), k=self.s.top_k)
            a = assess_control(c, hits, self.llm, self.s)
            self.ws.put(a)  # saved immediately: an interrupted run loses nothing
            results.append(a)
        emit(len(todo), len(todo), "Done")
        if results:
            self.ws.log(f"Assessed {len(results)} control(s) with {self.s.llm_model}")
        return results

    # ------------------------------------------------------------ remediation
    def remediate(self, risk: RiskItem, use_llm: bool = True) -> RiskItem:
        control = next(c for c in self.controls if c.id == risk.control_id)
        if use_llm:
            rem, ai = draft_remediation(self.llm, control, risk)
        else:
            rem, ai = template_remediation(control, risk), False
        self.ws.update_risk(
            risk.control_id, action_plan=rem.action_plan, owner_role=rem.owner_role,
            effort=rem.effort, policy_snippet=rem.policy_snippet, ai_drafted=ai,
        )
        self.ws.log(f"Drafted remediation for {risk.control_id} ({'AI' if ai else 'template'})")
        return risk
