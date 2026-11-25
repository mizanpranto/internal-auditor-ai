"""A tiny stand-in for Ollama, for testing the UI without a GPU or model.

    python tests/fake_ollama.py 11500
    OLLAMA_URL=http://127.0.0.1:11500 streamlit run app.py

It answers /api/tags, /api/embed and /api/chat with deterministic fake verdicts
(some verified, some made up) so every UI path can be exercised. NOT a real model.
"""

import hashlib
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK_RE = re.compile(r'\[CHUNK (\d+)\][^\n]*\n"""\n(.*?)\n"""', re.DOTALL)
WORD = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in on for with by is are be as at from that this it its all each any".split())


def words(t):
    return {w for w in WORD.findall(t.lower()) if w not in STOP and len(w) > 2}


def embed(t):
    v = [0.0] * 256
    for w in WORD.findall(t.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
    return v


def assess(user: str) -> dict:
    head = user.split("DOCUMENT EXCERPTS")[0]
    cid = re.search(r"CONTROL ([\d.]+)", head).group(1)
    chunks = [(int(i), t) for i, t in CHUNK_RE.findall(user)]
    cw = words(head)
    best = max(chunks, key=lambda c: len(cw & words(c[1])), default=None)
    if not best:
        return {"chunk_id": -1, "quote": "", "status": "No evidence", "reasoning": "Nothing relevant.", "missing": "Evidence."}
    overlap = len(cw & words(best[1]))
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"[#*]", "", best[1]).strip())
    sentences = [s for s in sentences if len(s.split()) >= 6] or sentences
    quote = max(sentences, key=lambda s: len(cw & words(s)))
    h = int(hashlib.md5(cid.encode()).hexdigest(), 16) % 9
    if h == 0:  # a hallucinated verdict, to show the citation lock downgrading it
        return {"chunk_id": best[0], "quote": "All endpoints run managed antivirus with signature updates every four hours.",
                "status": "Met", "reasoning": "The policy covers this.", "missing": ""}
    if overlap < 3:
        return {"chunk_id": -1, "quote": "", "status": "No evidence", "reasoning": "No excerpt addresses this control.", "missing": "A documented procedure and operating evidence."}
    status = "Met" if overlap >= 6 and h % 2 else "Partially met"
    return {"chunk_id": best[0], "quote": quote.strip(), "status": status,
            "reasoning": "The excerpt describes this requirement." if status == "Met" else "The excerpt covers part of the requirement.",
            "missing": "" if status == "Met" else "Evidence of operation, an owner and a review cycle."}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text:latest"}, {"name": "deepseek-r1:14b"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            return self._send({"embeddings": [embed(t) for t in req["input"]]})
        user = req["messages"][-1]["content"]
        if "DOCUMENT EXCERPTS" in user:
            out = assess(user)
        else:
            out = {"action_plan": "Define an owner, write the procedure, collect operating evidence and review it every quarter.",
                   "owner_role": "IT / Security Operations Lead", "policy_snippet": "[Company] shall document and review this control at least annually.",
                   "effort": "Medium"}
        self._send({"message": {"content": json.dumps(out)}})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11500
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
