"""Exercises the real HTTP client against a tiny fake Ollama server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from grc_copilot.config import Settings
from grc_copilot.llm import OllamaClient, OllamaError

STATE = {"chat_calls": 0, "reject_think": False, "bad_json_first": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text:latest"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            self.server.last_embed = req
            return self._send(200, {"embeddings": [[1.0, 0.0] for _ in req["input"]]})
        STATE["chat_calls"] += 1
        self.server.last_chat = req
        if STATE["reject_think"] and "think" in req:
            return self._send(400, {"error": "model does not support think"})
        if STATE["bad_json_first"] and STATE["chat_calls"] == 1:
            return self._send(200, {"message": {"content": "not json"}})
        self._send(200, {"message": {"content": "<think>hmm</think>" + json.dumps({"ok": True})}})


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    STATE.update(chat_calls=0, reject_think=False, bad_json_first=False)
    yield srv
    srv.shutdown()


def client(srv):
    return OllamaClient(Settings(ollama_url=f"http://127.0.0.1:{srv.server_port}"))


def test_chat_json_sends_schema_and_strips_think(server):
    out = client(server).chat_json("sys", "user", {"type": "object"}, task="t")
    assert out == {"ok": True}
    req = server.last_chat
    assert req["format"] == {"type": "object"} and req["think"] is False and req["options"]["num_ctx"] == 8192


def test_retries_bad_json(server):
    STATE["bad_json_first"] = True
    assert client(server).chat_json("s", "u", {}) == {"ok": True}
    assert STATE["chat_calls"] == 2


def test_falls_back_when_think_unsupported(server):
    STATE["reject_think"] = True
    assert client(server).chat_json("s", "u", {}) == {"ok": True}
    assert "think" not in server.last_chat and server.last_chat["messages"][-1]["content"].endswith("/no_think")


def test_embed_uses_nomic_prefixes_and_batches(server):
    c = client(server)
    vecs = c.embed(["a", "b", "c"], kind="query")
    assert len(vecs) == 3 and server.last_embed["input"][0] == "search_query: a"
    assert len(c.embed([str(i) for i in range(70)])) == 70


def test_healthcheck(server):
    h = client(server).healthcheck()
    assert h["ok"] and h["llm"] and h["embed"]


def test_unreachable_server_gives_friendly_error():
    c = OllamaClient(Settings(ollama_url="http://127.0.0.1:9"))
    with pytest.raises(OllamaError, match="ollama serve"):
        c.chat_json("s", "u", {})
    assert c.healthcheck()["ok"] is False
