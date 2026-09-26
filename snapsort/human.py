"""Everything a person decides: the "Needs you" inbox, approvals, rejections and corrections.
Corrections become rules so the agent doesn't ask the same thing twice."""
from __future__ import annotations

from pathlib import Path

from . import db, events, taskq, tools
from .agent import rules
from .agent.actions import Action
from .config import Settings


def open_item(conn, *, task_id: int | None, file_id: int, reason: str, actions: list[Action]) -> int:
    return conn.execute(
        "INSERT INTO inbox(task_id, file_id, reason, actions_json, created_at) VALUES (?,?,?,?,?)",
        (task_id, file_id, reason, db.dumps([a.to_dict() for a in actions]), db.now()),
    ).lastrowid


def list_open(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT i.*, f.title, f.doc_type, f.sensitivity, f.current_path
           FROM inbox i JOIN files f ON f.id = i.file_id
           WHERE i.state = 'open' ORDER BY i.id"""
    ).fetchall()
    return [
        {"id": r["id"], "task_id": r["task_id"], "file_id": r["file_id"], "reason": r["reason"],
         "actions": db.loads(r["actions_json"], []), "title": r["title"], "name": Path(r["current_path"]).name,
         "doc_type": r["doc_type"], "sensitivity": r["sensitivity"], "created_at": r["created_at"]}
        for r in rows
    ]


def _open(conn, item_id: int):
    row = conn.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(f"no inbox item {item_id}")
    if row["state"] != "open":
        raise ValueError(f"inbox item {item_id} is already {row['state']}")
    return row


def _facts(conn, file_id: int) -> tuple[str, dict, str]:
    f = conn.execute("SELECT doc_type, fields_json, title FROM files WHERE id=?", (file_id,)).fetchone()
    return f["doc_type"] or "other", db.loads(f["fields_json"], {}), f["title"] or "this file"


def _close(conn, item, state: str, resolution: dict) -> None:
    conn.execute("UPDATE inbox SET state=?, resolution_json=?, resolved_at=? WHERE id=?",
                 (state, db.dumps(resolution), db.now(), item["id"]))
    if item["task_id"]:
        taskq.finish(conn, item["task_id"], "done")
    conn.execute("UPDATE files SET status='done', updated_at=? WHERE id=?", (db.now(), item["file_id"]))


def approve(conn, settings: Settings, item_id: int, edits: dict | None = None) -> list[int]:
    item = _open(conn, item_id)
    edits = {k: v for k, v in (edits or {}).items() if v}
    doc_type, fields, title = _facts(conn, item["file_id"])
    if edits.get("due_date"):
        fields["due_date"] = edits["due_date"]
        conn.execute("UPDATE files SET fields_json=? WHERE id=?", (db.dumps(fields), item["file_id"]))
    applied: list[int] = []
    for action in (Action.from_dict(d) for d in db.loads(item["actions_json"], [])):
        if action.tool == "flag_for_review":
            continue  # seeing the flag is the action
        if action.tool == "file_document" and edits.get("folder") and edits["folder"] != action.args.get("folder"):
            action.args["folder"] = edits["folder"]
            rules.learn(conn, doc_type, fields, {"folder": edits["folder"]}, "your correction in the inbox")
        if action.tool == "create_reminder" and edits.get("due_date"):
            action.args["due_date"] = edits["due_date"]
        applied.append(tools.execute(conn, settings, task_id=item["task_id"], file_id=item["file_id"], action=action))
    _close(conn, item, "edited" if edits else "approved", {"edits": edits, "journal_ids": applied})
    changed = f" with your changes ({', '.join(edits)})" if edits else ""
    events.emit(conn, "human", f"You approved {title}{changed}", file_id=item["file_id"])
    return applied


def reject(conn, item_id: int, learn: bool = True) -> None:
    item = _open(conn, item_id)
    doc_type, fields, title = _facts(conn, item["file_id"])
    learned = []
    if learn:
        for d in db.loads(item["actions_json"], []):
            if d["tool"] not in ("flag_for_review", "mark_duplicate"):
                learned.append(rules.learn(conn, doc_type, fields, {"skip_tool": d["tool"]}, "you said leave it"))
    _close(conn, item, "rejected", {"learned_rules": learned})
    events.emit(conn, "human", f"You chose to leave {title} as it is", file_id=item["file_id"])


def refile(conn, settings: Settings, file_id: int, folder: str) -> int:
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if f is None:
        raise KeyError(f"no file {file_id}")
    if f["vaulted"]:
        raise tools.ToolError("This file is in the vault. Undo the vault step before moving it.")
    doc_type, fields, _ = _facts(conn, file_id)
    jid = tools.execute(conn, settings, task_id=None, file_id=file_id,
                        action=Action("file_document", {"folder": folder, "name": Path(f["current_path"]).name}, "you moved it"))
    rules.learn(conn, doc_type, fields, {"folder": folder}, "you moved a file")
    return jid


def vault_now(conn, settings: Settings, file_id: int) -> int:
    """The person chose to encrypt this file. Anything still waiting on it in the inbox is settled."""
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if f is None:
        raise KeyError(f"no file {file_id}")
    if f["vaulted"]:
        raise tools.ToolError("This file is already in the vault.")
    jid = tools.execute(conn, settings, task_id=None, file_id=file_id,
                        action=Action("vault", {"name": Path(f["current_path"]).name}, "you chose to lock it"))
    for item in conn.execute("SELECT * FROM inbox WHERE file_id=? AND state='open'", (file_id,)).fetchall():
        _close(conn, item, "approved", {"vaulted_by_you": True, "journal_ids": [jid]})
    events.emit(conn, "human", f"You locked {f['title'] or 'this file'} in the encrypted vault", file_id=file_id)
    return jid
