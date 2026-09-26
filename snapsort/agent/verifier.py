"""Checks the agent runs on its own work before acting. Everything except confirm_in_image is pure."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

from ..llm import LLMError, LLMUnavailable
from . import prompts
from .schemas import AMOUNT_KEYS, CONFIRM_SCHEMA, DATE_KEYS, REFERENCE_KEYS, REQUIRED_FIELDS

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) instructions",
    r"\bai assistant\b",
    r"\byou are now\b",
    r"\b(delete|erase|wipe|remove) (every|all) (file|files|document|documents)\b",
    r"\bupload\b.*\bhttps?://",
    r"\bsystem prompt\b",
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def injection_suspected(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


def _amount_forms(value: str) -> list[str]:
    try:
        f = float(re.sub(r"[^0-9.]", "", value))
    except ValueError:
        return []
    forms = {norm(f"{f:.2f}")}
    if f == int(f):
        forms.add(str(int(f)))
    return sorted(forms)


def _date_forms(d: date) -> tuple[list[str], list[str]]:
    """(numeric-only forms, unambiguous forms), normalised the same way as the text."""
    m = MONTHS[d.month - 1]
    mm = m[:3]
    numeric = [f"{d.day:02d}{d.month:02d}{d.year}", f"{d.day}{d.month}{d.year}", f"{d.day:02d}{d.month:02d}{d.year % 100:02d}"]
    named = [d.isoformat(), f"{d.day}{m}{d.year}", f"{d.day:02d}{m}{d.year}", f"{d.day}{mm}{d.year}",
             f"{d.day:02d}{mm}{d.year}", f"{m}{d.day}{d.year}", f"{mm}{d.day}{d.year}", f"{mm}{d.day:02d}{d.year}"]
    return [norm(x) for x in numeric], [norm(x) for x in named]


def verify_extraction(doc_type: str, fields: dict, text: str, today: date | None = None) -> list[Check]:
    today = today or date.today()
    ntext = norm(text)
    has_text = len(ntext) >= 25
    checks: list[Check] = []

    for key in REQUIRED_FIELDS.get(doc_type, []):
        present = bool(fields.get(key))
        label = key.replace("_", " ")
        checks.append(Check(f"required:{key}", present, f"found the {label}" if present else f"no {label} found"))

    for key, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            continue
        label = key.replace("_", " ")
        if key in AMOUNT_KEYS:
            forms = _amount_forms(value)
            if not forms:
                checks.append(Check(f"amount:{key}", False, f"{label} “{value}” isn't a number"))
            elif has_text:
                ok = any(f in ntext for f in forms)
                checks.append(Check(f"grounded:{key}", ok, f"{label} {value} appears in the document" if ok
                                    else f"{label} {value} does not appear in the document"))
        elif key in DATE_KEYS:
            try:
                d = date.fromisoformat(value)
            except ValueError:
                checks.append(Check(f"date:{key}", False, f"{label} “{value}” isn't a valid date"))
                continue
            if not 2000 <= d.year <= today.year + 10:
                checks.append(Check(f"date:{key}", False, f"{label} {value} has an unlikely year"))
                continue
            if has_text:
                numeric, named = _date_forms(d)
                in_numeric = any(f in ntext for f in numeric)
                in_named = any(f in ntext for f in named)
                ok = in_numeric or in_named
                checks.append(Check(f"grounded:{key}", ok, f"{label} {value} appears in the document" if ok
                                    else f"{label} {value} does not appear in the document"))
                if in_numeric and not in_named and d.day <= 12 and d.day != d.month:
                    other = date(d.year, d.day, d.month).isoformat()
                    checks.append(Check(f"ambiguous:{key}", False,
                                        f"the {label} is written only in numbers, so it could be {value} or {other}. I assumed day/month"))
        elif key in REFERENCE_KEYS and has_text:
            ok = norm(value) in ntext
            checks.append(Check(f"grounded:{key}", ok, f"{label} appears in the document" if ok
                                else f"{label} {value} does not appear in the document"))

    if injection_suspected(text):
        checks.append(Check("injection", False, "the text contains instructions aimed at an AI assistant. I treated it as data and didn't follow them"))
    if not has_text:
        checks.append(Check("text", True, "there was little readable text, so the values come from the model reading the image"))
    return checks


def confirm_in_image(llm, model: str, image: bytes, key: str, value: str) -> Check:
    label = key.replace("_", " ")
    try:
        out = llm.generate_json(model, prompts.confirm_prompt(f"{label}: {value}"), CONFIRM_SCHEMA, [image])
    except LLMUnavailable:
        raise
    except LLMError as e:
        return Check(f"visual:{key}", False, f"couldn't double-check the {label}: {e}")
    ok = bool(out.get("visible"))
    return Check(f"visual:{key}", ok, f"{label} {value} confirmed in the image" if ok else f"couldn't see {label} {value} in the image")


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)
