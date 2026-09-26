"""Find files by keyword (SQLite FTS5) and meaning (EmbeddingGemma), then answer only from what was found."""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import db, views
from .agent import prompts
from .agent.schemas import ASK_SCHEMA
from .llm import LLMError, LLMUnavailable

STOP = {"the", "a", "an", "my", "me", "show", "find", "what", "whats", "is", "was", "of", "for", "to", "in", "on",
        "that", "this", "with", "did", "do", "where", "which", "from", "and", "when", "how", "much", "last", "get"}
SYNONYMS = {
    "wifi": ["wireless", "ssid", "router"], "password": ["passcode", "pin"],
    "electricity": ["power", "tgspdcl", "units"], "salary": ["payslip", "pay"],
    "blood": ["lab", "hba1c", "glucose"], "sugar": ["hba1c", "glucose"], "insurance": ["policy", "health"],
    "train": ["irctc", "pnr", "ticket"], "rent": ["rental", "agreement"], "internet": ["fiber", "broadband", "airtel"],
}
MIN_SIMILARITY = 0.35


def _flatten(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten(v)]
    if isinstance(value, list):
        return [s for v in value for s in _flatten(v)]
    return [str(value)] if value not in (None, "") else []


def index_file(conn, llm, file_id: int) -> bool:
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    name = Path(f["current_path"]).name
    body = " ".join([name, (f["text"] or "")[:8000], *_flatten(db.loads(f["fields_json"], {}))])
    conn.execute("DELETE FROM files_fts WHERE file_id=?", (file_id,))
    conn.execute("INSERT INTO files_fts(file_id, title, summary, body, doc_type) VALUES (?,?,?,?,?)",
                 (file_id, f["title"] or "", f["summary"] or "", body, (f["doc_type"] or "").replace("_", " ")))
    try:
        vector = llm.embed(f"{f['title'] or name}\n{f['summary'] or ''}\n{body[:2000]}")
    except LLMError:
        return False  # keyword search still works
    conn.execute("INSERT OR REPLACE INTO embeddings(file_id, vector_json) VALUES (?,?)", (file_id, db.dumps(vector)))
    return True


def _terms(query: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1 and w not in STOP]
    out: list[str] = []
    for w in words:
        out += [w, *SYNONYMS.get(w, [])]
    return list(dict.fromkeys(out))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(conn, llm, query: str, k: int = 5) -> list:
    scores: dict[int, float] = {}
    terms = _terms(query)
    if terms:
        fts = " OR ".join(f'"{t}"' for t in terms)
        rows = conn.execute("SELECT file_id FROM files_fts WHERE files_fts MATCH ? ORDER BY bm25(files_fts) LIMIT 20", (fts,))
        for rank, r in enumerate(rows):
            scores[r["file_id"]] = scores.get(r["file_id"], 0.0) + 1.0 / (rank + 1)
    try:
        qv = llm.embed(query)
        sims = sorted(((_cosine(qv, db.loads(r["vector_json"], [])), r["file_id"])
                       for r in conn.execute("SELECT * FROM embeddings")), reverse=True)[:20]
        for rank, (sim, fid) in enumerate(sims):
            if sim >= MIN_SIMILARITY:
                scores[fid] = scores.get(fid, 0.0) + 1.0 / (rank + 1)
    except LLMError:
        pass
    ids = sorted(scores, key=scores.get, reverse=True)[:k]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM files WHERE id IN ({placeholders}) AND status NOT IN ('missing', 'cancelled')", ids)}
    return [rows[i] for i in ids if i in rows]


def _for_prompt(row) -> str:
    return (f"[file {row['id']}] {row['title'] or ''} ({row['doc_type'] or 'unknown'}), named {Path(row['current_path']).name}\n"
            f"summary: {row['summary'] or ''}\nfields: {row['fields_json']}\ntext: {(row['text'] or '')[:1200]}")


def ask(conn, llm, settings, question: str) -> dict:
    rows = search(conn, llm, question)
    if not rows:
        return {"found": False, "answer": "I couldn't find anything about that in your files.", "files": [],
                "grounded": True, "sensitive": False}
    closest = [views.public_file(r, settings.root) for r in rows[:3]]
    try:
        out = llm.generate_json(settings.work_model, prompts.ask_prompt(question, "\n\n".join(_for_prompt(r) for r in rows)), ASK_SCHEMA)
    except LLMUnavailable:
        raise
    except LLMError:
        return {"found": True, "answer": "Here are the closest matches.", "files": closest, "grounded": True, "sensitive": False}
    by_id = {r["id"]: r for r in rows}
    cited = [c for c in out.get("citations", []) if isinstance(c, int) and c in by_id]
    if not out.get("found"):
        return {"found": False, "answer": out.get("answer") or "I couldn't find that in your files.", "files": [],
                "grounded": True, "sensitive": False}
    if not cited:
        return {"found": True, "grounded": False, "sensitive": False, "files": closest,
                "answer": "I found something related but couldn't tie an answer to a specific file. Here are the closest matches."}
    files = [by_id[c] for c in cited]
    sensitive = any(r["sensitivity"] == "high" or r["doc_type"] == "credential" for r in files)
    return {"found": True, "answer": str(out.get("answer", "")), "grounded": True, "sensitive": sensitive,
            "files": [views.public_file(r, settings.root) for r in files]}
