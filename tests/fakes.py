"""Test doubles. FakeLLM answers by schema title so tests read like a script."""
from __future__ import annotations

from datetime import date, timedelta

from snapsort.llm import LLMError

DUE = (date.today() + timedelta(days=9)).isoformat()  # matches the Airtel mock bill's due date


class FakeLLM:
    def __init__(self, routes: dict | None = None, embed_error: bool = False):
        self.routes = routes or {}
        self.embed_error = embed_error
        self.calls: list[dict] = []

    def generate_json(self, model, prompt, schema, images=None):
        title = schema.get("title", "")
        self.calls.append({"model": model, "title": title, "prompt": prompt, "images": images or []})
        reply = self.routes.get(title)
        if callable(reply):
            reply = reply(model, prompt, images or [])
        if isinstance(reply, BaseException):
            raise reply
        if reply is None:
            raise LLMError(f"FakeLLM has no route for '{title}'")
        return reply

    def embed(self, text):
        if self.embed_error:
            raise LLMError("no embedding model")
        vec = [0.0] * 26
        for ch in text.lower():
            if "a" <= ch <= "z":
                vec[ord(ch) - 97] += 1.0
        return vec

    def models(self):
        return ["gemma4:e2b", "gemma4:e4b", "embeddinggemma:latest"]

    def titles(self):
        return [c["title"] for c in self.calls]


def routes_for(doc_type="bill", *, confidence=0.95, sensitivity="medium", contains_secret=False,
               title="Airtel fiber bill", fields=None, summary="Airtel broadband bill",
               plan=("file_document", "create_reminder")):
    return {
        "triage": {"doc_type": doc_type, "sensitivity": sensitivity, "contains_secret": contains_secret,
                   "title": title, "confidence": confidence},
        "extract": {"summary": summary,
                    "fields": {"vendor": "Airtel", "amount": "1179.00", "due_date": DUE} if fields is None else fields,
                    "confidence": 0.9},
        "plan": {"actions": [{"tool": t, "why": f"model chose {t}"} for t in plan]},
        "confirm": {"visible": True},
    }
