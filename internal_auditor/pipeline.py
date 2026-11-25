"""End-to-end run: documents -> retrieval -> assessment -> risks -> reports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .assessor import assess_control
from .config import Settings
from .frameworks import load_framework
from .ingest import ingest_folder
from .llm import OllamaClient
from .remediation import draft_remediation, template_remediation
from .report import write_markdown, write_xlsx
from .risk import build_risk_register
from .schemas import Assessment, Control, RiskItem
from .scoring import summarize
from .store import ChunkStore

Progress = Callable[[str, int, int, str], None]


@dataclass
class RunResult:
    meta: dict
    controls: list[Control]
    assessments: list[Assessment]
    risks: list[RiskItem]
    summary: dict
    docs: list[dict]
    paths: dict = field(default_factory=dict)


def control_query(c: Control) -> str:
    return f"{c.title}. {c.summary} {c.evidence_hint}"


def run(
    docs_dir: str | Path,
    settings: Optional[Settings] = None,
    llm=None,
    *,
    out_dir: str | Path | None = None,
    control_ids: Optional[list[str]] = None,
    limit: Optional[int] = None,
    remediate: bool = True,
    use_embeddings: bool = True,
    resume: bool = False,
    progress: Optional[Progress] = None,
) -> RunResult:
    settings = settings or Settings()
    llm = llm or OllamaClient(settings)
    out = Path(out_dir or settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    emit: Progress = progress or (lambda *_a: None)

    # 1. framework ---------------------------------------------------------
    fw_meta, controls = load_framework(settings.framework_path)
    if control_ids:
        wanted = set(control_ids)
        unknown = wanted - {c.id for c in controls}
        if unknown:
            raise ValueError(f"Unknown control id(s): {sorted(unknown)}")
        controls = [c for c in controls if c.id in wanted]
    if limit:
        controls = controls[:limit]

    # 2. documents -> chunks -----------------------------------------------
    emit("ingest", 0, 1, "Reading documents")
    chunks, docs = ingest_folder(docs_dir, settings)
    if not chunks:
        raise ValueError(f"No readable text found in {docs_dir} (supported: pdf, docx, txt, md)")
    emit("ingest", 1, 1, f"{len(docs)} document(s), {len(chunks)} chunks")

    # 3. index -------------------------------------------------------------
    store = ChunkStore(llm if use_embeddings else None, settings)
    store.add(chunks)
    retrieval = "keyword-only"
    if use_embeddings:
        try:
            emit("index", 0, 1, "Embedding documents")
            store.build_vectors()
            store.prefetch_queries([control_query(c) for c in controls])
            retrieval = "hybrid (embeddings + keywords)"
        except Exception as e:  # noqa: BLE001
            store.vectors = None
            emit("index", 1, 1, f"Embeddings unavailable, using keyword search ({e})")
    store.save(out / "index")
    emit("index", 1, 1, f"Retrieval: {retrieval}")

    # 4. assess every control (with resume) ----------------------------------
    jsonl = out / "assessments.jsonl"
    done: dict[str, Assessment] = {}
    if resume and jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                a = Assessment.model_validate_json(line)
                done[a.control_id] = a
    else:
        jsonl.write_text("", encoding="utf-8")

    assessments: list[Assessment] = []
    total = len(controls)
    with jsonl.open("a", encoding="utf-8") as fh:
        for i, c in enumerate(controls, start=1):
            if c.id in done:
                assessments.append(done[c.id])
                emit("assess", i, total, f"{c.id} {c.title} (resumed)")
                continue
            hits = store.search(control_query(c), k=settings.top_k)
            a = assess_control(c, hits, llm, settings)
            assessments.append(a)
            fh.write(a.model_dump_json() + "\n")
            fh.flush()
            emit("assess", i, total, f"{c.id} {c.title} -> {a.status}")

    # 5. risks + remediation -----------------------------------------------
    risks = build_risk_register(assessments, controls)
    by_id = {c.id: c for c in controls}
    for i, r in enumerate(risks, start=1):
        c = by_id[r.control_id]
        if remediate:
            rem, ai = draft_remediation(llm, c, r)
        else:
            rem, ai = template_remediation(c, r), False
        r.action_plan, r.owner_role, r.effort, r.policy_snippet, r.ai_drafted = (
            rem.action_plan, rem.owner_role, rem.effort, rem.policy_snippet, ai,
        )
        emit("remediate", i, len(risks), f"{r.control_id} {r.title}")

    # 6. reports -------------------------------------------------------------
    summary = summarize(assessments)
    meta = {
        **fw_meta,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": settings.llm_model,
        "retrieval": retrieval,
    }
    emit("report", 0, 1, "Writing reports")
    paths = {
        "xlsx": str(write_xlsx(out / "compliance_report.xlsx", meta, assessments, risks, summary, docs)),
        "md": str(write_markdown(out / "compliance_report.md", meta, assessments, risks, summary, docs)),
        "json": str(out / "results.json"),
    }
    (out / "results.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "summary": summary,
                "assessments": [a.model_dump() for a in assessments],
                "risks": [r.model_dump() for r in risks],
                "documents": docs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    emit("report", 1, 1, "Done")
    return RunResult(meta, controls, assessments, risks, summary, docs, paths)
