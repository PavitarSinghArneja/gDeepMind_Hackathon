from snapsort import db, events, taskq


def _file(conn, name="a.pdf"):
    t = db.now()
    return conn.execute(
        "INSERT INTO files(original_path, current_path, sha256, created_at, updated_at) VALUES (?,?,?,?,?)",
        (name, name, "x", t, t),
    ).lastrowid


def test_claim_returns_oldest_queued_and_leases_it(conn):
    first = taskq.enqueue(conn, _file(conn, "a"))
    taskq.enqueue(conn, _file(conn, "b"))
    task = taskq.claim(conn, lease_seconds=60)
    assert task["id"] == first and task["state"] == "running"
    assert taskq.claim(conn, 60)["id"] != first


def test_expired_lease_can_be_reclaimed(conn):
    tid = taskq.enqueue(conn, _file(conn))
    taskq.claim(conn, 60, now=1000.0)
    assert taskq.claim(conn, 60, now=1030.0) is None
    assert taskq.claim(conn, 60, now=1061.0)["id"] == tid


def test_checkpoint_merges_and_advances_stage(conn):
    tid = taskq.enqueue(conn, _file(conn))
    taskq.checkpoint(conn, tid, "extract", triage={"doc_type": "bill"})
    cp = taskq.checkpoint(conn, tid, "check", extraction={"fields": {}})
    row = conn.execute("SELECT stage FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row["stage"] == "check"
    assert set(cp) == {"triage", "extraction"}


def test_fail_retries_with_backoff_then_gives_up(conn):
    tid = taskq.enqueue(conn, _file(conn))
    assert taskq.fail(conn, tid, "boom", max_attempts=3) == "queued"
    assert taskq.fail(conn, tid, "boom", max_attempts=3) == "queued"
    assert taskq.fail(conn, tid, "boom", max_attempts=3) == "failed"


def test_defer_does_not_spend_an_attempt(conn):
    tid = taskq.enqueue(conn, _file(conn))
    taskq.claim(conn, 60)
    taskq.defer(conn, tid, 10, "model offline")
    row = conn.execute("SELECT state, attempts, not_before FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row["state"] == "queued" and row["attempts"] == 0 and row["not_before"] > db.now()


def test_recover_requeues_running_tasks(conn):
    tid = taskq.enqueue(conn, _file(conn))
    taskq.claim(conn, 60)
    assert taskq.recover(conn) == 1
    assert taskq.claim(conn, 60)["id"] == tid


def test_counts(conn):
    taskq.enqueue(conn, _file(conn, "a"))
    taskq.enqueue(conn, _file(conn, "b"))
    taskq.claim(conn, 60)
    assert taskq.counts(conn) == {"queued": 1, "running": 1}


def test_events_since(conn):
    first = events.emit(conn, "sense", "hello")
    events.emit(conn, "triage", "world", data={"x": 1})
    rows = events.since(conn, first)
    assert [r["message"] for r in rows] == ["world"]
    assert rows[0]["data"] == {"x": 1}
