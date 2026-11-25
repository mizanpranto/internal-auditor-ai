"""Read policy / evidence documents and split them into citeable chunks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import Settings
from .schemas import Chunk

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


# ----------------------------------------------------------------- reading
def read_document(path: Path) -> list[tuple[Optional[int], str]]:
    """Return [(page_number_or_None, text), ...]."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return [(None, _read_docx(path))]
    return [(None, path.read_text(encoding="utf-8", errors="replace"))]


def _read_pdf(path: Path) -> list[tuple[Optional[int], str]]:
    from pypdf import PdfReader

    pages: list[tuple[Optional[int], str]] = []
    for i, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = page.extract_text() or ""
        # PDF line wraps are not paragraph breaks
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        pages.append((i, text))
    return pages


def _read_docx(path: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    parts: list[str] = []
    for child in doc.element.body.iterchildren():  # keep paragraphs and tables in order
        if child.tag.endswith("}p"):
            text = Paragraph(child, doc).text.strip()
            if text:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                cells = []
                for cell in row.cells:
                    t = cell.text.strip()
                    if t and (not cells or cells[-1] != t):  # merged cells repeat
                        cells.append(t)
                if cells:
                    parts.append(" | ".join(cells))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- chunking
def _split_paragraph(par: str, size: int, overlap: int) -> list[str]:
    """Pack the sentences of one long paragraph into <= size-word pieces."""
    out: list[str] = []
    cur: list[str] = []
    n = 0
    fresh = 0  # words added since the last emit (so we never emit overlap-only chunks)
    for sent in _SENTENCE.split(par.strip()):
        w = len(sent.split())
        if w > size:  # one enormous sentence: hard split by words
            if fresh:
                out.append(" ".join(cur))
            cur, n, fresh = [], 0, 0
            words = sent.split()
            step = max(1, size - overlap)
            for i in range(0, len(words), step):
                out.append(" ".join(words[i : i + size]))
            continue
        if n + w > size and fresh:
            out.append(" ".join(cur))
            keep: list[str] = []
            k = 0
            for prev in reversed(cur):
                pw = len(prev.split())
                if k + pw > overlap:
                    break
                keep.insert(0, prev)
                k += pw
            cur, n, fresh = keep, k, 0
        cur.append(sent)
        n += w
        fresh += w
    if fresh:
        out.append(" ".join(cur))
    return out


def chunk_text(text: str, size: int = 200, overlap: int = 40) -> list[str]:
    """Paragraph-aware chunker with a little overlap between neighbours."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    n = 0
    fresh = 0
    for p in paragraphs:
        w = len(p.split())
        if w > size:
            if fresh:
                chunks.append("\n\n".join(cur))
            cur, n, fresh = [], 0, 0
            chunks.extend(_split_paragraph(p, size, overlap))
            continue
        if n + w > size and fresh:
            chunks.append("\n\n".join(cur))
            last = cur[-1]
            lw = len(last.split())
            cur, n, fresh = ([last], lw, 0) if lw <= overlap else ([], 0, 0)
        cur.append(p)
        n += w
        fresh += w
    if fresh:
        chunks.append("\n\n".join(cur))
    return chunks


# ------------------------------------------------------------------ folder
def ingest_folder(folder: str | Path, settings: Settings) -> tuple[list[Chunk], list[dict]]:
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
    chunks: list[Chunk] = []
    docs: list[dict] = []
    cid = 0
    for f in files:
        name = str(f.relative_to(folder))
        try:
            pages = read_document(f)
        except Exception as e:  # noqa: BLE001
            docs.append({"name": name, "chunks": 0, "words": 0, "warning": f"unreadable: {e}"})
            continue
        before = len(chunks)
        words = 0
        for page_no, text in pages:
            words += len(text.split())
            for piece in chunk_text(text, settings.chunk_words, settings.chunk_overlap):
                chunks.append(Chunk(chunk_id=cid, doc=name, page=page_no, text=piece))
                cid += 1
        warning = "" if words else "no extractable text (scanned PDF? run OCR first)"
        docs.append({"name": name, "chunks": len(chunks) - before, "words": words, "warning": warning})
    return chunks, docs
