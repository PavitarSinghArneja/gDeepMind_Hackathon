import shutil
from pathlib import Path

import pytest

from snapsort import db, tools
from snapsort.agent.actions import Action


def make(conn, settings, name="bill.pdf", content=b"data"):
    p = settings.watch_dirs[0] / name
    p.write_bytes(content)
    t = db.now()
    fid = conn.execute(
        "INSERT INTO files(original_path, current_path, sha256, created_at, updated_at) VALUES (?,?,?,?,?)",
        (str(p), str(p), "x", t, t),
    ).lastrowid
    return fid, p


def current(conn, fid):
    return conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()


def journal(conn, jid):
    return conn.execute("SELECT * FROM journal WHERE id=?", (jid,)).fetchone()


FILE = Action("file_document", {"folder": "Finance/Bills", "name": "2026_bill.pdf"})


def test_file_document_moves_and_journals(conn, settings):
    fid, src = make(conn, settings)
    jid = tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    dest = settings.library_dir / "Finance" / "Bills" / "2026_bill.pdf"
    assert dest.exists() and not src.exists()
    assert current(conn, fid)["current_path"] == str(dest)
    assert journal(conn, jid)["status"] == "applied"
    assert all(c.ok for c in tools.postconditions(conn, [jid]))


def test_name_collision_gets_a_suffix(conn, settings):
    (settings.library_dir / "Finance" / "Bills").mkdir(parents=True)
    (settings.library_dir / "Finance" / "Bills" / "2026_bill.pdf").write_bytes(b"other")
    fid, _ = make(conn, settings)
    tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    assert current(conn, fid)["current_path"].endswith("2026_bill (2).pdf")


def test_path_traversal_is_refused(conn, settings):
    fid, src = make(conn, settings)
    with pytest.raises(tools.ToolError):
        tools.execute(conn, settings, task_id=None, file_id=fid,
                      action=Action("file_document", {"folder": "../../outside", "name": "x.pdf"}))
    assert src.exists()
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0


def test_undo_restores_original_location(conn, settings):
    fid, src = make(conn, settings)
    jid = tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    tools.undo(conn, settings, jid)
    assert src.exists() and current(conn, fid)["current_path"] == str(src)
    assert journal(conn, jid)["status"] == "undone"


def test_undo_refuses_if_original_path_is_taken(conn, settings):
    fid, src = make(conn, settings)
    jid = tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    src.write_bytes(b"someone else")
    with pytest.raises(tools.ToolError):
        tools.undo(conn, settings, jid)


def test_vault_encrypts_and_undo_decrypts(conn, settings):
    fid, src = make(conn, settings, "wifi.png", b"password is Mango")
    jid = tools.execute(conn, settings, task_id=None, file_id=fid, action=Action("vault", {"name": "wifi.png"}))
    row = current(conn, fid)
    dest = Path(row["current_path"])
    assert dest.parent == settings.vault_dir and row["vaulted"] == 1 and not src.exists()
    assert b"Mango" not in dest.read_bytes()
    tools.undo(conn, settings, jid)
    assert src.read_bytes() == b"password is Mango" and current(conn, fid)["vaulted"] == 0


def test_reminder_and_duplicate_then_undo_task(conn, settings):
    first, _ = make(conn, settings, "a.pdf")
    fid, _ = make(conn, settings, "b.pdf")
    tools.execute(conn, settings, task_id=7, file_id=fid,
                  action=Action("create_reminder", {"title": "Pay Airtel", "due_date": "2026-10-05", "amount": "1179.00"}))
    tools.execute(conn, settings, task_id=7, file_id=fid, action=Action("mark_duplicate", {"of_file_id": first}))
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
    assert current(conn, fid)["duplicate_of"] == first
    assert set(tools.applied_for_task(conn, 7)) == {"create_reminder", "mark_duplicate"}
    assert tools.undo_task(conn, settings, 7) == 2
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
    assert current(conn, fid)["duplicate_of"] is None


def _pending(conn, fid, src, dest):
    return conn.execute(
        "INSERT INTO journal(task_id, file_id, tool, args_json, status, created_at) VALUES (NULL,?,?,?,'pending',?)",
        (fid, "file_document", db.dumps({"src": str(src), "dest": str(dest)}), db.now()),
    ).lastrowid


def test_reconcile_move_that_finished_before_the_crash(conn, settings):
    fid, src = make(conn, settings)
    dest = settings.library_dir / "Other" / "bill.pdf"
    dest.parent.mkdir(parents=True)
    shutil.move(src, dest)
    jid = _pending(conn, fid, src, dest)
    assert tools.reconcile(conn, settings) == 1
    assert journal(conn, jid)["status"] == "applied" and current(conn, fid)["current_path"] == str(dest)


def test_reconcile_move_that_never_happened(conn, settings):
    fid, src = make(conn, settings)
    jid = _pending(conn, fid, src, settings.library_dir / "Other" / "bill.pdf")
    conn.execute("UPDATE files SET current_path=? WHERE id=?", (str(settings.library_dir / "Other" / "bill.pdf"), fid))
    assert tools.reconcile(conn, settings) == 1
    assert journal(conn, jid)["status"] == "failed" and current(conn, fid)["current_path"] == str(src)


def test_postconditions_catch_a_file_that_moved_back(conn, settings):
    fid, src = make(conn, settings)
    jid = tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    shutil.move(current(conn, fid)["current_path"], src)
    assert not tools.postconditions(conn, [jid])[0].ok
