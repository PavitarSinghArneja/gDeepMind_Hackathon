"""Shapes database rows for the UI. Secrets stay masked unless the person explicitly reveals them."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import db
from .agent.rules import describe

MASK = "••••••••"
ALWAYS_MASK = {"secret"}
MASK_WHEN_HIGH = {"id_last4", "account_last4", "policy_number", "username"}


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def public_file(row, root: Path, reveal: bool = False) -> dict:
    fields = db.loads(row["fields_json"], {})
    if not reveal:
        hide = ALWAYS_MASK | (MASK_WHEN_HIGH if row["sensitivity"] == "high" else set())
        fields = {k: (MASK if k in hide and v else v) for k, v in fields.items()}
    return {
        "id": row["id"], "name": Path(row["current_path"]).name, "title": row["title"], "doc_type": row["doc_type"],
        "sensitivity": row["sensitivity"], "confidence": row["confidence"], "summary": row["summary"],
        "fields": fields, "checks": db.loads(row["checks_json"], []), "status": row["status"],
        "duplicate_of": row["duplicate_of"], "vaulted": bool(row["vaulted"]),
        "location": _rel(row["current_path"], root), "updated_at": row["updated_at"],
    }


def list_files(conn, root: Path, doc_type: str | None = None) -> list[dict]:
    sql = "SELECT * FROM files WHERE status NOT IN ('missing', 'cancelled')"
    params: tuple = ()
    if doc_type:
        sql, params = sql + " AND doc_type=?", (doc_type,)
    return [public_file(r, root) for r in conn.execute(sql + " ORDER BY updated_at DESC", params)]


def file_detail(conn, root: Path, file_id: int, reveal: bool = False) -> dict | None:
    row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        return None
    d = public_file(row, root, reveal)
    d["original_location"] = _rel(row["original_path"], root)
    d["original_name"] = Path(row["original_path"]).name
    d["journal"] = [
        {"id": j["id"], "task_id": j["task_id"], "tool": j["tool"], "status": j["status"],
         "args": db.loads(j["args_json"], {}), "created_at": j["created_at"]}
        for j in conn.execute("SELECT * FROM journal WHERE file_id=? ORDER BY id", (file_id,))
    ]
    d["timeline"] = [
        {"ts": e["ts"], "stage": e["stage"], "level": e["level"], "message": e["message"]}
        for e in conn.execute("SELECT * FROM events WHERE file_id=? ORDER BY id", (file_id,))
    ]
    return d


def reminders(conn, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for r in conn.execute("SELECT * FROM reminders WHERE state IN ('active', 'done') ORDER BY state, due_date"):
        try:
            days = (date.fromisoformat(r["due_date"]) - today).days
        except ValueError:
            days = None
        out.append({"id": r["id"], "file_id": r["file_id"], "title": r["title"], "due_date": r["due_date"],
                    "amount": r["amount"], "days_left": days, "paid": r["state"] == "done"})
    return out


def rules_list(conn) -> list[dict]:
    return [
        {"id": r["id"], "text": describe(db.loads(r["match_json"], {}), db.loads(r["effect_json"], {})),
         "hits": r["hits"], "source": r["source"]}
        for r in conn.execute("SELECT * FROM rules ORDER BY id DESC")
    ]


def stats(conn) -> dict:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    secs = [db.loads(r["data_json"], {}).get("seconds") for r in conn.execute("SELECT data_json FROM events WHERE stage='done'")]
    secs = [s for s in secs if isinstance(s, (int, float))]
    return {
        "files": one("SELECT COUNT(*) FROM files WHERE status NOT IN ('missing', 'cancelled')"),
        "by_status": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")},
        "by_type": {r[0] or "pending": r[1] for r in conn.execute("SELECT doc_type, COUNT(*) FROM files GROUP BY doc_type")},
        "avg_seconds": round(sum(secs) / len(secs), 1) if secs else None,
        "inbox_open": one("SELECT COUNT(*) FROM inbox WHERE state='open'"),
        "reminders": one("SELECT COUNT(*) FROM reminders WHERE state='active'"),
        "vaulted": one("SELECT COUNT(*) FROM files WHERE vaulted=1"),
        "duplicates": one("SELECT COUNT(*) FROM files WHERE duplicate_of IS NOT NULL"),
        "rules": one("SELECT COUNT(*) FROM rules"),
        "escalations": one("SELECT COUNT(*) FROM events WHERE stage='triage' AND message LIKE 'Only %'"),
        "fallbacks": one("SELECT COUNT(*) FROM events WHERE stage='model' AND message LIKE '%falling back%'"),
        "checks_failed": one("SELECT COUNT(*) FROM events WHERE stage='verify' AND level='warn'"),
        "blocked": one("SELECT COUNT(*) FROM events WHERE stage='policy'"),
    }


EXPENSE_TYPES = {"bill": ("vendor", "due_date"), "payment_receipt": ("payee", "date")}


def expenses(conn, root: Path, start: str | None = None, end: str | None = None) -> list[dict]:
    """Bills and payments with an amount, dated by due date (bills) or payment date (receipts), oldest first."""
    rows = []
    for r in conn.execute("SELECT * FROM files WHERE doc_type IN ('bill', 'payment_receipt') AND duplicate_of IS NULL "
                          "AND status NOT IN ('missing', 'cancelled')"):
        who_key, date_key = EXPENSE_TYPES[r["doc_type"]]
        f = db.loads(r["fields_json"], {})
        when, amount = str(f.get(date_key) or ""), str(f.get("amount") or "").replace(",", "")
        try:
            date.fromisoformat(when)
            value = float(amount)
        except ValueError:
            continue
        if (start and when < start) or (end and when > end):
            continue
        rows.append({"date": when, "paid_to": f.get(who_key) or r["title"] or "", "amount": round(value, 2),
                     "type": "Bill" if r["doc_type"] == "bill" else "Payment", "file": _rel(r["current_path"], root),
                     "verified": all(c.get("ok") for c in db.loads(r["checks_json"], [])),
                     "file_id": r["id"]})
    return sorted(rows, key=lambda x: x["date"])
