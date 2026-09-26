"""Rules learned from corrections. Match = document type + issuer; effect = folder or skip a tool.
When several rules match, the newest one wins."""
from __future__ import annotations

from .. import db, events
from .actions import Action
from .planner import issuer


def _match_for(doc_type: str, fields: dict) -> dict:
    return {"doc_type": doc_type, "issuer": issuer(fields).lower()}


def matches(match: dict, doc_type: str, fields: dict) -> bool:
    if match.get("doc_type") != doc_type:
        return False
    want = match.get("issuer", "")
    return not want or want in issuer(fields).lower()


def describe(match: dict, effect: dict) -> str:
    who = f"{match['issuer'].title()} " if match.get("issuer") else ""
    what = f"{who}{match['doc_type'].replace('_', ' ')}s"
    if "folder" in effect:
        return f"{what} go to {effect['folder']}"
    if "skip_tool" in effect:
        return f"don't {effect['skip_tool'].replace('_', ' ')} for {what}"
    return f"{what}: {effect}"


def learn(conn, doc_type: str, fields: dict, effect: dict, source: str) -> int:
    match = _match_for(doc_type, fields)
    existing = conn.execute(
        "SELECT id FROM rules WHERE match_json=? AND effect_json=?", (db.dumps(match), db.dumps(effect))
    ).fetchone()
    if existing:
        return existing["id"]
    rid = conn.execute(
        "INSERT INTO rules(match_json, effect_json, source, created_at) VALUES (?,?,?,?)",
        (db.dumps(match), db.dumps(effect), source, db.now()),
    ).lastrowid
    events.emit(conn, "learn", f"Learned rule #{rid}: {describe(match, effect)}", data={"rule_id": rid})
    return rid


def apply_rules(conn, actions: list[Action], doc_type: str, fields: dict) -> tuple[list[Action], list[int]]:
    out = list(actions)
    hits: list[int] = []
    folder_done = False
    skipped: set[str] = set()
    for r in conn.execute("SELECT * FROM rules ORDER BY id DESC").fetchall():
        match, effect = db.loads(r["match_json"], {}), db.loads(r["effect_json"], {})
        if not matches(match, doc_type, fields):
            continue
        if "folder" in effect and not folder_done:
            for a in out:
                if a.tool == "file_document":
                    a.args["folder"] = effect["folder"]
                    a.rule_id = r["id"]
                    folder_done = True
            if folder_done:
                hits.append(r["id"])
        tool = effect.get("skip_tool")
        if tool and tool not in skipped:
            before = len(out)
            out = [a for a in out if a.tool != tool]
            skipped.add(tool)
            if len(out) != before:
                hits.append(r["id"])
    for rid in hits:
        conn.execute("UPDATE rules SET hits = hits + 1 WHERE id=?", (rid,))
    return out, hits
