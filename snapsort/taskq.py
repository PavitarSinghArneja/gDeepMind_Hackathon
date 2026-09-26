"""Durable task queue. A task is one file's trip through the pipeline.

States: queued → running → done | needs_human | failed | cancelled.
A running task holds a lease; if the process dies, the lease expires (or `recover` runs at boot)
and the task is picked up again from its last checkpoint.
"""
from __future__ import annotations

from . import db


def enqueue(conn, file_id: int) -> int:
    t = db.now()
    return conn.execute(
        "INSERT INTO tasks(file_id, created_at, updated_at) VALUES (?,?,?)", (file_id, t, t)
    ).lastrowid


def claim(conn, lease_seconds: int, now: float | None = None):
    t = db.now() if now is None else now
    with db.tx(conn):
        row = conn.execute(
            """SELECT id FROM tasks
               WHERE (state = 'queued' AND not_before <= ?) OR (state = 'running' AND lease_until < ?)
               ORDER BY id LIMIT 1""",
            (t, t),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE tasks SET state='running', lease_until=?, updated_at=? WHERE id=?",
            (t + lease_seconds, t, row["id"]),
        )
    return conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()


def get_checkpoint(row) -> dict:
    return db.loads(row["checkpoint_json"], {})


def checkpoint(conn, task_id: int, stage: str, **data) -> dict:
    row = conn.execute("SELECT checkpoint_json FROM tasks WHERE id=?", (task_id,)).fetchone()
    cp = db.loads(row["checkpoint_json"], {})
    cp.update(data)
    conn.execute(
        "UPDATE tasks SET stage=?, checkpoint_json=?, updated_at=? WHERE id=?",
        (stage, db.dumps(cp), db.now(), task_id),
    )
    return cp


def finish(conn, task_id: int, state: str) -> None:
    conn.execute(
        "UPDATE tasks SET state=?, lease_until=NULL, updated_at=? WHERE id=?", (state, db.now(), task_id)
    )


def defer(conn, task_id: int, seconds: float, reason: str) -> None:
    """Put a task back without spending an attempt (e.g. the model isn't running)."""
    conn.execute(
        "UPDATE tasks SET state='queued', lease_until=NULL, not_before=?, last_error=?, updated_at=? WHERE id=?",
        (db.now() + seconds, reason, db.now(), task_id),
    )


def fail(conn, task_id: int, error: str, max_attempts: int) -> str:
    attempts = conn.execute("SELECT attempts FROM tasks WHERE id=?", (task_id,)).fetchone()["attempts"] + 1
    if attempts >= max_attempts:
        state, not_before = "failed", 0.0
    else:
        state, not_before = "queued", db.now() + 2 ** attempts
    conn.execute(
        """UPDATE tasks SET state=?, attempts=?, last_error=?, lease_until=NULL, not_before=?, updated_at=?
           WHERE id=?""",
        (state, attempts, error[:500], not_before, db.now(), task_id),
    )
    return state


def recover(conn) -> int:
    """At boot nothing is running yet, so any 'running' task was interrupted."""
    return conn.execute(
        "UPDATE tasks SET state='queued', lease_until=NULL, not_before=0 WHERE state='running'"
    ).rowcount


def counts(conn) -> dict[str, int]:
    return {r["state"]: r["n"] for r in conn.execute("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")}
