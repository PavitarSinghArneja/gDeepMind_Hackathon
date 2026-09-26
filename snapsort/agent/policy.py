"""The boundary between what the agent does alone and what it asks a person about."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .actions import Action

DEFAULT = {
    "confidence_threshold": 0.8,
    "auto": ["file_document", "create_reminder", "mark_duplicate"],
    "confirm": ["vault", "flag_for_review"],
    "always_confirm_doc_types": ["id_document", "prescription", "lab_report"],
    "never": ["delete a file", "share or upload anything", "send data over the network"],
}


@dataclass(frozen=True)
class Policy:
    confidence_threshold: float
    auto: frozenset
    confirm: frozenset
    always_confirm_doc_types: frozenset
    never: tuple


@dataclass
class Decision:
    auto: list[Action] = field(default_factory=list)
    confirm: list[Action] = field(default_factory=list)
    blocked: list[Action] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def load_policy(path: Path) -> Policy:
    data = dict(DEFAULT)
    if path.exists():
        data.update(yaml.safe_load(path.read_text()) or {})
    return Policy(
        confidence_threshold=float(data["confidence_threshold"]),
        auto=frozenset(data["auto"]),
        confirm=frozenset(data["confirm"]),
        always_confirm_doc_types=frozenset(data["always_confirm_doc_types"]),
        never=tuple(data["never"]),
    )


def gate(policy: Policy, actions: list[Action], *, doc_type: str, confidence: float, failed_checks=()) -> Decision:
    d = Decision()
    if confidence < policy.confidence_threshold:
        d.reasons.append(f"I'm only {confidence:.0%} sure about this file")
    d.reasons.extend(failed_checks)
    if doc_type in policy.always_confirm_doc_types:
        d.reasons.append(f"{doc_type.replace('_', ' ')}s are always checked with you")
    blocked = [a for a in actions if a.tool not in policy.auto and a.tool not in policy.confirm]
    for a in blocked:
        d.reasons.append(f"it asked for “{a.tool}”, which I'm not allowed to do, so I blocked it")
    cautious = bool(d.reasons)
    for a in actions:
        if a in blocked:
            d.blocked.append(a)
        elif a.tool in policy.auto and not cautious:
            d.auto.append(a)
        else:
            d.confirm.append(a)
    return d
