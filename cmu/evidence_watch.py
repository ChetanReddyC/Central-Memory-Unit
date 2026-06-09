from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

from .evidence_session import EVIDENCE_SESSION_VERSION, EvidenceSessionReport, run_evidence_session
from .models import Memory
from .usage import MemoryUseReceipt, MemoryUseStore


EVIDENCE_WATCH_VERSION = "cmu-evidence-watch/v1"


@dataclass(frozen=True)
class EvidenceWatchCycle:
    index: int
    session_id: str
    linked: int
    needs_review: int
    skipped: int
    recorded: bool

    @property
    def clean(self) -> bool:
        return self.needs_review == 0


@dataclass(frozen=True)
class EvidenceWatchReport:
    root: str
    interval_seconds: float
    cycles: list[EvidenceWatchCycle] = field(default_factory=list)
    session_reports: list[EvidenceSessionReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.cycles) and all(report.ok for report in self.session_reports)

    @property
    def linked(self) -> int:
        return sum(cycle.linked for cycle in self.cycles)

    @property
    def needs_review(self) -> int:
        return sum(cycle.needs_review for cycle in self.cycles)

    def render(self) -> str:
        lines = [
            "CMU Evidence Watch",
            f"Version: {EVIDENCE_WATCH_VERSION}",
            f"Evidence Session: {EVIDENCE_SESSION_VERSION}",
            "Mode: bounded watch loop around evidence-session; applies only the same clean high-confidence links.",
            f"Root: {self.root}",
            f"Interval Seconds: {self.interval_seconds:g}",
            f"Status: {'pass' if self.ok else 'review'}",
            "",
            "Summary:",
            f"- Cycles: {len(self.cycles)}",
            f"- Linked: {self.linked}",
            f"- Needs Review: {self.needs_review}",
            "",
            "Cycles:",
        ]
        if not self.cycles:
            lines.append("- None")
        else:
            for cycle in self.cycles:
                lines.append(
                    f"- cycle {cycle.index}: session={cycle.session_id} linked={cycle.linked} "
                    f"needs_review={cycle.needs_review} skipped={cycle.skipped} recorded={'yes' if cycle.recorded else 'no'}"
                )
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can now be run by a scheduler or long-running host as a real evidence watch loop while preserving the conservative receipt-to-checkpoint linking policy.",
            ]
        )
        return "\n".join(lines)


def run_evidence_watch(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    cycles: int = 1,
    interval_seconds: float = 0.0,
    limit: int = 20,
    hours: int = 72,
    min_score: float = 0.75,
    min_confidence: float = 0.75,
    apply: bool = False,
    record: bool = False,
) -> EvidenceWatchReport:
    if cycles < 1:
        raise ValueError("evidence-watch requires at least one cycle")
    if interval_seconds < 0:
        raise ValueError("evidence-watch interval cannot be negative")
    session_reports: list[EvidenceSessionReport] = []
    cycle_records: list[EvidenceWatchCycle] = []
    for index in range(1, cycles + 1):
        report = run_evidence_session(
            root,
            memories,
            receipts,
            limit=limit,
            hours=hours,
            min_score=min_score,
            min_confidence=min_confidence,
            apply=apply,
            record=record,
        )
        session_reports.append(report)
        cycle_records.append(
            EvidenceWatchCycle(
                index=index,
                session_id=report.record.id,
                linked=report.record.linked,
                needs_review=report.record.needs_review,
                skipped=report.record.skipped,
                recorded=report.recorded,
            )
        )
        if index < cycles and interval_seconds:
            time.sleep(interval_seconds)
        receipts = MemoryUseStore(root).list()
    return EvidenceWatchReport(
        root=str(root),
        interval_seconds=interval_seconds,
        cycles=cycle_records,
        session_reports=session_reports,
    )
