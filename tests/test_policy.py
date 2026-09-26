from pathlib import Path

from snapsort.agent.actions import Action
from snapsort.agent.policy import gate, load_policy
from snapsort.config import ROOT

P = load_policy(Path("does-not-exist.yaml"))


def tools(actions):
    return [a.tool for a in actions]


def test_confident_bill_runs_on_its_own():
    d = gate(P, [Action("file_document"), Action("create_reminder")], doc_type="bill", confidence=0.95)
    assert tools(d.auto) == ["file_document", "create_reminder"] and not d.confirm and not d.reasons


def test_vault_always_asks():
    d = gate(P, [Action("vault")], doc_type="credential", confidence=0.99)
    assert tools(d.confirm) == ["vault"] and not d.auto


def test_low_confidence_sends_everything_to_a_person():
    d = gate(P, [Action("file_document"), Action("create_reminder")], doc_type="bill", confidence=0.5)
    assert not d.auto and tools(d.confirm) == ["file_document", "create_reminder"]
    assert "50%" in d.reasons[0]


def test_failed_checks_send_everything_to_a_person():
    d = gate(P, [Action("file_document")], doc_type="bill", confidence=0.95, failed_checks=["amount 9 does not appear"])
    assert not d.auto and d.reasons == ["amount 9 does not appear"]


def test_sensitive_types_always_ask():
    d = gate(P, [Action("file_document")], doc_type="lab_report", confidence=0.99)
    assert not d.auto and "lab reports" in d.reasons[0]


def test_unknown_tools_are_blocked():
    d = gate(P, [Action("file_document"), Action("delete_file")], doc_type="other", confidence=0.99)
    assert tools(d.blocked) == ["delete_file"]
    assert tools(d.confirm) == ["file_document"]  # a blocked request makes the whole file suspicious
    assert any("delete_file" in r for r in d.reasons)


def test_repo_policy_file_loads():
    p = load_policy(ROOT / "config" / "policy.yaml")
    assert "file_document" in p.auto and "vault" in p.confirm and p.confidence_threshold == 0.8
    assert any("delete" in n for n in p.never)
