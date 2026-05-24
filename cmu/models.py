from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class MemoryType(str, Enum):
    CANDIDATE = "candidate"
    SITUATION = "situation"
    ANCHOR = "anchor"
    PRACTICE = "practice"
    EXCEPTION = "exception"
    ANTI_PATTERN = "anti-pattern"
    QUESTION = "question"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"


class MemoryRelationType(str, Enum):
    SUPPORTS = "supports"
    CHALLENGES = "challenges"
    EXCEPTION_TO = "exception_to"
    DERIVED_FROM = "derived_from"
    SAME_SITUATION = "same_situation"
    RELATED_PRACTICE = "related_practice"


@dataclass
class MemoryScope:
    ownership: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    workflow: list[str] = field(default_factory=list)
    environment: list[str] = field(default_factory=list)
    actor: list[str] = field(default_factory=list)
    time: list[str] = field(default_factory=list)

    def flattened(self) -> list[str]:
        values: list[str] = []
        for items in asdict(self).values():
            values.extend(items)
        return values


@dataclass
class MemoryRelationship:
    type: MemoryRelationType
    target_id: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.type.value,
            "target_id": self.target_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRelationship":
        return cls(
            type=MemoryRelationType(data["type"]),
            target_id=data["target_id"],
            reason=data.get("reason", ""),
        )


@dataclass
class Memory:
    id: str
    type: MemoryType
    title: str
    summary: str
    signals: list[str]
    scope: MemoryScope
    evidence: list[str]
    use_this_path: str
    avoid_this: str
    challenge_only_if: str
    relationships: list[MemoryRelationship] = field(default_factory=list)
    liability_score: int = 1
    confidence: float = 0.6
    approved_by: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())

    @classmethod
    def create(
        cls,
        *,
        type: MemoryType,
        title: str,
        summary: str,
        signals: list[str] | None = None,
        scope: MemoryScope | None = None,
        evidence: list[str] | None = None,
        use_this_path: str = "",
        avoid_this: str = "",
        challenge_only_if: str = "",
        relationships: list[MemoryRelationship] | None = None,
        liability_score: int = 1,
        confidence: float = 0.6,
        approved_by: str = "",
    ) -> "Memory":
        return cls(
            id=f"mem_{uuid4().hex[:12]}",
            type=type,
            title=title.strip(),
            summary=summary.strip(),
            signals=[item.strip() for item in signals or [] if item.strip()],
            scope=scope or MemoryScope(),
            evidence=[item.strip() for item in evidence or [] if item.strip()],
            use_this_path=use_this_path.strip(),
            avoid_this=avoid_this.strip(),
            challenge_only_if=challenge_only_if.strip(),
            relationships=relationships or [],
            liability_score=max(1, min(liability_score, 5)),
            confidence=max(0.0, min(confidence, 1.0)),
            approved_by=approved_by.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        data["status"] = self.status.value
        data["relationships"] = [relationship.to_dict() for relationship in self.relationships]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Memory":
        return cls(
            id=data["id"],
            type=MemoryType(data["type"]),
            title=data["title"],
            summary=data["summary"],
            signals=list(data.get("signals", [])),
            scope=MemoryScope(**data.get("scope", {})),
            evidence=list(data.get("evidence", [])),
            use_this_path=data.get("use_this_path", ""),
            avoid_this=data.get("avoid_this", ""),
            challenge_only_if=data.get("challenge_only_if", ""),
            relationships=[
                MemoryRelationship.from_dict(item)
                for item in data.get("relationships", [])
            ],
            liability_score=int(data.get("liability_score", 1)),
            confidence=float(data.get("confidence", 0.6)),
            approved_by=data.get("approved_by", ""),
            status=MemoryStatus(data.get("status", MemoryStatus.ACTIVE.value)),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )


@dataclass
class ActionNote:
    recognized_situation: str
    why_it_matches: str
    use_this_path: str
    respect_this_memory: str
    avoid_this: str
    challenge_only_if: str
    evidence: str
    confidence: str

    def render(self) -> str:
        return "\n".join(
            [
                "CMU Action Note",
                f"Recognized Situation: {self.recognized_situation}",
                f"Why It Matches: {self.why_it_matches}",
                f"Use This Path: {self.use_this_path}",
                f"Respect This Memory: {self.respect_this_memory}",
                f"Avoid This: {self.avoid_this}",
                f"Challenge Only If: {self.challenge_only_if}",
                f"Evidence: {self.evidence}",
                f"Confidence: {self.confidence}",
            ]
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
