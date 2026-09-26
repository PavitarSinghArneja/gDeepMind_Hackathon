from snapsort.agent import rules
from snapsort.agent.actions import Action


def filing():
    return [Action("file_document", {"folder": "Finance/Bills", "name": "x.pdf"}), Action("create_reminder", {"title": "t"})]


def test_folder_rule_applies_to_matching_issuer(conn):
    rid = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "Finance/Telecom"}, "test")
    acts, hits = rules.apply_rules(conn, filing(), "bill", {"vendor": "Airtel Xstream"})
    assert acts[0].args["folder"] == "Finance/Telecom" and acts[0].rule_id == rid and hits == [rid]
    assert conn.execute("SELECT hits FROM rules WHERE id=?", (rid,)).fetchone()["hits"] == 1


def test_rule_ignores_other_issuers_and_types(conn):
    rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "Finance/Telecom"}, "test")
    assert rules.apply_rules(conn, filing(), "bill", {"vendor": "Jio"})[1] == []
    assert rules.apply_rules(conn, filing(), "payment_receipt", {"payee": "Airtel"})[1] == []


def test_skip_rule_removes_tool(conn):
    rid = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"skip_tool": "create_reminder"}, "test")
    acts, hits = rules.apply_rules(conn, filing(), "bill", {"vendor": "Airtel"})
    assert [a.tool for a in acts] == ["file_document"] and hits == [rid]


def test_newest_rule_wins(conn):
    rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "Old"}, "test")
    newest = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "New"}, "test")
    acts, hits = rules.apply_rules(conn, filing(), "bill", {"vendor": "Airtel"})
    assert acts[0].args["folder"] == "New" and hits == [newest]


def test_learning_same_rule_twice_is_idempotent(conn):
    a = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "X"}, "test")
    b = rules.learn(conn, "bill", {"vendor": "Airtel"}, {"folder": "X"}, "test")
    assert a == b


def test_describe():
    assert rules.describe({"doc_type": "bill", "issuer": "airtel"}, {"folder": "Finance/Telecom"}) == "Airtel bills go to Finance/Telecom"
    assert rules.describe({"doc_type": "bill", "issuer": ""}, {"skip_tool": "create_reminder"}) == "don't create reminder for bills"
