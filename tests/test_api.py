import pytest
from fastapi.testclient import TestClient

from snapsort import db, human, tools
from snapsort.agent.actions import Action
from snapsort.agent.policy import load_policy
from snapsort.api.server import create_app
from snapsort.sense.watcher import register_file
from tests.fakes import FakeLLM, routes_for


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings, FakeLLM(routes_for(), embed_error=True), load_policy(settings.policy_path)))


def a_file(conn, settings, name="wifi.png", content=b"password Mango"):
    p = settings.watch_dirs[1] / name
    p.write_bytes(content)
    fid = register_file(conn, settings, p)
    conn.execute("UPDATE files SET doc_type='credential', sensitivity='high', title='WiFi', status='needs_human', "
                 "fields_json=? WHERE id=?", (db.dumps({"secret": "Mango"}), fid))
    return fid


def test_state(client):
    body = client.get("/api/state").json()
    assert body["models"]["available"] is True
    assert "internet" in body["network"] and "Finance/Bills" in body["folders"]
    assert "vault" in body["policy"]["confirm"]


def test_index_page(client):
    assert client.get("/").status_code == 200


def test_files_list_and_detail_mask_secrets(client, conn, settings):
    fid = a_file(conn, settings)
    assert client.get("/api/files").json()[0]["fields"]["secret"] == "••••••••"
    detail = client.get(f"/api/files/{fid}").json()
    assert "journal" in detail and "timeline" in detail
    assert client.post(f"/api/files/{fid}/reveal").json()["fields"]["secret"] == "Mango"
    assert client.get("/api/files/999").status_code == 404


def test_inbox_approve_vaults_the_file_and_preview_needs_reveal(client, conn, settings):
    fid = a_file(conn, settings)
    item = human.open_item(conn, task_id=None, file_id=fid, reason="secret", actions=[Action("vault", {"name": "wifi.png"})])
    assert client.get("/api/inbox").json()[0]["id"] == item
    assert client.post(f"/api/inbox/{item}/approve", json={}).status_code == 200
    assert client.get(f"/api/files/{fid}/preview").status_code == 403
    r = client.get(f"/api/files/{fid}/preview?reveal=true")
    assert r.status_code == 200 and r.content == b"password Mango"
    assert client.post(f"/api/inbox/{item}/approve", json={}).status_code == 409


def test_refile_and_undo(client, conn, settings):
    fid = a_file(conn, settings, "bill.pdf", b"%PDF bill")
    conn.execute("UPDATE files SET doc_type='bill', sensitivity='medium' WHERE id=?", (fid,))
    assert client.post(f"/api/files/{fid}/refile", json={"folder": "Finance/Telecom"}).status_code == 200
    assert client.get("/api/rules").json()[0]["text"].endswith("go to Finance/Telecom")
    jid = conn.execute("SELECT id FROM journal").fetchone()[0]
    assert client.post(f"/api/journal/{jid}/undo").status_code == 200
    assert client.post(f"/api/journal/{jid}/undo").status_code == 409


def test_ask_without_matches(client):
    assert client.post("/api/ask", json={"question": "my passport number"}).json()["found"] is False


def test_recent_events(client, conn, settings):
    a_file(conn, settings)
    assert any("Noticed" in e["message"] for e in client.get("/api/events/recent").json())


def test_expenses_export_filters_by_date(client, conn, settings):
    for i, (dt, fields) in enumerate([("bill", {"vendor": "Airtel", "amount": "1179.00", "due_date": "2026-10-05"}),
                                      ("payment_receipt", {"payee": "Chai Point", "amount": "450", "date": "2026-09-20"}),
                                      ("bill", {"vendor": "Old", "amount": "99", "due_date": "2026-01-01"})]):
        fid = a_file(conn, settings, f"e{i}.png", f"x{i}".encode())
        conn.execute("UPDATE files SET doc_type=?, fields_json=?, status='done' WHERE id=?", (dt, db.dumps(fields), fid))
    body = client.get("/api/expenses?start=2026-09-01&end=2026-10-31").json()
    assert [r["paid_to"] for r in body["rows"]] == ["Chai Point", "Airtel"] and body["total"] == 1629.0
    csv_text = client.get("/api/expenses?start=2026-09-01&end=2026-10-31&format=csv").text
    assert "Airtel,1179.00" in csv_text and "Total,1629.00" in csv_text


def test_mark_paid_logs_and_can_be_undone(client, conn, settings):
    fid = a_file(conn, settings)
    rid = conn.execute("INSERT INTO reminders(file_id, title, due_date, created_at) VALUES (?, 'Pay Airtel', '2099-01-01', 0)", (fid,)).lastrowid
    client.post(f"/api/reminders/{rid}/done")
    assert client.get("/api/reminders").json()[0]["paid"] is True
    assert any("marked “Pay Airtel” as paid" in e["message"] for e in client.get("/api/events/recent").json())
    client.post(f"/api/reminders/{rid}/undo")
    assert client.get("/api/reminders").json()[0]["paid"] is False
