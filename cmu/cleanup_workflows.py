from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .analytics import usefulness_analytics_report
from .authority import set_memory_authority
from .governance import governance_report
from .graphview import graph_memory_view_report
from .lifecycle import lifecycle_report
from .models import Memory, MemoryStatus, MemoryType
from .quality import quality_report
from .retrieval import Match, PreflightQuery
from .usage import MemoryUseReceipt, resolve_receipt_without_commit, use_review


CLEANUP_WORKFLOW_VERSION = "cmu-cleanup-workflows/v1"
STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}
EVIDENCE_TYPES = {MemoryType.SITUATION, MemoryType.ANTI_PATTERN, MemoryType.QUESTION}


@dataclass(frozen=True)
class CleanupItem:
    subject_id: str
    title: str
    action: str
    state: str
    applied: bool = False
    detail: str = ""

    def render(self) -> str:
        applied = "applied" if self.applied else "preview"
        detail = f"; {self.detail}" if self.detail else ""
        return f"- {self.action}: {self.subject_id} {self.title} [{applied}] {self.state}{detail}"


@dataclass(frozen=True)
class CleanupWorkflowReport:
    name: str
    root: str
    items: list[CleanupItem] = field(default_factory=list)
    written_path: str = ""

    def render(self) -> str:
        lines = [
            f"CMU {self.name}",
            f"Version: {CLEANUP_WORKFLOW_VERSION}",
            f"Root: {self.root}",
            f"Items: {len(self.items)}",
        ]
        if self.written_path:
            lines.append(f"Written: {self.written_path}")
        lines.append("")
        lines.append("Results:")
        lines.extend(item.render() for item in self.items) if self.items else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: this workflow uses real CMU stores and explicit preview/apply gates instead of treating cleanup as a prose checklist.",
            ]
        )
        return "\n".join(lines)


def authority_cleanup(
    root: Path | str,
    memories: list[Memory],
    *,
    owner: str,
    approved_by: str,
    approver_role: str,
    consequence: str,
    review_due: str = "",
    apply: bool = False,
    store=None,
) -> CleanupWorkflowReport:
    items: list[CleanupItem] = []
    for memory in memories:
        if memory.status != MemoryStatus.ACTIVE or memory.type not in STABLE_TYPES:
            continue
        missing = [
            name
            for name, value in [
                ("authority_owner", memory.authority_owner),
                ("authority_role", memory.authority_role),
                ("authority_consequence", memory.authority_consequence),
            ]
            if not value.strip()
        ]
        if not missing:
            continue
        if not apply:
            items.append(
                CleanupItem(
                    memory.id,
                    memory.title,
                    "authority-cleanup",
                    "missing " + ", ".join(missing),
                )
            )
            continue
        decision = set_memory_authority(
            memory,
            owner=owner or memory.authority_owner or first(memory.scope.ownership) or approved_by,
            approved_by=approved_by,
            approver_role=approver_role,
            consequence=consequence,
            review_due_at=review_due,
        )
        if decision.applied and decision.memory is not None and store is not None:
            store.update(decision.memory)
        items.append(
            CleanupItem(
                memory.id,
                memory.title,
                "authority-cleanup",
                decision.reason,
                applied=decision.applied,
                detail=", ".join(decision.missing),
            )
        )
    return CleanupWorkflowReport("Authority Cleanup", str(Path(root)), items)


def receipt_closure(
    root: Path | str,
    receipts: list[MemoryUseReceipt],
    *,
    outcome: str,
    note: str,
    resolved_by: str,
    apply: bool = False,
    use_store=None,
) -> CleanupWorkflowReport:
    items: list[CleanupItem] = []
    for receipt in receipts:
        if receipt.commit_hash or receipt.outcome_signal:
            continue
        if not apply:
            items.append(CleanupItem(receipt.id, receipt.memory_title, "receipt-closure", "unresolved receipt"))
            continue
        decision = resolve_receipt_without_commit(receipt, outcome=outcome, note=note, resolved_by=resolved_by)
        if decision.resolved and decision.receipt is not None and use_store is not None:
            use_store.update(decision.receipt)
        items.append(
            CleanupItem(
                receipt.id,
                receipt.memory_title,
                "receipt-closure",
                decision.reason,
                applied=decision.resolved,
                detail=", ".join(decision.missing),
            )
        )
    return CleanupWorkflowReport("Receipt Closure", str(Path(root)), items)


def cleanup_evidence(
    root: Path | str,
    memories: list[Memory],
    *,
    apply: bool = False,
    use_store=None,
) -> CleanupWorkflowReport:
    items: list[CleanupItem] = []
    for memory in memories:
        if memory.status != MemoryStatus.ACTIVE or memory.type not in EVIDENCE_TYPES:
            continue
        query = evidence_query(memory)
        receipt = MemoryUseReceipt.create(
            memory,
            query,
            Match(memory=memory, score=3.75, matched_terms=["cleanup-evidence", memory.type.value]),
            source_command="cleanup-evidence",
        )
        if apply and use_store is not None:
            use_store.add(receipt)
        items.append(
            CleanupItem(
                receipt.id if apply else memory.id,
                memory.title,
                "cleanup-evidence",
                f"focused {memory.type.value} use receipt",
                applied=apply,
                detail=f"memory={memory.id}",
            )
        )
    return CleanupWorkflowReport("Cleanup Evidence", str(Path(root)), items)


def cleanup_audit_bundle(root: Path | str, memories: list[Memory], receipts: list[MemoryUseReceipt], *, write: bool = False) -> CleanupWorkflowReport:
    root_path = Path(root)
    payload = {
        "schema": CLEANUP_WORKFLOW_VERSION,
        "quality": quality_report(memories, receipts).render(),
        "governance": governance_report(memories, receipts).render(),
        "analytics": usefulness_analytics_report(memories, receipts).render(),
        "graph": graph_memory_view_report(memories).render(),
        "lifecycle": lifecycle_report(memories, receipts).render(),
        "use_review": use_review(receipts, memories).render(),
    }
    output_path = root_path / ".cmu" / "cleanup_audit.json"
    if write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    item = CleanupItem(
        "cleanup-audit",
        "quality/governance/analytics/graph/lifecycle",
        "cleanup-audit",
        "post-evidence outputs rendered",
        applied=write,
        detail=str(output_path) if write else "",
    )
    return CleanupWorkflowReport("Cleanup Audit", str(root_path), [item], written_path=str(output_path) if write else "")


def evidence_query(memory: Memory) -> PreflightQuery:
    flattened = memory.scope.flattened()
    return PreflightQuery(
        prompt=" ".join([memory.title, memory.summary, memory.use_this_path, memory.avoid_this]).strip(),
        actor=first(memory.scope.actor) or "agent",
        area=first(memory.scope.ownership) or memory.type.value,
        files=memory.scope.code[:],
        workflow=memory.scope.workflow[:] or [memory.type.value],
        environment=memory.scope.environment[:],
        risk="high" if memory.liability_score >= 4 else "medium",
    )


def first(values: list[str]) -> str:
    return values[0] if values else ""
