"""Document types, the fields we extract for each, and the JSON schemas Ollama must follow."""
from __future__ import annotations

DOC_TYPES = [
    "bill", "bank_statement", "payment_receipt", "salary_slip", "prescription", "lab_report",
    "insurance_card", "id_document", "credential", "travel_ticket", "personal_photo", "other",
]

CATEGORY_FOLDERS = {
    "bill": "Finance/Bills",
    "bank_statement": "Finance/Statements",
    "payment_receipt": "Finance/Receipts",
    "salary_slip": "Finance/Salary",
    "prescription": "Health/Prescriptions",
    "lab_report": "Health/Lab Reports",
    "insurance_card": "Health/Insurance",
    "id_document": "Identity",
    "credential": "Secrets",
    "travel_ticket": "Travel",
    "personal_photo": "Photos",
    "other": "Other",
}

_S = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}
_TESTS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": _S, "value": _S, "unit": _S, "reference_range": _S,
                       "flag": {"type": "string", "enum": ["normal", "high", "low", "unknown"]}},
        "required": ["name", "value", "flag"],
    },
}

FIELD_SPECS = {
    "bill": {"vendor": _S, "amount": _S, "due_date": _S, "billing_period": _S, "account_last4": _S},
    "bank_statement": {"bank": _S, "period_start": _S, "period_end": _S, "closing_balance": _S},
    "payment_receipt": {"payee": _S, "amount": _S, "date": _S, "reference": _S},
    "salary_slip": {"employer": _S, "month": _S, "net_pay": _S},
    "prescription": {"doctor": _S, "patient": _S, "date": _S, "medicines": _LIST},
    "lab_report": {"lab": _S, "patient": _S, "date": _S, "tests": _TESTS},
    "insurance_card": {"insurer": _S, "policy_number": _S, "member": _S, "valid_until": _S},
    "id_document": {"id_kind": _S, "name": _S, "id_last4": _S},
    "credential": {"service": _S, "username": _S, "secret": _S},
    "travel_ticket": {"carrier": _S, "origin": _S, "destination": _S, "date": _S, "reference": _S},
    "personal_photo": {"description": _S},
    "other": {"description": _S},
}

REQUIRED_FIELDS = {
    "bill": ["vendor", "amount", "due_date"],
    "bank_statement": ["bank"],
    "payment_receipt": ["payee", "amount"],
    "salary_slip": ["employer", "net_pay"],
    "prescription": ["doctor"],
    "lab_report": ["tests"],
    "insurance_card": ["insurer", "policy_number"],
    "id_document": ["id_kind"],
    "credential": ["secret"],
    "travel_ticket": ["date"],
}

ISSUER_KEYS = ("vendor", "bank", "payee", "employer", "doctor", "lab", "insurer", "service", "carrier", "id_kind")
AMOUNT_KEYS = ("amount", "closing_balance", "net_pay")
DATE_KEYS = ("due_date", "date", "period_start", "period_end", "valid_until")
REFERENCE_KEYS = ("reference", "policy_number", "account_last4", "id_last4")

TOOLS = ["file_document", "create_reminder", "vault", "flag_for_review"]

TRIAGE_SCHEMA = {
    "title": "triage",
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "sensitivity": {"type": "string", "enum": ["low", "medium", "high"]},
        "contains_secret": {"type": "boolean"},
        "title": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["doc_type", "sensitivity", "contains_secret", "title", "confidence"],
}


def extract_schema(doc_type: str) -> dict:
    """Fields sit at the top level and are all required: small models leave optional nested objects empty."""
    props = {"summary": {"type": "string"}, **FIELD_SPECS[doc_type], "confidence": {"type": "number"}}
    return {"title": "extract", "type": "object", "properties": props, "required": list(props)}


PLAN_SCHEMA = {
    "title": "plan",
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string", "enum": TOOLS}, "why": {"type": "string"}},
                "required": ["tool", "why"],
            },
        }
    },
    "required": ["actions"],
}

ASK_SCHEMA = {
    "title": "ask",
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["found", "answer", "citations"],
}

CONFIRM_SCHEMA = {
    "title": "confirm",
    "type": "object",
    "properties": {"visible": {"type": "boolean"}},
    "required": ["visible"],
}
