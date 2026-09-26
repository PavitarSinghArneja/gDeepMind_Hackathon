from datetime import date

from snapsort import db, search, views
from tests.fakes import FakeLLM


def add(conn, llm, settings, title, doc_type, text, fields=None, sensitivity="medium"):
    t = db.now()
    path = settings.library_dir / f"{title}.pdf"
    fid = conn.execute(
        """INSERT INTO files(original_path, current_path, sha256, doc_type, sensitivity, title, summary, fields_json,
                             text, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?, 'done', ?, ?)""",
        (str(path), str(path), title, doc_type, sensitivity, title, f"summary of {title}", db.dumps(fields or {}), text, t, t),
    ).lastrowid
    search.index_file(conn, llm, fid)
    return fid


def test_keyword_search_with_synonyms(conn, settings):
    llm = FakeLLM(embed_error=True)
    wifi = add(conn, llm, settings, "Router admin page", "credential", "Network name (SSID): HomeNet_5G Password: Mango")
    add(conn, llm, settings, "Airtel bill", "bill", "Amount Payable Rs 1179")
    assert [r["id"] for r in search.search(conn, llm, "what's my wifi password?")][0] == wifi


def test_ask_with_no_hits_does_not_call_the_model(conn, settings):
    llm = FakeLLM()
    out = search.ask(conn, llm, settings, "show my passport")
    assert out["found"] is False and llm.calls == []


def test_ask_drops_citations_it_was_not_given(conn, settings):
    llm = FakeLLM({"ask": {"found": True, "answer": "It's due soon", "citations": [999]}}, embed_error=True)
    add(conn, llm, settings, "Airtel bill", "bill", "Airtel Amount Payable Rs 1179")
    out = search.ask(conn, llm, settings, "when is the airtel bill due")
    assert out["grounded"] is False and out["files"][0]["title"] == "Airtel bill"


def test_ask_marks_secret_answers_sensitive(conn, settings):
    llm = FakeLLM(embed_error=True)
    wifi = add(conn, llm, settings, "Router admin page", "credential", "SSID HomeNet_5G Password Mango",
               {"secret": "Mango@Tree#2026"}, sensitivity="high")
    llm.routes["ask"] = {"found": True, "answer": "Mango@Tree#2026", "citations": [wifi]}
    out = search.ask(conn, llm, settings, "wifi password")
    assert out["grounded"] and out["sensitive"] and out["files"][0]["id"] == wifi


def test_public_file_masks_until_reveal(conn, settings):
    llm = FakeLLM(embed_error=True)
    fid = add(conn, llm, settings, "Router admin page", "credential", "x", {"secret": "Mango", "service": "HomeNet"}, "high")
    row = conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    assert views.public_file(row, settings.root)["fields"] == {"secret": "••••••••", "service": "HomeNet"}
    assert views.public_file(row, settings.root, reveal=True)["fields"]["secret"] == "Mango"


def test_reminders_and_stats(conn, settings):
    llm = FakeLLM(embed_error=True)
    fid = add(conn, llm, settings, "Airtel bill", "bill", "x")
    conn.execute("INSERT INTO reminders(file_id, title, due_date, created_at) VALUES (?,?,?,?)", (fid, "Pay", "2026-10-05", db.now()))
    assert views.reminders(conn, today=date(2026, 9, 26))[0]["days_left"] == 9
    s = views.stats(conn)
    assert s["files"] == 1 and s["reminders"] == 1 and s["by_type"] == {"bill": 1}
