from pathlib import Path

import pytest

from snapsort import db, human, taskq, tools
from snapsort.agent.actions import Action
from snapsort.sense.watcher import register_file


def bill(conn, settings, name="airtel.pdf"):
    p = settings.watch_dirs[0] / name
    p.write_bytes(b"airtel bill " + name.encode())
    fid = register_file(conn, settings, p)
    conn.execute("UPDATE files SET doc_type='bill', title='Airtel bill', fields_json=? WHERE id=?",
                 (db.dumps({"vendor": "Airtel", "amount": "1179.00", "due_date": "2026-10-05"}), fid))
    tid = conn.execute("SELECT id FROM tasks WHERE file_id=?", (fid,)).fetchone()["id"]
    return fid, tid


FILE = Action("file_document", {"folder": "Finance/Bills", "name": "2026-10-05_bill_Airtel.pdf"}, "organise")
REMIND = Action("create_reminder", {"title": "Pay Airtel", "due_date": "2026-10-03", "amount": "1179.00"}, "due soon")


def test_approve_runs_actions_and_closes_everything(conn, settings):
    fid, tid = bill(conn, settings)
    item = human.open_item(conn, task_id=tid, file_id=fid, reason="checks failed", actions=[FILE, REMIND])
    assert human.list_open(conn)[0]["actions"][0]["tool"] == "file_document"
    human.approve(conn, settings, item)
    assert "Finance/Bills" in conn.execute("SELECT current_path FROM files WHERE id=?", (fid,)).fetchone()[0]
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "done"
    assert human.list_open(conn) == []


def test_edits_change_the_action_and_teach_a_rule(conn, settings):
    fid, tid = bill(conn, settings)
    item = human.open_item(conn, task_id=tid, file_id=fid, reason="ambiguous date", actions=[FILE, REMIND])
    human.approve(conn, settings, item, {"folder": "Finance/Telecom", "due_date": "2026-01-10"})
    assert "Finance/Telecom" in conn.execute("SELECT current_path FROM files WHERE id=?", (fid,)).fetchone()[0]
    assert conn.execute("SELECT due_date FROM reminders").fetchone()[0] == "2026-01-10"
    assert db.loads(conn.execute("SELECT fields_json FROM files WHERE id=?", (fid,)).fetchone()[0])["due_date"] == "2026-01-10"
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 1


def test_reject_learns_skip_rules_only_when_asked(conn, settings):
    fid, tid = bill(conn, settings)
    human.reject(conn, human.open_item(conn, task_id=tid, file_id=fid, reason="r", actions=[REMIND]), learn=False)
    assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0
    fid2, tid2 = bill(conn, settings, "airtel2.pdf")
    human.reject(conn, human.open_item(conn, task_id=tid2, file_id=fid2, reason="r", actions=[REMIND]), learn=True)
    assert "skip_tool" in conn.execute("SELECT effect_json FROM rules").fetchone()[0]


def test_flag_only_item_is_acknowledged(conn, settings):
    fid, tid = bill(conn, settings)
    item = human.open_item(conn, task_id=tid, file_id=fid, reason="look",
                           actions=[Action("flag_for_review", {"reason": "odd"}, "odd")])
    assert human.approve(conn, settings, item) == []


def test_cannot_resolve_twice_or_missing(conn, settings):
    fid, tid = bill(conn, settings)
    item = human.open_item(conn, task_id=tid, file_id=fid, reason="r", actions=[])
    human.approve(conn, settings, item)
    with pytest.raises(ValueError):
        human.approve(conn, settings, item)
    with pytest.raises(KeyError):
        human.reject(conn, 999)


def test_refile_moves_and_learns(conn, settings):
    fid, _ = bill(conn, settings)
    tools.execute(conn, settings, task_id=None, file_id=fid, action=FILE)
    human.refile(conn, settings, fid, "Finance/Telecom")
    path = conn.execute("SELECT current_path FROM files WHERE id=?", (fid,)).fetchone()[0]
    assert Path(path).parent.name == "Telecom"
    assert "Finance/Telecom" in conn.execute("SELECT effect_json FROM rules").fetchone()[0]


def test_vault_now_encrypts_and_settles_the_inbox(conn, settings):
    fid, tid = bill(conn, settings)
    human.open_item(conn, task_id=tid, file_id=fid, reason="lab reports are always checked", actions=[FILE])
    human.vault_now(conn, settings, fid)
    row = conn.execute("SELECT vaulted, status, current_path FROM files WHERE id=?", (fid,)).fetchone()
    assert row["vaulted"] == 1 and row["status"] == "done" and row["current_path"].endswith(".enc")
    assert human.list_open(conn) == []
    with pytest.raises(tools.ToolError):
        human.vault_now(conn, settings, fid)
