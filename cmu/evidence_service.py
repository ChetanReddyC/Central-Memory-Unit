from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

from .evidence_session import EVIDENCE_SESSION_VERSION, run_evidence_session
from .json_store import update_json
from .models import Memory, utc_now
from .usage import MemoryUseReceipt, MemoryUseStore


EVIDENCE_SERVICE_VERSION = "cmu-evidence-service/v1"


@dataclass(frozen=True)
class EvidenceServiceCycle:
    index: int
    session_id: str
    linked: int
    needs_review: int
    skipped: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "session_id": self.session_id,
            "linked": self.linked,
            "needs_review": self.needs_review,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class EvidenceServiceReport:
    root: str
    service_id: str
    started_at: str
    stopped_at: str
    interval_seconds: float
    apply: bool
    record_sessions: bool
    stop_file: str
    stopped_reason: str
    cycles: list[EvidenceServiceCycle] = field(default_factory=list)
    state_file: Path | None = None

    @property
    def linked(self) -> int:
        return sum(cycle.linked for cycle in self.cycles)

    @property
    def needs_review(self) -> int:
        return sum(cycle.needs_review for cycle in self.cycles)

    def render(self) -> str:
        lines = [
            "CMU Evidence Service",
            f"Version: {EVIDENCE_SERVICE_VERSION}",
            f"Evidence Session: {EVIDENCE_SESSION_VERSION}",
            "Mode: background service loop around evidence-session; stop with the configured stop file.",
            f"Service: {self.service_id}",
            f"Root: {self.root}",
            f"Interval Seconds: {self.interval_seconds:g}",
            f"Apply Links: {'yes' if self.apply else 'no'}",
            f"Record Sessions: {'yes' if self.record_sessions else 'no'}",
            f"Stop File: {self.stop_file}",
            f"Stopped: {self.stopped_reason}",
            "",
            "Summary:",
            f"- Cycles: {len(self.cycles)}",
            f"- Linked: {self.linked}",
            f"- Needs Review: {self.needs_review}",
            f"- State File: {self.state_file or 'not recorded'}",
            "",
            "Cycles:",
        ]
        lines.extend(
            f"- cycle {cycle.index}: session={cycle.session_id} linked={cycle.linked} needs_review={cycle.needs_review} skipped={cycle.skipped}"
            for cycle in self.cycles
        )
        if not self.cycles:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: CMU now has a real long-running evidence service entrypoint with durable service state, while still reusing the conservative evidence-session linking policy.",
            ]
        )
        return "\n".join(lines)


def run_evidence_service(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    interval_seconds: float = 60.0,
    max_cycles: int = 0,
    limit: int = 20,
    hours: int = 72,
    min_score: float = 0.75,
    min_confidence: float = 0.75,
    apply: bool = False,
    record_sessions: bool = True,
    record_service: bool = True,
    stop_file: str = ".cmu/evidence_service.stop",
) -> EvidenceServiceReport:
    if interval_seconds < 0:
        raise ValueError("evidence-service interval cannot be negative")
    if max_cycles < 0:
        raise ValueError("evidence-service max-cycles cannot be negative")
    root_path = Path(root)
    stop_path = root_path / stop_file
    service_id = f"evsvc_{utc_now().replace(':', '').replace('+', '_')}"
    started_at = utc_now()
    cycles: list[EvidenceServiceCycle] = []
    stopped_reason = "max-cycles" if max_cycles else "stop-file"
    index = 0
    while True:
        if stop_path.exists():
            stopped_reason = "stop-file"
            break
        index += 1
        report = run_evidence_session(
            root_path,
            memories,
            receipts,
            limit=limit,
            hours=hours,
            min_score=min_score,
            min_confidence=min_confidence,
            apply=apply,
            record=record_sessions,
        )
        cycles.append(
            EvidenceServiceCycle(
                index=index,
                session_id=report.record.id,
                linked=report.record.linked,
                needs_review=report.record.needs_review,
                skipped=report.record.skipped,
            )
        )
        receipts = MemoryUseStore(root_path).list()
        if max_cycles and index >= max_cycles:
            stopped_reason = "max-cycles"
            break
        if interval_seconds:
            time.sleep(interval_seconds)
    stopped_at = utc_now()
    state_file = root_path / ".cmu" / "evidence_service_runs.json" if record_service else None
    result = EvidenceServiceReport(
        root=str(root_path),
        service_id=service_id,
        started_at=started_at,
        stopped_at=stopped_at,
        interval_seconds=interval_seconds,
        apply=apply,
        record_sessions=record_sessions,
        stop_file=str(stop_path),
        stopped_reason=stopped_reason,
        cycles=cycles,
        state_file=state_file,
    )
    if record_service:
        update_json(
            state_file,
            {"version": 1, "service_runs": []},
            lambda data: append_service_run(data, result),
        )
    return result


def append_service_run(data: dict, report: EvidenceServiceReport) -> EvidenceServiceReport:
    data["service_runs"].append(
        {
            "schema": EVIDENCE_SERVICE_VERSION,
            "service_id": report.service_id,
            "started_at": report.started_at,
            "stopped_at": report.stopped_at,
            "root": report.root,
            "interval_seconds": report.interval_seconds,
            "apply": report.apply,
            "record_sessions": report.record_sessions,
            "stop_file": report.stop_file,
            "stopped_reason": report.stopped_reason,
            "cycles": [cycle.to_dict() for cycle in report.cycles],
        }
    )
    return report
