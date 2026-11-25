# Contributing to Internal Auditor

Internal Auditor is a work in progress and everyone is welcome to help, whether you are an auditor, a GRC or privacy specialist, a developer or a writer.

## Ways to help

- **Try it and report what breaks.** Open an issue with what you did, what you expected and what happened. Do not attach real client documents.
- **Add a framework.** Copy a file in `frameworks/`, keep the same fields (`id`, `domain`, `title`, `summary`, `evidence_hint`, `impact`) and write your own paraphrases. Never paste text from a paid or copyrighted standard. Say in the file's `note` that the wording is a paraphrase. Add the framework and its control count to `EXPECTED` in `tests/test_frameworks.py`.
- **Improve accuracy.** A benchmark that compares model verdicts with hand verdicts from experienced auditors is the most valuable missing piece.
- **Review the audit method.** If you are an auditor, tell us where the tool would not stand up in a real engagement.
- **Docs and translations.**

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q          # no model needed
python tests/fake_ollama.py 11500 &
OLLAMA_URL=http://127.0.0.1:11500 streamlit run app.py
```

The fake Ollama gives deterministic stand-in answers so you can use the whole interface without a GPU. It is not a real model.

## Ground rules

- Keep the two core rules: a verdict must carry a verified quote, and a human approves before it counts.
- Add tests for new behaviour. All tests must pass.
- Use fictional data only in samples and tests.
- Be honest in wording. Do not claim certification, compliance or accuracy the tool has not earned.
- Small, focused pull requests are easier to review.

By contributing you agree that your work is released under the MIT license.
