from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ToolResult:
    tool: str
    found: bool
    data: dict | list | None
    evidence_ids: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    unanswered_questions: list[str] = field(default_factory=list)
    invented: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvestigationResult:
    findings: list[dict] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    risk_signals: list[dict] = field(default_factory=list)
    unanswered_questions: list[str] = field(default_factory=list)
    transaction_id: str | None = None
    invented: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
