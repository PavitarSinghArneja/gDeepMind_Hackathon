import shutil

import pytest

from snapsort import db, taskq
from snapsort.agent import rules
from snapsort.agent.pipeline import Pipeline
from snapsort.agent.policy import load_policy
from snapsort.agent.verifier import Check
from snapsort.llm import LLMUnavailable
from snapsort.sense.watcher import register_file
from tests.fakes import FakeLLM, routes_for

WIFI = dict(doc_type="credential", sensitivity="high", contains_secret=True, title="WiFi password HomeNet_5G",
            fields={"service": "HomeNet_5G", "username": "", "secret": "Mango@Tree#2026"}, plan=("file_document",))


class Crash(BaseException):
    """Stands in for kill -9: not caught by the pipeline's error handling."""


@pytest.fixture
def add(conn, settings, mock_dir):
    def _add(pattern, folder=0):
        src = next(mock_dir.rglob(pattern))
        dest = settings.watch_dirs[folder] / src.name
        shutil.copyfile(src, dest)
        return register_file(conn, settings, dest)
    return _add


def pipe(conn, settings, llm):
    return Pipeline(conn, settings, llm, load_policy(settings.policy_path))


def file_row(conn, fid):
    return conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()


def stages(conn, fid):
    return [r["stage"] for r in conn.execute("SELECT stage FROM events WHERE file_id=? ORDER BY id", (fid,))]


def inbox(conn, fid):
    return conn.execute("SELECT * FROM inbox WHERE file_id=?", (fid,)).fetchall()


def test_happy_path_bill_is_filed_with_a_reminder(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    llm = FakeLLM(routes_for())
    assert pipe(conn, settings, llm).run_once()
    f = file_row(conn, fid)
    assert f["status"] == "done" and "library/Finance/Bills/" in f["current_path"]
    assert conn.execute("SELECT title FROM reminders").fetchone()[0].startswith("Pay Airtel")
    assert llm.titles() == ["triage", "extract", "plan"]
    for stage in ("sense", "triage", "extract", "verify", "plan", "act", "done"):
        assert stage in stages(conn, fid)


def test_low_confidence_escalates_to_the_bigger_model(conn, settings, add):
    if settings.triage_model == settings.work_model:
        pytest.skip("only one model configured; escalation is off until gemma4:e4b is installed")
    add("Downloads/Airtel_Bill_*.pdf")
    routes = routes_for()
    routes["triage"] = lambda model, prompt, images: {**routes_for()["triage"], "confidence": 0.5 if model == settings.triage_model else 0.92}
    llm = FakeLLM(routes)
    pipe(conn, settings, llm).run_once()
    assert [c["model"] for c in llm.calls if c["title"] == "triage"] == [settings.triage_model, settings.work_model]
    assert conn.execute("SELECT status FROM files").fetchone()[0] == "done"


def test_still_unsure_goes_to_a_person_without_touching_the_file(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    pipe(conn, settings, FakeLLM(routes_for(confidence=0.5))).run_once()
    f = file_row(conn, fid)
    assert f["status"] == "needs_human" and "/mock/Downloads/" in f["current_path"]
    item = inbox(conn, fid)[0]
    assert "50%" in item["reason"] and "file_document" in item["actions_json"]


def test_exact_duplicate_is_marked_without_model_calls(conn, settings, add):
    first = add("Downloads/Airtel_Bill_*.pdf")
    llm = FakeLLM(routes_for())
    p = pipe(conn, settings, llm)
    p.run_once()
    calls_before = len(llm.calls)
    dup = add("document(1).pdf")
    p.run_once()
    assert file_row(conn, dup)["duplicate_of"] == first and file_row(conn, dup)["status"] == "done"
    assert len(llm.calls) == calls_before


def test_near_duplicate_screenshot_asks_first(conn, settings, add):
    llm = FakeLLM(routes_for(**WIFI))
    p = pipe(conn, settings, llm)
    first = add("Screenshot * at 21.14.03.png", folder=1)
    p.run_once()
    near = add("Screenshot * at 21.14.10.png", folder=1)
    p.run_once()
    item = inbox(conn, near)[0]
    assert "near-copy" in item["reason"] and "mark_duplicate" in item["actions_json"]
    assert f'"of_file_id": {first}' in item["actions_json"]


def test_secret_goes_to_the_inbox_as_a_vault_proposal(conn, settings, add):
    fid = add("Screenshot * at 21.14.03.png", folder=1)
    pipe(conn, settings, FakeLLM(routes_for(**WIFI))).run_once()
    item = inbox(conn, fid)[0]
    assert '"tool": "vault"' in item["actions_json"] and "encrypted vault" in item["reason"]
    assert file_row(conn, fid)["vaulted"] == 0


@pytest.mark.parametrize("name,words", [("Statement_Protected.pdf", "password-protected"), ("broken_download.pdf", "corrupt")])
def test_unreadable_pdfs_go_to_a_person_without_model_calls(conn, settings, add, name, words):
    fid = add(name)
    llm = FakeLLM(routes_for())
    pipe(conn, settings, llm).run_once()
    assert words in inbox(conn, fid)[0]["reason"] and llm.calls == []


def test_unsupported_file_is_indexed_by_name_only(conn, settings, add):
    fid = add("setup_installer.dmg")
    llm = FakeLLM(routes_for())
    pipe(conn, settings, llm).run_once()
    f = file_row(conn, fid)
    assert f["status"] == "done" and f["doc_type"] == "other" and llm.calls == []


def test_model_unavailable_defers_without_spending_an_attempt(conn, settings, add):
    add("Downloads/Airtel_Bill_*.pdf")
    routes = routes_for()
    routes["triage"] = LLMUnavailable("connection refused")
    pipe(conn, settings, FakeLLM(routes)).run_once()
    t = conn.execute("SELECT * FROM tasks").fetchone()
    assert t["state"] == "queued" and t["attempts"] == 0 and t["not_before"] > db.now()


def test_crash_resumes_from_checkpoint_without_repeating_model_calls(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    routes = routes_for()
    routes["extract"] = Crash()
    with pytest.raises(Crash):
        pipe(conn, settings, FakeLLM(routes)).run_once()
    assert taskq.recover(conn) == 1
    llm = FakeLLM(routes_for())
    pipe(conn, settings, llm).run_once()
    assert llm.titles() == ["extract", "plan"]
    assert file_row(conn, fid)["status"] == "done"
    assert "recover" in stages(conn, fid)


def test_hallucinated_value_blocks_automatic_action(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    bad = routes_for(fields={"vendor": "Airtel", "amount": "9999.00", "due_date": routes_for()["extract"]["fields"]["due_date"]})
    pipe(conn, settings, FakeLLM(bad)).run_once()
    assert file_row(conn, fid)["status"] == "needs_human"
    assert "9999.00 does not appear" in inbox(conn, fid)[0]["reason"]
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0


def test_prompt_injection_note_is_flagged_and_forbidden_tool_blocked(conn, settings, add):
    fid = add("ReadMe_Assistant.txt")
    routes = routes_for("other", sensitivity="low", title="Note to AI", fields={"description": "a note"},
                        plan=("file_document", "delete_file"))
    pipe(conn, settings, FakeLLM(routes)).run_once()
    reason = inbox(conn, fid)[0]["reason"]
    assert "instructions aimed at an AI" in reason and "delete_file" in reason
    assert "policy" in stages(conn, fid)
    assert "/mock/Downloads/" in file_row(conn, fid)["current_path"]


def test_failed_postcondition_rolls_back(conn, settings, add, monkeypatch):
    from snapsort import tools

    fid = add("Downloads/Airtel_Bill_*.pdf")
    monkeypatch.setattr(tools, "postconditions", lambda conn, ids: [Check("landed:file_document", False, "boom")])
    pipe(conn, settings, FakeLLM(routes_for())).run_once()
    assert "/mock/Downloads/" in file_row(conn, fid)["current_path"]
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
    assert "rolled back 2" in inbox(conn, fid)[0]["reason"]


def test_repeated_errors_end_in_failed_with_a_note(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    routes = routes_for()
    routes["extract"] = RuntimeError("model crashed")
    p = pipe(conn, settings, FakeLLM(routes))
    for _ in range(settings.max_attempts):
        conn.execute("UPDATE tasks SET not_before=0")
        p.run_once()
    assert conn.execute("SELECT state FROM tasks").fetchone()[0] == "failed"
    assert "couldn't process" in inbox(conn, fid)[0]["reason"]


def test_learned_rule_is_applied(conn, settings, add):
    rid = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "Finance/Telecom"}, "test")
    fid = add("Downloads/Airtel_Bill_*.pdf")
    pipe(conn, settings, FakeLLM(routes_for())).run_once()
    assert "library/Finance/Telecom/" in file_row(conn, fid)["current_path"]
    assert any(f"rule #{rid}" in r["message"] for r in conn.execute("SELECT message FROM events WHERE stage='learn'"))


def test_file_deleted_before_processing_is_cancelled(conn, settings, add):
    fid = add("Downloads/Airtel_Bill_*.pdf")
    next(settings.watch_dirs[0].glob("Airtel_Bill_*.pdf")).unlink()
    pipe(conn, settings, FakeLLM(routes_for())).run_once()
    assert conn.execute("SELECT state FROM tasks").fetchone()[0] == "cancelled"
