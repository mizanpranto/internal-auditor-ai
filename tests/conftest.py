import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHUNK_RE = re.compile(r'\[CHUNK (\d+)\][^\n]*\n"""\n(.*?)\n"""', re.DOTALL)


class FakeLLM:
    """Deterministic stand-in for Ollama so the whole pipeline can be tested offline.

    behaviour:
      grounded     - quotes the first words of the first excerpt, says Met
      hallucinate  - claims Met with a quote that is NOT in any document
      none         - always says No evidence
    """

    def __init__(self, behaviour: str = "grounded"):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def embed(self, texts, kind="document"):
        out = []
        for t in texts:
            v = np.zeros(256)
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
            out.append(v.tolist())
        return out

    def chat_json(self, system, user, schema, task=""):
        self.calls.append(task)
        if task == "remediate":
            return {"action_plan": "Do the thing.", "owner_role": "IT Manager",
                    "policy_snippet": "The company shall do the thing.", "effort": "Low"}
        chunks = CHUNK_RE.findall(user)
        if self.behaviour == "none" or not chunks:
            return {"chunk_id": -1, "quote": "", "status": "No evidence", "reasoning": "Nothing relevant.", "missing": "Everything."}
        cid, text = int(chunks[0][0]), chunks[0][1]
        if self.behaviour == "hallucinate":
            return {"chunk_id": cid, "quote": "The organization encrypts every laptop with hardware TPM and rotates keys monthly",
                    "status": "Met", "reasoning": "Looks fine.", "missing": ""}
        quote = " ".join(text.split()[:14])
        return {"chunk_id": cid, "quote": quote, "status": "Met", "reasoning": "Covered.", "missing": ""}


@pytest.fixture
def fake():
    return FakeLLM()
