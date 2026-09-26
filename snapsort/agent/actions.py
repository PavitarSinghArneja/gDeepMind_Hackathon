"""A proposed action, passed between planner, policy gate and tools."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Action:
    """One step the agent wants to take. The model picks `tool` and `why`; code fills `args`."""

    tool: str
    args: dict = field(default_factory=dict)
    why: str = ""
    rule_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Action":
        return Action(d["tool"], dict(d.get("args") or {}), d.get("why", ""), d.get("rule_id"))
