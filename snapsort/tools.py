"""The only things the agent can do to the world. Every action is written to the journal *before*
it runs (status 'pending') with exact paths, so a crash can be reconciled and anything undone.

There is deliberately no delete, share, upload or network tool.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from cryptography.fernet import Fernet

from . import db, events
from .agent.actions import Action
from .agent.verifier import Check
from .config import Settings


class ToolError(Exception):
    pass


def vault_key(settings: Settings) -> bytes:
    p = settings.vault_key_path
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(Fernet.generate_key())
        os.chmod(p, 0o600)
    return p.read_bytes()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    for i in range(2, 1000):
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not candidate.exists():
            return candidate
    raise ToolError("too many files with the same name")


def _set_path(conn, file_id: int, path: Path) -> None:
    conn.execute("UPDATE files SET current_path=?, updated_at=? WHERE id=?", (str(path), db.now(), file_id))


def plan_args(conn, settings: Settings, file_id: int, action: Action) -> dict:
    """Turn an action into exact arguments before anything is touched."""
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    src = Path(f["current_path"])
    if action.tool == "file_document":
        library = settings.library_dir.resolve()
        folder = (library / action.args["folder"]).resolve()
        if not _inside(folder, library):
            raise ToolError(f"refusing to write outside the library: {action.args['folder']}")
        target = folder / Path(action.args["name"]).name
        if target == src.resolve():
            raise ToolError("the file is already there")
        return {"src": str(src), "dest": str(_unique(target))}
    if action.tool == "vault":
        name = Path(action.args.get("name") or src.name).name
        return {"src": str(src), "dest": str(_unique(settings.vault_dir.resolve() / f"{name}.enc"))}
    if action.tool == "create_reminder":
        return {"title": action.args["title"], "due_date": action.args["due_date"], "amount": action.args.get("amount", "")}
    if action.tool == "mark_duplicate":
        return {"of_file_id": int(action.args["of_file_id"])}
    raise ToolError(f"unknown tool {action.tool}")


def describe(tool: str, args: dict, settings: Settings) -> str:
    if tool == "file_document":
        dest = Path(args["dest"])
        try:
            where = str(dest.relative_to(settings.library_dir.resolve()))
        except ValueError:
            where = dest.name
        return f"Filed as {where}"
    if tool == "vault":
        return "Encrypted it into the vault"
    if tool == "create_reminder":
        return f"Reminder “{args['title']}” for {args['due_date']}"
    if tool == "mark_duplicate":
        return f"Marked as a duplicate of #{args['of_file_id']}"
    return tool


def _run(conn, settings: Settings, file_id: int, tool: str, args: dict) -> dict:
    if tool == "file_document":
        src, dest = Path(args["src"]), Path(args["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        _set_path(conn, file_id, dest)  # before the move, so the watcher ignores the old path
        try:
            shutil.move(str(src), str(dest))
        except Exception:
            _set_path(conn, file_id, src)
            raise
        return {}
    if tool == "vault":
        src, dest = Path(args["src"]), Path(args["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Fernet(vault_key(settings)).encrypt(src.read_bytes()))
        os.chmod(dest, 0o600)
        _set_path(conn, file_id, dest)
        conn.execute("UPDATE files SET vaulted=1 WHERE id=?", (file_id,))
        src.unlink()
        return {}
    if tool == "create_reminder":
        rid = conn.execute(
            "INSERT INTO reminders(file_id, title, due_date, amount, created_at) VALUES (?,?,?,?,?)",
            (file_id, args["title"], args["due_date"], args.get("amount", ""), db.now()),
        ).lastrowid
        return {"reminder_id": rid}
    if tool == "mark_duplicate":
        conn.execute("UPDATE files SET duplicate_of=? WHERE id=?", (args["of_file_id"], file_id))
        return {}
    raise ToolError(f"unknown tool {tool}")


def execute(conn, settings: Settings, *, task_id: int | None, file_id: int, action: Action) -> int:
    args = plan_args(conn, settings, file_id, action)
    jid = conn.execute(
        "INSERT INTO journal(task_id, file_id, tool, args_json, status, created_at) VALUES (?,?,?,?, 'pending', ?)",
        (task_id, file_id, action.tool, db.dumps(args), db.now()),
    ).lastrowid
    try:
        result = _run(conn, settings, file_id, action.tool, args)
    except Exception as e:
        conn.execute("UPDATE journal SET status='failed', result_json=? WHERE id=?", (db.dumps({"error": str(e)}), jid))
        raise ToolError(f"{action.tool} failed: {e}") from e
    conn.execute("UPDATE journal SET status='applied', result_json=? WHERE id=?", (db.dumps(result), jid))
    events.emit(conn, "act", describe(action.tool, args, settings), file_id=file_id, data={"journal_id": jid})
    return jid


def applied_for_task(conn, task_id: int) -> dict[str, int]:
    return {r["tool"]: r["id"] for r in conn.execute(
        "SELECT id, tool FROM journal WHERE task_id=? AND status='applied' ORDER BY id", (task_id,))}


def undo(conn, settings: Settings, journal_id: int) -> None:
    j = conn.execute("SELECT * FROM journal WHERE id=?", (journal_id,)).fetchone()
    if j is None:
        raise ToolError(f"no journal entry {journal_id}")
    if j["status"] != "applied":
        raise ToolError(f"nothing to undo: this action is {j['status']}")
    args, result, fid, tool = db.loads(j["args_json"], {}), db.loads(j["result_json"], {}), j["file_id"], j["tool"]
    if tool in ("file_document", "vault"):
        src, dest = Path(args["src"]), Path(args["dest"])
        if src.exists():
            raise ToolError(f"can't undo: something else is already at {src.name}")
        if not dest.exists():
            raise ToolError(f"can't undo: {dest.name} is no longer where I put it")
        src.parent.mkdir(parents=True, exist_ok=True)
        _set_path(conn, fid, src)
        if tool == "file_document":
            shutil.move(str(dest), str(src))
        else:
            src.write_bytes(Fernet(vault_key(settings)).decrypt(dest.read_bytes()))
            conn.execute("UPDATE files SET vaulted=0 WHERE id=?", (fid,))
            dest.unlink()
    elif tool == "create_reminder":
        conn.execute("DELETE FROM reminders WHERE id=?", (result.get("reminder_id"),))
    elif tool == "mark_duplicate":
        conn.execute("UPDATE files SET duplicate_of=NULL WHERE id=?", (fid,))
    conn.execute("UPDATE journal SET status='undone' WHERE id=?", (journal_id,))
    events.emit(conn, "undo", f"Undid: {describe(tool, args, settings)}", file_id=fid)


def undo_task(conn, settings: Settings, task_id: int) -> int:
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM journal WHERE task_id=? AND status='applied' ORDER BY id DESC", (task_id,))]
    for jid in ids:
        undo(conn, settings, jid)
    return len(ids)


def postconditions(conn, journal_ids: list[int]) -> list[Check]:
    out: list[Check] = []
    for jid in journal_ids:
        j = conn.execute("SELECT * FROM journal WHERE id=?", (jid,)).fetchone()
        args, result = db.loads(j["args_json"], {}), db.loads(j["result_json"], {})
        if j["tool"] in ("file_document", "vault"):
            dest = Path(args["dest"])
            ok = dest.exists() and not Path(args["src"]).exists()
            out.append(Check(f"landed:{j['tool']}", ok, f"{dest.name} is in place" if ok else f"{dest.name} is not where it should be"))
        elif j["tool"] == "create_reminder":
            ok = conn.execute("SELECT 1 FROM reminders WHERE id=?", (result.get("reminder_id"),)).fetchone() is not None
            out.append(Check("landed:create_reminder", ok, "reminder saved" if ok else "reminder is missing"))
        elif j["tool"] == "mark_duplicate":
            row = conn.execute("SELECT duplicate_of FROM files WHERE id=?", (j["file_id"],)).fetchone()
            ok = row["duplicate_of"] == args["of_file_id"]
            out.append(Check("landed:mark_duplicate", ok, "duplicate link saved" if ok else "duplicate link is missing"))
    return out


def reconcile(conn, settings: Settings) -> int:
    """After a crash, settle every 'pending' action by looking at what is actually on disk."""
    rows = conn.execute("SELECT * FROM journal WHERE status='pending'").fetchall()
    for j in rows:
        args, tool, status, result = db.loads(j["args_json"], {}), j["tool"], "failed", {}
        if tool in ("file_document", "vault"):
            src, dest = Path(args["src"]), Path(args["dest"])
            if dest.exists() and not src.exists():
                status = "applied"
                _set_path(conn, j["file_id"], dest)
                if tool == "vault":
                    conn.execute("UPDATE files SET vaulted=1 WHERE id=?", (j["file_id"],))
            else:
                if tool == "vault" and dest.exists():
                    dest.unlink()  # half-written ciphertext; the original is still there
                _set_path(conn, j["file_id"], src)
        elif tool == "create_reminder":
            r = conn.execute("SELECT id FROM reminders WHERE file_id=? AND title=? AND due_date=?",
                             (j["file_id"], args["title"], args["due_date"])).fetchone()
            if r:
                status, result = "applied", {"reminder_id": r["id"]}
        elif tool == "mark_duplicate":
            row = conn.execute("SELECT duplicate_of FROM files WHERE id=?", (j["file_id"],)).fetchone()
            if row and row["duplicate_of"] == args["of_file_id"]:
                status = "applied"
        conn.execute("UPDATE journal SET status=?, result_json=? WHERE id=?", (status, db.dumps(result), j["id"]))
        events.emit(conn, "recover", f"Found an interrupted “{tool.replace('_', ' ')}” and marked it {status}", file_id=j["file_id"])
    return len(rows)
