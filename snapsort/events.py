"""The activity feed. Every stage of every file writes a row here; the UI streams them."""
from __future__ import annotations

from . import db


def emit(conn, stage: str, message: str, *, file_id: int | None = None, level: str = "info", data: dict | None = None) -> int:
    return conn.execute(
        "INSERT INTO events(ts, level, stage, file_id, message, data_json) VALUES (?,?,?,?,?,?)",
        (db.now(), level, stage, file_id, message, db.dumps(data or {})),
    ).lastrowid


def since(conn, after_id: int = 0, limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
    return [
        {"id": r["id"], "ts": r["ts"], "level": r["level"], "stage": r["stage"], "file_id": r["file_id"],
         "message": r["message"], "data": db.loads(r["data_json"], {})}
        for r in rows
    ]
