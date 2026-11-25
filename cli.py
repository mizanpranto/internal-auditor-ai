#!/usr/bin/env python3
"""Command line interface for the Internal Auditor.

  python cli.py check                       # is Ollama running, are the models pulled?
  python cli.py controls                    # list the controls in the framework
  python cli.py assess --docs sample_docs   # run the full assessment
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from internal_auditor.config import Settings
from internal_auditor.frameworks import load_framework
from internal_auditor.llm import OllamaClient
from internal_auditor.pipeline import run
from internal_auditor.scoring import STATUS_ORDER


def cmd_check(s: Settings) -> int:
    h = OllamaClient(s).healthcheck()
    if not h["ok"]:
        print(f"[x] Ollama not reachable at {s.ollama_url}: {h['error']}")
        print("    Start it with:  ollama serve")
        return 1
    print(f"[ok] Ollama reachable at {s.ollama_url}")
    print(f"[{'ok' if h['llm'] else 'x'}] chat model   {s.llm_model}" + ("" if h["llm"] else f"   -> ollama pull {s.llm_model}"))
    print(f"[{'ok' if h['embed'] else 'x'}] embed model  {s.embed_model}" + ("" if h["embed"] else f"   -> ollama pull {s.embed_model}"))
    return 0 if h["llm"] and h["embed"] else 1


def cmd_controls(s: Settings) -> int:
    meta, controls = load_framework(s.framework_path)
    print(meta.get("framework", ""), f"({len(controls)} controls)\n")
    for c in controls:
        print(f"{c.id:<6} {c.title}")
    return 0


def cmd_assess(s: Settings, a: argparse.Namespace) -> int:
    if not Path(a.docs).is_dir():
        print(f"Documents folder not found: {a.docs}")
        return 2

    def progress(stage: str, i: int, total: int, msg: str) -> None:
        if stage in ("assess", "remediate"):
            print(f"  [{stage:<9} {i:>3}/{total}] {msg}", flush=True)
        else:
            print(f"[{stage}] {msg}", flush=True)

    result = run(
        a.docs,
        s,
        out_dir=a.out,
        control_ids=[x.strip() for x in a.controls.split(",")] if a.controls else None,
        limit=a.limit,
        remediate=not a.no_remediation,
        use_embeddings=not a.keyword_only,
        resume=a.resume,
        progress=progress,
    )
    o = result.summary["overall"]
    print(f"\nOverall readiness: {o['score']:.0%}  (" + ", ".join(f"{k}: {v}" for k, v in o["counts"].items() if k in STATUS_ORDER) + ")")
    print(f"Risks logged: {len(result.risks)}   Findings needing review: {result.summary['needs_review']}")
    for k, p in result.paths.items():
        print(f"  {k:<5} {p}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Internal Auditor (ISO 27001 gap assessment on a local LLM)")
    p.add_argument("--model", help="chat model (default qwen3:8b)")
    p.add_argument("--embed-model", help="embedding model (default nomic-embed-text)")
    p.add_argument("--ollama-url", help="default http://localhost:11434")
    p.add_argument("--framework", help="path to a framework JSON file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify Ollama and models")
    sub.add_parser("controls", help="list framework controls")
    a = sub.add_parser("assess", help="assess documents against the framework")
    a.add_argument("--docs", required=True, help="folder with policies / evidence (pdf, docx, txt, md)")
    a.add_argument("--out", default="output", help="output folder (default: output)")
    a.add_argument("--controls", help="only these control ids, e.g. 5.1,8.7,8.13")
    a.add_argument("--limit", type=int, help="only the first N controls (quick test)")
    a.add_argument("--no-remediation", action="store_true", help="skip AI remediation drafts (faster)")
    a.add_argument("--keyword-only", action="store_true", help="skip embeddings, use keyword search only")
    a.add_argument("--resume", action="store_true", help="continue a previous run in --out")
    args = p.parse_args()

    s = Settings()
    if args.model:
        s.llm_model = args.model
    if args.embed_model:
        s.embed_model = args.embed_model
    if args.ollama_url:
        s.ollama_url = args.ollama_url
    if args.framework:
        s.framework_path = Path(args.framework)

    try:
        if args.cmd == "check":
            return cmd_check(s)
        if args.cmd == "controls":
            return cmd_controls(s)
        return cmd_assess(s, args)
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run with --resume to continue.")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
