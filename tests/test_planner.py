from datetime import date

import pytest

from snapsort.agent.planner import build_actions, suggest_name
from snapsort.llm import LLMBadOutput, LLMUnavailable
from tests.fakes import FakeLLM

TODAY = date(2026, 9, 26)
TRI = {"doc_type": "bill", "title": "Airtel fiber bill", "contains_secret": False, "confidence": 0.95}
EXT = {"summary": "Airtel bill", "fields": {"vendor": "Airtel", "amount": "1179.00", "due_date": "2026-10-05"}, "confidence": 0.9}


def plan(*tools):
    return {"plan": {"actions": [{"tool": t, "why": f"because {t}"} for t in tools]}}


def run(settings, llm, doc_type="bill", tri=TRI, ext=EXT, suffix=".pdf"):
    return build_actions(llm, settings, doc_type=doc_type, triage=tri, extraction=ext, ext=suffix, today=TODAY)


def test_model_choice_with_code_filled_arguments(settings):
    acts = run(settings, FakeLLM(plan("file_document", "create_reminder")))
    assert [a.tool for a in acts] == ["file_document", "create_reminder"]
    assert acts[0].args == {"folder": "Finance/Bills", "name": "2026-10-05_bill_Airtel.pdf"}
    assert acts[1].args == {"title": "Pay Airtel ₹1179.00", "due_date": "2026-10-05", "amount": "1179.00"}
    assert acts[0].why == "because file_document"


def test_bad_model_output_falls_back_to_default_plan(settings):
    acts = run(settings, FakeLLM({"plan": LLMBadOutput("junk")}))
    assert [a.tool for a in acts] == ["file_document", "create_reminder"]


def test_model_unavailable_propagates(settings):
    with pytest.raises(LLMUnavailable):
        run(settings, FakeLLM({"plan": LLMUnavailable("down")}))


def test_no_reminder_for_past_due_date(settings):
    past = {**EXT, "fields": {**EXT["fields"], "due_date": "2026-09-01"}}
    assert [a.tool for a in run(settings, FakeLLM(plan("file_document", "create_reminder")), ext=past)] == ["file_document"]


def test_visible_secret_forces_vault_instead_of_filing(settings):
    tri = {"doc_type": "credential", "title": "WiFi password", "contains_secret": True, "confidence": 0.95}
    ext = {"summary": "router page", "fields": {"service": "HomeNet_5G", "secret": "Mango@Tree#2026"}, "confidence": 0.9}
    acts = run(settings, FakeLLM(plan("file_document")), doc_type="credential", tri=tri, ext=ext, suffix=".png")
    assert [a.tool for a in acts] == ["vault"]
    assert acts[0].args["name"] == "2026-09-26_credential_HomeNet-5G.png"


def test_abnormal_lab_values_are_flagged(settings):
    tri = {"doc_type": "lab_report", "title": "Apollo lab report", "contains_secret": False, "confidence": 0.95}
    ext = {"summary": "labs", "confidence": 0.9, "fields": {"lab": "Apollo", "date": "2026-09-20", "tests": [
        {"name": "HbA1c", "value": "7.2", "flag": "high"}, {"name": "TSH", "value": "2.1", "flag": "normal"}]}}
    acts = run(settings, FakeLLM(plan("file_document")), doc_type="lab_report", tri=tri, ext=ext)
    flag = [a for a in acts if a.tool == "flag_for_review"][0]
    assert flag.args["reason"] == "Out of range: HbA1c 7.2 (high)"


def test_unknown_tool_from_model_is_kept_for_the_policy_to_block(settings):
    acts = run(settings, FakeLLM(plan("file_document", "delete_file")))
    assert "delete_file" in [a.tool for a in acts]


def test_insurance_reminder_only_close_to_expiry(settings):
    tri = {"doc_type": "insurance_card", "title": "Star Health card", "contains_secret": False, "confidence": 0.9}
    near = {"summary": "", "confidence": 0.9, "fields": {"insurer": "Star Health", "valid_until": "2026-11-05"}}
    far = {"summary": "", "confidence": 0.9, "fields": {"insurer": "Star Health", "valid_until": "2027-06-01"}}
    llm = FakeLLM(plan("file_document", "create_reminder"))
    assert "create_reminder" in [a.tool for a in run(settings, llm, "insurance_card", tri, near)]
    assert "create_reminder" not in [a.tool for a in run(settings, llm, "insurance_card", tri, far)]


def test_suggest_name_strips_path_tricks():
    name = suggest_name("bill", "", {"vendor": "../../etc/passwd"}, ".pdf", TODAY)
    assert "/" not in name and name == "2026-09-26_bill_etc-passwd.pdf"
