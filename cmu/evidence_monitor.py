from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import Memory
from .usage import (
    CommitLinkRequest,
    MemoryUseReceipt,
    MemoryUseStore,
    auto_link_receipts,
    format_list,
    inspect_git_commit,
    link_commit,
    short_hash,
)


EVIDENCE_MONITOR_VERSION = "cmu-evidence-monitor/v1"
DEFAULT_MONITOR_MIN_SCORE = 0.75
DEFAULT_MONITOR_MIN_CONFIDENCE = 0.75
RISKY_MONITOR_FLAGS = {"wip_commit", "reverted_after_use", "mixed_commit", "no_file_overlap", "delayed_commit"}


@dataclass
class EvidenceMonitorItem:
    receipt: MemoryUseReceipt
    status: str
    reason: str
    score: float = 0.0
    commit_hash: str = ""
    flags: list[str] = field(default_factory=list)
    applied: bool = False

    def render(self) -> str:
        commit = f" {short_hash(self.commit_hash)}" if self.commit_hash else ""
        score = f" score={self.score:.2f}" if self.score else ""
        flags = f" flags={format_list(self.flags)}" if self.flags else ""
        return f"- {self.status}: {self.receipt.id}{commit}{score}{flags} - {self.reason}"


@dataclass
class EvidenceMonitorReport:
    root: str
    applied: bool
    items: list[EvidenceMonitorItem]
    error: str = ""

    @property
    def linked_count(self) -> int:
        return sum(1 for item in self.items if item.status == "linked")

    @property
    def review_count(self) -> int:
        return sum(1 for item in self.items if item.status == "needs-review")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    def render(self) -> str:
        if self.error:
            return "\n".join(
                [
                    "CMU Evidence Monitor Not Applied",
                    f"Version: {EVIDENCE_MONITOR_VERSION}",
                    f"Root: {self.root}",
                    f"Reason: {self.error}",
                    "No receipts were changed.",
                ]
            )
        header = "CMU Evidence Monitor Applied" if self.applied else "CMU Evidence Monitor Dry Run"
        lines = [
            header,
            f"Version: {EVIDENCE_MONITOR_VERSION}",
            "Mode: checkpoint monitor for confident automatic receipt linking.",
            f"Root: {self.root}",
            f"Summary: total={len(self.items)} linked={self.linked_count} needs_review={self.review_count} skipped={self.skipped_count}",
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No unlinked Memory Use Receipts found."])
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can inspect recent Git checkpoints and link only high-confidence clean matches while leaving WIP, reverted, mixed, delayed, ambiguous, or weak evidence for review.",
            ]
        )
        return "\n".join(lines)


def monitor_checkpoints(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    limit: int = 20,
    hours: int = 72,
    min_score: float = DEFAULT_MONITOR_MIN_SCORE,
    min_confidence: float = DEFAULT_MONITOR_MIN_CONFIDENCE,
    apply: bool = False,
) -> EvidenceMonitorReport:
    root_path = Path(root)
    dry_auto = auto_link_receipts(
        receipts,
        memories,
        root=root_path,
        limit=limit,
        hours=hours,
        min_score=min_score,
        apply=False,
    )
    if dry_auto.error:
        return EvidenceMonitorReport(root=str(root_path), applied=apply, items=[], error=dry_auto.error)

    items: list[EvidenceMonitorItem] = []
    for decision in dry_auto.decisions:
        if not decision.matched:
            items.append(
                EvidenceMonitorItem(
                    receipt=decision.receipt,
                    status="needs-review",
                    reason=decision.reason,
                    score=decision.score,
                    commit_hash=decision.commit_hash,
                )
            )
            continue
        try:
            commit = inspect_git_commit(root_path, decision.commit_hash)
        except RuntimeError as error:
            items.append(EvidenceMonitorItem(receipt=decision.receipt, status="needs-review", reason=str(error)))
            continue
        link_decision = link_commit(
            decision.receipt,
            CommitLinkRequest(
                use_id=decision.receipt.id,
                commit_hash=commit.commit_hash,
                message=commit.message,
                files=commit.files,
                commit_time=commit.commit_time,
                metadata_source="git-monitor",
                note=f"Checkpoint monitor matched recent Git evidence with score {decision.score:.2f}.",
            ),
        )
        linked = link_decision.receipt
        if not link_decision.linked or linked is None:
            items.append(
                EvidenceMonitorItem(
                    receipt=decision.receipt,
                    status="needs-review",
                    reason=link_decision.reason,
                    score=decision.score,
                    commit_hash=commit.commit_hash,
                )
            )
            continue
        risky_flags = sorted(flag for flag in linked.flags if flag in RISKY_MONITOR_FLAGS)
        if linked.outcome_signal != "committed":
            risky_flags.append(f"outcome:{linked.outcome_signal}")
        if linked.link_confidence < min_confidence:
            risky_flags.append(f"confidence:{linked.link_confidence:.2f}")
        if risky_flags:
            items.append(
                EvidenceMonitorItem(
                    receipt=linked,
                    status="needs-review",
                    reason="matched commit needs human review before linking",
                    score=decision.score,
                    commit_hash=commit.commit_hash,
                    flags=risky_flags,
                )
            )
            continue
        if apply:
            MemoryUseStore(root_path).update(linked)
        items.append(
            EvidenceMonitorItem(
                receipt=linked,
                status="linked",
                reason="high-confidence clean checkpoint match",
                score=decision.score,
                commit_hash=commit.commit_hash,
                flags=linked.flags,
                applied=apply,
            )
        )
    return EvidenceMonitorReport(root=str(root_path), applied=apply, items=items)
