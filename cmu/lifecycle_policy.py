from __future__ import annotations

from dataclasses import dataclass, field

from .authority import review_is_expired
from .gravity import gravity_report
from .lifecycle_ops import dedupe, lifecycle_merge
from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType, utc_now
from .quality import apply_decay_action, quality_card
from .usage import MemoryUseReceipt, is_drag_signal, scope_change_is_safe_narrowing, scope_change_summary


LIFECYCLE_POLICY_VERSION = "cmu-lifecycle-policy/v1"


@dataclass(frozen=True)
class LifecyclePolicyItem:
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
class LifecyclePolicyReport:
    applied: bool = False
    ok: bool = True
    items: list[LifecyclePolicyItem] = field(default_factory=list)
    changed_memories: list[Memory] = field(default_factory=list)
    created_memories: list[Memory] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Lifecycle Policy Review",
            f"Version: {LIFECYCLE_POLICY_VERSION}",
            "Mode: cross-surface lifecycle review for settling, merge, split, decay, and scope refinement.",
            f"Summary: total={len(self.items)} applied={'yes' if self.applied else 'no'} ok={'yes' if self.ok else 'no'}",
            "",
            "Lifecycle Policy Cards:",
        ]
        if self.items:
            lines.extend(item.render() for item in self.items)
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: lifecycle pressure now has one review/apply surface that uses existing controlled gates instead of leaving merge, split, decay, and scope refinement as disconnected CLI commands.",
            ]
        )
        return "\n".join(lines)


def lifecycle_policy_review(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
    approved_by: str = "",
    approver_role: str = "",
    apply: bool = False,
) -> LifecyclePolicyReport:
    active = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE and (not memory_id or memory.id == memory_id)]
    memory_by_id = {memory.id: memory for memory in memories}
    items: list[LifecyclePolicyItem] = []
    changed: dict[str, Memory] = {}
    created: list[Memory] = []

    gravity = {card.memory_id: card for card in gravity_report(memories, receipts, memory_id=memory_id).cards}
    merge_pairs = proposed_merge_pairs(active)
    for target, source in merge_pairs:
        status = "merged" if apply and approved_by.strip() else ("blocked" if apply else "would-merge")
        reason = f"duplicate-like memory terms overlap; source={source.id}"
        command = f"cmu lifecycle-policy --memory {target.id} --approved-by <owner> --apply"
        if apply and approved_by.strip():
            merge = lifecycle_merge(
                memories,
                target_id=target.id,
                source_id=source.id,
                reason="Lifecycle policy duplicate-like merge pressure.",
                approved_by=approved_by,
                apply=True,
            )
            for memory in merge.changed_memories:
                changed[memory.id] = memory
        elif apply:
            reason += "; missing approved_by"
        items.append(LifecyclePolicyItem(target.id, target.title, "merge-policy", status, reason, command))

    for memory in active:
        card = gravity.get(memory.id)
        if card and "split pressure" in card.pressures:
            candidate = split_candidate(memory, receipts)
            if apply:
                created.append(candidate)
            items.append(
                LifecyclePolicyItem(
                    memory.id,
                    memory.title,
                    "split-policy",
                    "recorded" if apply else "would-record",
                    "broad scope plus drag evidence requires a split Candidate before broader trust.",
                    f"cmu lifecycle-policy --memory {memory.id} --apply",
                )
            )

        quality = quality_card(memory, receipts)
        drag_count = sum(1 for receipt in receipts if receipt.memory_id == memory.id and is_drag_signal(receipt))
        if quality.state == "decay-ready" or drag_count or review_is_expired(memory):
            action = "retire" if quality.state == "decay-ready" and memory.type == MemoryType.CANDIDATE else "weaken"
            status = "decayed" if apply else "would-decay"
            reason = f"quality={quality.state}; score={quality.score:.2f}; signals={', '.join(quality.signals)}"
            if apply:
                decision = apply_decay_action(
                    memories,
                    receipts,
                    memory.id,
                    action=action,
                    reason="Lifecycle policy decay pressure from quality and use evidence.",
                    approved_by=approved_by,
                    approver_role=approver_role,
                )
                if decision.applied and decision.memory is not None:
                    changed[decision.memory.id] = decision.memory
                else:
                    status = "blocked"
                    reason = decision.reason
            items.append(
                LifecyclePolicyItem(
                    memory.id,
                    memory.title,
                    "decay-policy",
                    status,
                    reason,
                    f"cmu lifecycle-policy --memory {memory.id} --approved-by <owner> --approver-role owner --apply",
                )
            )

    for candidate in active:
        target_id = scope_refinement_target(candidate)
        if not target_id:
            continue
        target = memory_by_id.get(target_id)
        if target is None or target.status != MemoryStatus.ACTIVE:
            continue
        safe = scope_change_is_safe_narrowing(target.scope, candidate.scope)
        status = "narrowed" if apply and approved_by.strip() and safe else ("blocked" if apply else "would-narrow")
        changes = scope_change_summary(target.scope, candidate.scope)
        reason = f"Candidate proposes safe scope narrowing: {', '.join(changes) if changes else 'no scope delta'}"
        if apply and approved_by.strip() and safe:
            target.scope = copy_scope(candidate.scope)
            target.evidence = dedupe(
                target.evidence
                + [
                    f"Lifecycle scope refinement applied from Candidate: {candidate.id}",
                    f"Scope refinement approved by: {approved_by.strip()}",
                    f"Scope changes: {', '.join(changes) if changes else 'none'}",
                ]
            )
            target.updated_at = utc_now()
            candidate.status = MemoryStatus.RETIRED
            candidate.evidence = dedupe(candidate.evidence + [f"Scope refinement applied to target: {target.id}"])
            changed[target.id] = target
            changed[candidate.id] = candidate
        elif apply:
            missing = []
            if not approved_by.strip():
                missing.append("approved_by")
            if not safe:
                missing.append("safe_narrowing")
            reason += f"; missing {', '.join(missing)}"
        items.append(
            LifecyclePolicyItem(
                target.id,
                target.title,
                "scope-refinement-apply",
                status,
                reason,
                f"cmu lifecycle-policy --memory {candidate.id} --approved-by <owner> --apply",
            )
        )

    return LifecyclePolicyReport(
        applied=apply,
        ok=not any(item.status == "blocked" for item in items),
        items=items,
        changed_memories=list(changed.values()),
        created_memories=created,
    )


def proposed_merge_pairs(memories: list[Memory]) -> list[tuple[Memory, Memory]]:
    pairs: list[tuple[Memory, Memory]] = []
    used: set[str] = set()
    for memory in memories:
        if memory.id in used:
            continue
        partner = next(
            (
                other
                for other in memories
                if other.id != memory.id
                and other.id not in used
                and other.type == memory.type
                and len(merge_terms(memory) & merge_terms(other)) >= 6
                and scope_overlap(memory, other)
            ),
            None,
        )
        if partner is None:
            continue
        target, source = sorted([memory, partner], key=lambda item: (-item.confidence, item.created_at, item.id))
        pairs.append((target, source))
        used.update({target.id, source.id})
    return pairs


def merge_terms(memory: Memory) -> set[str]:
    text = " ".join(
        [
            memory.title,
            memory.summary,
            " ".join(memory.signals),
            memory.use_this_path,
            memory.avoid_this,
            memory.challenge_only_if,
        ]
    )
    stopwords = {
        "memory",
        "candidate",
        "situation",
        "practice",
        "should",
        "when",
        "with",
        "this",
        "that",
        "scope",
        "guidance",
    }
    return {term for term in text.lower().replace("/", " ").replace("-", " ").split() if len(term) > 3 and term not in stopwords}


def scope_overlap(left: Memory, right: Memory) -> bool:
    for axis in ["ownership", "code", "workflow", "environment", "actor", "time"]:
        left_values = {value.strip().lower() for value in getattr(left.scope, axis) if value.strip()}
        right_values = {value.strip().lower() for value in getattr(right.scope, axis) if value.strip()}
        if left_values and right_values and left_values & right_values:
            return True
    return not left.scope.flattened() or not right.scope.flattened()


def split_candidate(memory: Memory, receipts: list[MemoryUseReceipt]) -> Memory:
    drag_receipts = [receipt for receipt in receipts if receipt.memory_id == memory.id and is_drag_signal(receipt)]
    scope = MemoryScope(
        ownership=list(memory.scope.ownership),
        code=common_values([receipt.files for receipt in drag_receipts]) or list(memory.scope.code),
        workflow=common_values([receipt.workflow for receipt in drag_receipts]) or list(memory.scope.workflow),
        environment=common_values([receipt.environment for receipt in drag_receipts]) or list(memory.scope.environment),
        actor=list(memory.scope.actor),
        time=list(memory.scope.time),
    )
    return Memory.create(
        type=MemoryType.CANDIDATE,
        title=f"Split proposal for {memory.title}",
        summary=f"Drag evidence suggests splitting {memory.id} before broad retrieval use.",
        signals=["lifecycle split policy", "drag evidence", "scope pressure"],
        scope=scope,
        evidence=[
            f"Split target: {memory.id}",
            f"Drag receipts: {', '.join(receipt.id for receipt in drag_receipts) if drag_receipts else 'none'}",
            "Created by lifecycle-policy from real Memory Use Receipt pressure.",
        ],
        use_this_path="Review this Candidate to create a narrower memory or exception before trusting the original broadly.",
        avoid_this="Do not silently broaden or strengthen the original memory while split pressure remains open.",
        challenge_only_if="Apply only through explicit split, challenge, narrow-scope, or retirement review.",
        relationships=[MemoryRelationship(type=MemoryRelationType.DERIVED_FROM, target_id=memory.id, reason="lifecycle split policy")],
        liability_score=memory.liability_score,
        confidence=0.62,
    )


def scope_refinement_target(memory: Memory) -> str:
    for evidence in memory.evidence:
        if evidence.startswith("Scope refinement target:"):
            return evidence.split(":", 1)[1].strip()
    return ""


def common_values(groups: list[list[str]]) -> list[str]:
    counts: dict[str, int] = {}
    for group in groups:
        for value in {item.strip() for item in group if item.strip()}:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return []
    threshold = max(1, len(groups) // 2)
    return sorted(value for value, count in counts.items() if count >= threshold)


def copy_scope(scope: MemoryScope) -> MemoryScope:
    return MemoryScope(
        ownership=list(scope.ownership),
        code=list(scope.code),
        workflow=list(scope.workflow),
        environment=list(scope.environment),
        actor=list(scope.actor),
        time=list(scope.time),
    )
