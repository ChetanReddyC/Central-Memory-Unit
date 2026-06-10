from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .json_store import read_json
from .usage import MemoryUseReceipt, is_drag_signal, is_resolved_without_commit


EVIDENCE_METRICS_VERSION = "cmu-evidence-metrics/v1"


@dataclass(frozen=True)
class EvidenceMetricsReport:
    root: str
    session_count: int
    total_linked: int
    total_needs_review: int
    total_skipped: int
    receipt_count: int
    linked_receipts: int
    unresolved_receipts: int
    strong_uses: int
    drag_signals: int
    resolved_without_commit: int
    source_counts: dict[str, int] = field(default_factory=dict)

    @property
    def usefulness_ratio(self) -> float:
        if not self.linked_receipts:
            return 0.0
        return round(self.strong_uses / self.linked_receipts, 2)

    @property
    def drag_ratio(self) -> float:
        if not self.receipt_count:
            return 0.0
        return round(self.drag_signals / self.receipt_count, 2)

    def render(self) -> str:
        lines = [
            "CMU Longitudinal Evidence Metrics",
            f"Version: {EVIDENCE_METRICS_VERSION}",
            "Mode: read-only trend view over evidence sessions and Memory Use Receipts.",
            f"Root: {self.root}",
            "",
            "Evidence Sessions:",
            f"- Sessions: {self.session_count}",
            f"- Linked By Sessions: {self.total_linked}",
            f"- Needs Review By Sessions: {self.total_needs_review}",
            f"- Skipped By Sessions: {self.total_skipped}",
            "",
            "Receipt Outcomes:",
            f"- Receipts: {self.receipt_count}",
            f"- Linked Receipts: {self.linked_receipts}",
            f"- Unresolved Receipts: {self.unresolved_receipts}",
            f"- Strong Uses: {self.strong_uses}",
            f"- Drag Signals: {self.drag_signals}",
            f"- Resolved Without Commit: {self.resolved_without_commit}",
            f"- Usefulness Ratio: {self.usefulness_ratio:.2f}",
            f"- Drag Ratio: {self.drag_ratio:.2f}",
            f"- Sources: {format_counts(self.source_counts)}",
            "",
            f"Trend Judgment: {trend_judgment(self)}",
            "Proof Meaning: CMU can now track usefulness and drag longitudinally across recorded evidence sessions instead of judging one receipt or one command at a time.",
        ]
        return "\n".join(lines)


def evidence_metrics_report(root: Path | str, receipts: list[MemoryUseReceipt]) -> EvidenceMetricsReport:
    root_path = Path(root)
    session_data = read_json(root_path / ".cmu" / "evidence_sessions.json", {"version": 1, "sessions": []})
    sessions = list(session_data.get("sessions", []))
    linked = [receipt for receipt in receipts if receipt.commit_hash]
    unresolved = [receipt for receipt in receipts if not receipt.commit_hash and not is_resolved_without_commit(receipt)]
    source_counts: dict[str, int] = {}
    for receipt in receipts:
        source = receipt.source_command or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
    return EvidenceMetricsReport(
        root=str(root_path),
        session_count=len(sessions),
        total_linked=sum(int(session.get("linked", 0)) for session in sessions),
        total_needs_review=sum(int(session.get("needs_review", 0)) for session in sessions),
        total_skipped=sum(int(session.get("skipped", 0)) for session in sessions),
        receipt_count=len(receipts),
        linked_receipts=len(linked),
        unresolved_receipts=len(unresolved),
        strong_uses=sum(1 for receipt in linked if receipt.outcome_signal == "committed" and receipt.link_confidence >= 0.75),
        drag_signals=sum(1 for receipt in receipts if is_drag_signal(receipt)),
        resolved_without_commit=sum(1 for receipt in receipts if is_resolved_without_commit(receipt)),
        source_counts=source_counts,
    )


def trend_judgment(report: EvidenceMetricsReport) -> str:
    if report.receipt_count == 0:
        return "no receipts yet; run CMU in the work loop before judging usefulness or drag"
    if report.unresolved_receipts:
        return "evidence still has open receipts; link or resolve them before tuning retrieval or trust"
    if report.drag_signals and report.drag_signals >= report.strong_uses:
        return "drag is at least as strong as usefulness; review memory scope and wording before broadening use"
    if report.strong_uses:
        return "memory has closed positive evidence; keep collecting focused uses within the proven scope"
    return "evidence is closed but not yet strong; keep observing before changing authority or retrieval thresholds"


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "None"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
