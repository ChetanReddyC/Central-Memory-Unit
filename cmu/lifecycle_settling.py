from __future__ import annotations

from dataclasses import dataclass, field

from .gravity import gravity_report
from .models import Memory, MemoryScope, MemoryStatus, MemoryType, utc_now
from .usage import MemoryUseReceipt, use_summary


LIFECYCLE_SETTLING_VERSION = "cmu-lifecycle-settling/v1"


@dataclass(frozen=True)
class LifecycleSettlingItem:
    memory_id: str
    title: str
    action: str
    status: str
    reason: str
    command: str = ""

    def render(self) -> str:
        line = f"- {self.status}: {self.memory_id} {self.title} action={self.action} - {self.reason}"
        if self.command:
            line += f"\n  Command: {self.command}"
        return line


@dataclass
class LifecycleSettlingReport:
    title: str
    mode: str
    applied: bool = False
    ok: bool = True
    items: list[LifecycleSettlingItem] = field(default_factory=list)
    changed_memories: list[Memory] = field(default_factory=list)
    created_memories: list[Memory] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            self.title,
            f"Version: {LIFECYCLE_SETTLING_VERSION}",
            f"Mode: {self.mode}",
            f"Summary: total={len(self.items)} applied={'yes' if self.applied else 'no'} ok={'yes' if self.ok else 'no'}",
            "",
            "Lifecycle Settling Items:",
        ]
        lines.extend(item.render() for item in self.items)
        if not self.items:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: lifecycle settling now turns Memory Gravity and linked-use evidence into controlled settle and scope-refinement workflow items instead of only passive diagnostics.",
            ]
        )
        return "\n".join(lines)


def lifecycle_settle(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
    min_gravity: float = 3.2,
    apply: bool = False,
) -> LifecycleSettlingReport:
    cards = gravity_report(memories, receipts, memory_id=memory_id).cards
    memory_by_id = {memory.id: memory for memory in memories}
    items: list[LifecycleSettlingItem] = []
    changed: list[Memory] = []
    for card in cards:
        memory = memory_by_id[card.memory_id]
        if memory.status != MemoryStatus.ACTIVE:
            continue
        if "settle pressure" not in card.pressures or card.total_score < min_gravity:
            continue
        use = use_summary(receipts, memory.id)
        reason = (
            f"gravity={card.total_score:.2f}; committed={use.committed}; checkpoints={use.checkpoints}; "
            f"scope={', '.join(memory.scope.flattened())}"
        )
        items.append(
            LifecycleSettlingItem(
                memory_id=memory.id,
                title=memory.title,
                action="settle",
                status="settled" if apply else "would-settle",
                reason=reason,
                command=f"cmu lifecycle-settle --memory {memory.id} --apply",
            )
        )
        if apply:
            note = f"Lifecycle settled in current scope: {reason}"
            if note not in memory.evidence:
                memory.evidence.append(note)
            memory.confidence = round(min(1.0, memory.confidence + 0.05), 2)
            memory.updated_at = utc_now()
            changed.append(memory)
    return LifecycleSettlingReport(
        title="CMU Lifecycle Settling",
        mode="controlled settling from Memory Gravity and linked-use evidence.",
        applied=apply,
        items=items,
        changed_memories=changed,
    )


def lifecycle_scope_suggestions(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
    apply: bool = False,
) -> LifecycleSettlingReport:
    items: list[LifecycleSettlingItem] = []
    created: list[Memory] = []
    for memory in memories:
        if memory.status != MemoryStatus.ACTIVE or (memory_id and memory.id != memory_id):
            continue
        suggested = suggested_receipt_scope(memory, receipts)
        if suggested is None:
            continue
        reason = f"unresolved or drag evidence suggests narrower scope {format_scope(suggested)}"
        items.append(
            LifecycleSettlingItem(
                memory_id=memory.id,
                title=memory.title,
                action="scope-refinement",
                status="recorded" if apply else "would-record",
                reason=reason,
                command=f"cmu lifecycle-scope-suggest --memory {memory.id} --apply",
            )
        )
        if apply:
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title=f"Scope refinement proposal for {memory.title}",
                summary=f"Receipt evidence suggests narrowing or splitting scope for {memory.id}.",
                signals=["scope refinement", "memory gravity", "receipt evidence"],
                scope=suggested,
                evidence=[
                    f"Scope refinement target: {memory.id}",
                    f"Current scope: {format_scope(memory.scope)}",
                    f"Suggested scope: {format_scope(suggested)}",
                    "Created by lifecycle-scope-suggest from real Memory Use Receipt evidence.",
                ],
                use_this_path="Review this Candidate before narrowing, splitting, or changing retrieval scope.",
                avoid_this="Do not silently broaden or shift stable memory scope from receipt pressure alone.",
                challenge_only_if="Apply only through explicit review, challenge, split, or safe narrowing paths.",
                liability_score=memory.liability_score,
                confidence=0.65,
            )
            created.append(candidate)
    return LifecycleSettlingReport(
        title="CMU Lifecycle Scope Suggestions",
        mode="controlled scope-refinement Candidate generation from receipt evidence.",
        applied=apply,
        items=items,
        created_memories=created,
    )


def suggested_receipt_scope(memory: Memory, receipts: list[MemoryUseReceipt]) -> MemoryScope | None:
    relevant = [receipt for receipt in receipts if receipt.memory_id == memory.id]
    pressure = [
        receipt
        for receipt in relevant
        if receipt.outcome_signal in {"reverted", "low_confidence", "committed_low_confidence", "mixed_commit"} or "no_file_overlap" in receipt.flags
    ]
    if not pressure:
        return None
    files = common_values([receipt.files for receipt in pressure])
    workflow = common_values([receipt.workflow for receipt in pressure])
    environment = common_values([receipt.environment for receipt in pressure])
    if not files and not workflow and not environment:
        return None
    return MemoryScope(
        ownership=list(memory.scope.ownership),
        code=files or list(memory.scope.code),
        workflow=workflow or list(memory.scope.workflow),
        environment=environment or list(memory.scope.environment),
        actor=list(memory.scope.actor),
        time=list(memory.scope.time),
    )


def common_values(groups: list[list[str]]) -> list[str]:
    counts: dict[str, int] = {}
    for group in groups:
        for value in set(item.strip() for item in group if item.strip()):
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return []
    threshold = max(1, len(groups) // 2)
    return sorted(value for value, count in counts.items() if count >= threshold)


def format_scope(scope: MemoryScope) -> str:
    return ", ".join(scope.flattened()) if scope.flattened() else "none"
