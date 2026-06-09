from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .evidence_monitor import EvidenceMonitorReport, monitor_checkpoints
from .json_store import update_json
from .models import Memory, utc_now
from .usage import MemoryUseReceipt


EVIDENCE_SESSION_VERSION = "cmu-evidence-session/v1"


@dataclass(frozen=True)
class EvidenceSessionRecord:
    id: str
    created_at: str
    root: str
    applied: bool
    linked: int
    needs_review: int
    skipped: int
    item_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "root": self.root,
            "applied": self.applied,
            "linked": self.linked,
            "needs_review": self.needs_review,
            "skipped": self.skipped,
            "item_ids": self.item_ids,
        }


@dataclass(frozen=True)
class EvidenceSessionReport:
    record: EvidenceSessionRecord
    monitor: EvidenceMonitorReport
    recorded: bool

    @property
    def ok(self) -> bool:
        return not self.monitor.error

    def render(self) -> str:
        lines = [
            "CMU Evidence Session",
            f"Version: {EVIDENCE_SESSION_VERSION}",
            "Mode: session checkpoint monitor; applies clean links only when requested and can record the session summary.",
            f"Session: {self.record.id}",
            f"Root: {self.record.root}",
            f"Recorded: {'yes' if self.recorded else 'no'}",
            f"Applied Links: {'yes' if self.record.applied else 'no'}",
            f"Summary: linked={self.record.linked} needs_review={self.record.needs_review} skipped={self.record.skipped}",
            "",
            "Monitor Snapshot:",
            self.monitor.render(),
        ]
        lines.extend(
            [
                "",
                "Proof Meaning: CMU now has a session-level evidence workflow that can be invoked by schedulers or long-running hosts while preserving the same conservative monitor policy.",
            ]
        )
        return "\n".join(lines)


def run_evidence_session(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    limit: int = 20,
    hours: int = 72,
    min_score: float = 0.75,
    min_confidence: float = 0.75,
    apply: bool = False,
    record: bool = False,
) -> EvidenceSessionReport:
    root_path = Path(root)
    monitor = monitor_checkpoints(
        root_path,
        memories,
        receipts,
        limit=limit,
        hours=hours,
        min_score=min_score,
        min_confidence=min_confidence,
        apply=apply,
    )
    session = EvidenceSessionRecord(
        id=f"evs_{uuid4().hex[:12]}",
        created_at=utc_now(),
        root=str(root_path),
        applied=apply,
        linked=monitor.linked_count,
        needs_review=monitor.review_count,
        skipped=monitor.skipped_count,
        item_ids=[item.receipt.id for item in monitor.items],
    )
    if record:
        update_json(
            root_path / ".cmu" / "evidence_sessions.json",
            {"version": 1, "sessions": []},
            lambda data: append_session(data, session),
        )
    return EvidenceSessionReport(record=session, monitor=monitor, recorded=record)


def append_session(data: dict, session: EvidenceSessionRecord) -> EvidenceSessionRecord:
    data["sessions"].append(session.to_dict())
    return session
