from __future__ import annotations

from dataclasses import dataclass, field

from .challenges import challenged_memory_id, is_challenge_candidate
from .models import Memory, MemoryStatus, MemoryType
from .promotion import review_promotion
from .usage import MemoryUseReceipt, use_review


@dataclass
class LifecycleLine:
    memory_id: str
    title: str
    memory_type: str
    status: str
    stage: str
    gate: str
    evidence: str
    governance: str
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [{self.memory_type}/{self.status}] {self.title}",
                f"  Stage: {self.stage}",
                f"  Gate: {self.gate}",
                f"  Evidence: {self.evidence}",
                f"  Governance: {self.governance}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class LifecycleReport:
    lines: list[LifecycleLine] = field(default_factory=list)
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Core Memory Lifecycle",
            "Mode: read-only structural lifecycle proof; no memories or receipts are mutated.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Candidate: {self.count_stage('candidate')}",
                f"- Situation: {self.count_stage('situation')}",
                f"- Stable: {self.count_stage('stable')}",
                f"- Exception: {self.count_stage('exception')}",
                f"- Challenge Candidate: {self.count_stage('challenge-candidate')}",
                f"- Retired: {sum(1 for line in self.lines if line.status == MemoryStatus.RETIRED.value)}",
                f"- Ready Gates: {sum(1 for line in self.lines if line.gate.startswith('ready'))}",
                f"- Blocked Gates: {sum(1 for line in self.lines if line.gate.startswith('blocked'))}",
                "",
                "Lifecycle Lines:",
            ]
        )
        if not self.lines:
            lines.append("- None")
        else:
            for line in self.lines:
                lines.append(line.render())
        lines.extend(
            [
                "",
                "Proof Meaning: this report connects memory birth, promotion readiness, stable governance, use evidence, challenge state, and retirement visibility in one lifecycle view.",
            ]
        )
        return "\n".join(lines)

    def count_stage(self, prefix: str) -> int:
        return sum(1 for line in self.lines if line.stage.startswith(prefix))


def lifecycle_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
) -> LifecycleReport:
    filtered = [memory for memory in memories if not memory_id or memory.id == memory_id]
    memory_by_id = {memory.id: memory for memory in memories}
    challenge_counts = active_challenge_counts(memories)
    lines = [
        lifecycle_line(memory, memories, receipts, memory_by_id=memory_by_id, challenge_counts=challenge_counts)
        for memory in sorted(filtered, key=lambda item: (stage_sort_key(item), item.title))
    ]
    return LifecycleReport(lines=lines, memory_filter=memory_id)


def lifecycle_line(
    memory: Memory,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_by_id: dict[str, Memory],
    challenge_counts: dict[str, int],
) -> LifecycleLine:
    if memory.status == MemoryStatus.RETIRED:
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="retired",
            gate="closed",
            evidence=evidence_summary(memory, receipts),
            governance="retired memory should not guide new work",
            next_action="keep for history unless import/export or archival policy removes it",
        )
    if is_challenge_candidate(memory):
        stable_id = challenged_memory_id(memory)
        stable = memory_by_id.get(stable_id)
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="challenge-candidate",
            gate="ready: resolve challenge",
            evidence=evidence_summary(memory, receipts),
            governance=f"challenges {stable_id} {stable.title if stable else ''}".strip(),
            next_action="resolve as exception, strengthen, update, retire, or split with approval",
        )
    if memory.type == MemoryType.CANDIDATE:
        review = review_promotion(memories, memory.id, MemoryType.SITUATION)
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="candidate",
            gate="ready: promote to situation" if review.gate_passed else f"blocked: missing {format_list(review.missing)}",
            evidence=evidence_summary(memory, receipts),
            governance="low-friction draft; safe to edit or discard",
            next_action="promote to situation" if review.gate_passed else "add missing reusable scenario evidence/scope/future-use lesson",
        )
    if memory.type == MemoryType.SITUATION:
        practice = review_promotion(memories, memory.id, MemoryType.PRACTICE)
        anchor = review_promotion(memories, memory.id, MemoryType.ANCHOR)
        ready = []
        if practice.gate_passed:
            ready.append("practice")
        if anchor.gate_passed:
            ready.append("anchor")
        if ready:
            gate = f"ready: authority review for {format_list(ready)}"
            next_action = "review stable proposal and promote with approved owner/team"
        else:
            missing = sorted(set(practice.missing + anchor.missing))
            gate = f"blocked: missing stable signals {format_list(missing)}"
            next_action = "collect trust, scope, evidence, liability, and default-path signals"
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="situation",
            gate=gate,
            evidence=evidence_summary(memory, receipts),
            governance="validated reusable scenario; can still evolve through evidence",
            next_action=next_action,
        )
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
        review = use_review(receipts, memories, memory.id)
        card = review.cards[0] if review.cards else None
        active_challenges = challenge_counts.get(memory.id, 0)
        if active_challenges:
            gate = f"ready: {active_challenges} active challenge(s)"
            next_action = "resolve active challenge before broadening trust"
        elif card is not None and card.status == "Strengthen evidence suggested":
            gate = "ready: strengthen evidence"
            next_action = "prepare/apply strengthen with approval if scope is still accurate"
        elif card is not None and card.status == "Review suggested":
            gate = "ready: governance review"
            next_action = "challenge, narrow, split, retire, or strengthen based on review"
        else:
            gate = "collecting: stable use evidence"
            next_action = "keep using within scope and link/resolve receipts"
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="stable",
            gate=gate,
            evidence=card.signal_summary() if card is not None else evidence_summary(memory, receipts),
            governance=stable_governance(memory, active_challenges),
            next_action=next_action,
        )
    if memory.type == MemoryType.EXCEPTION:
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="exception",
            gate="active: scoped exception",
            evidence=evidence_summary(memory, receipts),
            governance="applies only inside its narrow exception scope",
            next_action="surface only when scope matches; revisit if exception repeats broadly",
        )
    if memory.type == MemoryType.ANTI_PATTERN:
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="anti-pattern",
            gate="active: avoidance memory",
            evidence=evidence_summary(memory, receipts),
            governance="warns against tempting paths",
            next_action="connect to scenarios and practices that should avoid this path",
        )
    if memory.type == MemoryType.QUESTION:
        return LifecycleLine(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            stage="question",
            gate="open: unresolved uncertainty",
            evidence=evidence_summary(memory, receipts),
            governance="should stay visible until answered or retired",
            next_action="resolve into situation/practice/exception or retire when answered",
        )
    return LifecycleLine(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        status=memory.status.value,
        stage="unknown",
        gate="blocked: unsupported memory type",
        evidence=evidence_summary(memory, receipts),
        governance="inspect memory manually",
        next_action="repair lifecycle classification",
    )


def active_challenge_counts(memories: list[Memory]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for memory in memories:
        if is_challenge_candidate(memory):
            stable_id = challenged_memory_id(memory)
            if stable_id:
                counts[stable_id] = counts.get(stable_id, 0) + 1
    return counts


def evidence_summary(memory: Memory, receipts: list[MemoryUseReceipt]) -> str:
    relevant = [receipt for receipt in receipts if receipt.memory_id == memory.id]
    linked = [receipt for receipt in relevant if receipt.commit_hash or receipt.outcome_signal]
    if relevant:
        return f"{len(memory.evidence)} memory evidence; {len(relevant)} use receipts; {len(linked)} linked/resolved"
    return f"{len(memory.evidence)} memory evidence; no use receipts"


def stable_governance(memory: Memory, active_challenges: int) -> str:
    approval = f"approved by {memory.approved_by}" if memory.approved_by else "missing explicit approval"
    if active_challenges:
        return f"{approval}; challenge path active"
    return f"{approval}; change only through challenge/split/approved review"


def stage_sort_key(memory: Memory) -> int:
    if memory.status == MemoryStatus.RETIRED:
        return 99
    if is_challenge_candidate(memory):
        return 4
    order = {
        MemoryType.CANDIDATE: 0,
        MemoryType.SITUATION: 1,
        MemoryType.PRACTICE: 2,
        MemoryType.ANCHOR: 2,
        MemoryType.EXCEPTION: 3,
        MemoryType.ANTI_PATTERN: 5,
        MemoryType.QUESTION: 6,
    }
    return order.get(memory.type, 50)


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
