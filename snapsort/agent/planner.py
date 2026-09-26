"""Decide what to do with a file. The model chooses tools and says why; code fills exact arguments,
so file names and folders stay consistent and can't escape the library."""
from __future__ import annotations

import re
from datetime import date

from .. import db
from ..llm import LLMBadOutput, LLMError, LLMUnavailable
from . import prompts
from .actions import Action
from .schemas import CATEGORY_FOLDERS, DATE_KEYS, ISSUER_KEYS, PLAN_SCHEMA

REMINDER_FIELD = {"bill": "due_date", "insurance_card": "valid_until"}
RENEWAL_WINDOW_DAYS = 45


def slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    return s[:max_len].strip("-") or "file"


def issuer(fields: dict) -> str:
    for k in ISSUER_KEYS:
        v = fields.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def primary_date(fields: dict) -> str:
    for k in DATE_KEYS:
        v = fields.get(k)
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return v
    return ""


def suggest_name(doc_type: str, title: str, fields: dict, ext: str, today: date) -> str:
    when = primary_date(fields) or today.isoformat()
    return f"{when}_{slug(doc_type)}_{slug(issuer(fields) or title)}{ext.lower()}"


def reminder_date(doc_type: str, fields: dict, today: date) -> str:
    key = REMINDER_FIELD.get(doc_type)
    value = fields.get(key, "") if key else ""
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError):
        return ""
    if d < today:
        return ""
    if doc_type == "insurance_card" and (d - today).days > RENEWAL_WINDOW_DAYS:
        return ""
    return value


def _range_flag(test: dict) -> str | None:
    """Recompute high/low from the reference range when both are numbers; the model's flag is only a fallback."""
    nums = re.findall(r"\d+(?:\.\d+)?", str(test.get("reference_range", "")))
    value = re.findall(r"\d+(?:\.\d+)?", str(test.get("value", "")))
    if len(nums) < 2 or not value:
        return None
    v, lo, hi = float(value[0]), float(nums[0]), float(nums[1])
    return "low" if v < lo else "high" if v > hi else "normal"


def abnormal_tests(fields: dict) -> list[dict]:
    out = []
    for t in fields.get("tests") or []:
        if not isinstance(t, dict):
            continue
        flag = _range_flag(t) or t.get("flag")
        if flag in ("high", "low"):
            out.append({**t, "flag": flag})
    return out


def _default_plan(doc_type: str, triage: dict, fields: dict, today: date) -> list[tuple[str, str]]:
    plan = [("file_document", "keep it organised")]
    if reminder_date(doc_type, fields, today):
        plan.append(("create_reminder", "there's a date coming up"))
    if triage.get("contains_secret"):
        plan.append(("vault", "a secret is visible"))
    return plan


def _materialize(tool: str, why: str, doc_type: str, triage: dict, fields: dict, ext: str, today: date) -> Action | None:
    title = triage.get("title", "")
    if tool == "file_document":
        return Action(tool, {"folder": CATEGORY_FOLDERS.get(doc_type, "Other"),
                             "name": suggest_name(doc_type, title, fields, ext, today)}, why)
    if tool == "create_reminder":
        due = reminder_date(doc_type, fields, today)
        if not due:
            return None
        who = issuer(fields) or title
        amount = str(fields.get("amount") or "")
        text = (f"Pay {who}" + (f" ₹{amount}" if amount else "")) if doc_type == "bill" else f"Renew {who}"
        return Action(tool, {"title": text, "due_date": due, "amount": amount}, why)
    if tool == "vault":
        return Action(tool, {"name": suggest_name(doc_type, title, fields, ext, today)}, why)
    if tool == "flag_for_review":
        return Action(tool, {"reason": why}, why)
    return Action(tool, {}, why)  # unknown tool: kept so the policy gate blocks it and logs it


def build_actions(llm, settings, *, doc_type: str, triage: dict, extraction: dict, ext: str, today: date | None = None) -> list[Action]:
    today = today or date.today()
    fields = extraction.get("fields", {})
    try:
        out = llm.generate_json(
            settings.triage_model,
            prompts.plan_prompt(doc_type, triage.get("title", ""), extraction.get("summary", ""), db.dumps(fields)),
            PLAN_SCHEMA,
        )
        proposed = [(str(a.get("tool", "")), str(a.get("why", ""))) for a in out.get("actions", []) if isinstance(a, dict)]
        if not proposed:
            raise LLMBadOutput("empty plan")
    except LLMUnavailable:
        raise
    except LLMError:
        proposed = _default_plan(doc_type, triage, fields, today)

    actions: list[Action] = []
    seen: set[str] = set()
    for tool, why in proposed:
        if tool in seen:
            continue
        seen.add(tool)
        a = _materialize(tool, why, doc_type, triage, fields, ext, today)
        if a is not None:
            actions.append(a)

    if triage.get("contains_secret") and "vault" not in seen:
        actions.append(_materialize("vault", "a password or code is visible", doc_type, triage, fields, ext, today))
    if any(a.tool == "vault" for a in actions):
        actions = [a for a in actions if a.tool != "file_document"]  # the vault is where it goes
    elif not any(a.tool == "file_document" for a in actions):
        actions.insert(0, _materialize("file_document", "keep it organised", doc_type, triage, fields, ext, today))

    bad = abnormal_tests(fields)
    if bad:
        reason = "Out of range: " + ", ".join(f"{t.get('name')} {t.get('value')} ({t.get('flag')})" for t in bad)
        flag = next((a for a in actions if a.tool == "flag_for_review"), None)
        if flag:
            flag.args["reason"] = reason
        else:
            actions.append(Action("flag_for_review", {"reason": reason}, "some results are outside the normal range"))
    return actions
