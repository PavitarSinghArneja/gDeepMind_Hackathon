from snapsort import db
from snapsort.sense.watcher import initial_scan, mark_missing, register_file


def tasks_for(conn, fid):
    return conn.execute("SELECT state FROM tasks WHERE file_id=? ORDER BY id", (fid,)).fetchall()


def test_new_file_gets_a_row_and_a_task(conn, settings):
    p = settings.watch_dirs[0] / "a.txt"
    p.write_text("hello")
    fid = register_file(conn, settings, p)
    assert fid is not None and len(tasks_for(conn, fid)) == 1


def test_same_content_twice_is_ignored(conn, settings):
    p = settings.watch_dirs[0] / "a.txt"
    p.write_text("hello")
    register_file(conn, settings, p)
    assert register_file(conn, settings, p) is None


def test_edited_file_is_requeued(conn, settings):
    p = settings.watch_dirs[0] / "a.txt"
    p.write_text("hello")
    fid = register_file(conn, settings, p)
    p.write_text("hello again")
    assert register_file(conn, settings, p) == fid
    assert len(tasks_for(conn, fid)) == 2


def test_partial_downloads_and_outside_files_are_ignored(conn, settings):
    part = settings.watch_dirs[0] / "x.pdf.crdownload"
    part.write_bytes(b"...")
    outside = settings.library_dir / "y.pdf"
    outside.write_bytes(b"...")
    assert register_file(conn, settings, part) is None
    assert register_file(conn, settings, outside) is None


def test_mark_missing_cancels_queued_task(conn, settings):
    p = settings.watch_dirs[1] / "b.txt"
    p.write_text("bye")
    fid = register_file(conn, settings, p)
    p.unlink()
    mark_missing(conn, p)
    assert conn.execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()["status"] == "missing"
    assert tasks_for(conn, fid)[0]["state"] == "cancelled"


def test_our_own_moves_are_not_treated_as_deletions(conn, settings):
    p = settings.watch_dirs[0] / "c.txt"
    p.write_text("mine")
    fid = register_file(conn, settings, p)
    moved = settings.library_dir / "c.txt"
    conn.execute("UPDATE files SET current_path=?, status='done' WHERE id=?", (str(moved), fid))
    mark_missing(conn, p)  # the watcher reports the old path as gone
    assert conn.execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()["status"] == "done"


def test_initial_scan_counts_new_files(conn, settings):
    for i, d in enumerate(settings.watch_dirs):
        (d / f"f{i}.txt").write_text(f"file {i}")
    assert initial_scan(conn, settings) == 3
    assert initial_scan(conn, settings) == 0


def test_deleting_a_finished_file_is_noted_once_and_not_as_a_warning(conn, settings):
    p = settings.watch_dirs[0] / "done.txt"
    p.write_text("finished")
    fid = register_file(conn, settings, p)
    conn.execute("UPDATE tasks SET state='done'")
    conn.execute("UPDATE files SET status='done' WHERE id=?", (fid,))
    p.unlink()
    mark_missing(conn, p)
    mark_missing(conn, p)  # macOS often reports the same deletion twice
    rows = conn.execute("SELECT level, message FROM events WHERE message LIKE '%done.txt%' AND stage='sense' AND level<>'info' OR message LIKE 'You deleted%'").fetchall()
    assert [r["level"] for r in rows] == ["info"] and "You deleted done.txt" in rows[0]["message"]
    assert conn.execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()["status"] == "missing"


def test_a_file_that_is_still_there_is_not_marked_missing(conn, settings):
    p = settings.watch_dirs[0] / "saved.txt"
    p.write_text("atomic save")
    fid = register_file(conn, settings, p)
    mark_missing(conn, p)  # a stale event while the file exists
    assert conn.execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()["status"] == "queued"
