from __future__ import annotations

from dataclasses import dataclass, field

from .governance import STABLE_TYPES, active_challenges_by_stable, governance_card
from .models import Memory
from .usage import (
    MemoryUseReceipt,
    UseReviewCard,
    format_source_counts,
    use_review,
    usage_adjustment,
)


@dataclass
class UsefulnessAnalyticsCard:
    memory_id: str
    title: str
    memory_type: str
    total_uses: int
    linked_uses: int
    unlinked_uses: int
    strong_committed: int
    drag_signals: int
    resolved_without_commit: int
    retrieval_adjustment: float
    verdict: str
    evidence_readiness: str
    governance_state: str
    source_summary: str
    semantic_summary: str
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [{self.memory_type}] {self.title}",
                f"  Verdict: {self.verdict}",
                f"  Evidence: {self.linked_uses}/{self.total_uses} linked; {self.unlinked_uses} unresolved; {self.strong_committed} strong; {self.drag_signals} drag; {self.resolved_without_commit} resolved-without-commit",
                f"  Retrieval Adjustment: {self.retrieval_adjustment:+.2f}",
                f"  Evidence Readiness: {self.evidence_readiness}",
                f"  Governance: {self.governance_state}",
                f"  Sources: {self.source_summary}",
                f"  Semantic: {self.semantic_summary}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class UsefulnessAnalyticsReport:
    cards: list[UsefulnessAnalyticsCard] = field(default_factory=list)
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Usefulness and Drag Analytics",
            "Mode: read-only usefulness/drag proof; no memories or receipts are mutated.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Memories With Evidence: {len(self.cards)}",
                f"- Total Uses: {sum(card.total_uses for card in self.cards)}",
                f"- Linked Uses: {sum(card.linked_uses for card in self.cards)}",
                f"- Unresolved Uses: {sum(card.unlinked_uses for card in self.cards)}",
                f"- Strong Uses: {sum(card.strong_committed for card in self.cards)}",
                f"- Drag Signals: {sum(card.drag_signals for card in self.cards)}",
                f"- Useful: {sum(1 for card in self.cards if card.verdict == 'useful')}",
                f"- Mixed: {sum(1 for card in self.cards if card.verdict == 'mixed')}",
                f"- Drag: {sum(1 for card in self.cards if card.verdict == 'drag')}",
                f"- Evidence Gaps: {sum(1 for card in self.cards if card.verdict == 'evidence-gap')}",
                "",
                "Analytics Cards:",
            ]
        )
        if not self.cards:
            lines.append("- None")
        else:
            for card in self.cards:
                lines.append(card.render())
        lines.extend(
            [
                "",
                "Proof Meaning: this report judges whether CMU memory use is helping, dragging, mixed, or still unproven by connecting receipts, linked checkpoint evidence, retrieval adjustment, semantic provenance, and stable-memory governance state.",
            ]
        )
        return "\n".join(lines)


def usefulness_analytics_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
) -> UsefulnessAnalyticsReport:
    memory_by_id = {memory.id: memory for memory in memories}
    receipts_by_memory: dict[str, list[MemoryUseReceipt]] = {}
    for receipt in receipts:
        receipts_by_memory.setdefault(receipt.memory_id, []).append(receipt)
    memory_ids = sorted(receipts_by_memory)
    if memory_id:
        memory_ids = [memory_id]
    challenges_by_stable = active_challenges_by_stable(memories)
    cards = [
        analytics_card(
            memory_by_id.get(current_id),
            receipts_by_memory.get(current_id, []),
            challenges_by_stable,
            current_id,
        )
        for current_id in memory_ids
        if receipts_by_memory.get(current_id) or memory_id
    ]
    return UsefulnessAnalyticsReport(cards=cards, memory_filter=memory_id)


def analytics_card(
    memory: Memory | None,
    receipts: list[MemoryUseReceipt],
    challenges_by_stable: dict[str, list[Memory]],
    memory_id: str,
) -> UsefulnessAnalyticsCard:
    review = use_review(receipts, [memory] if memory is not None else [], memory_id)
    use_card = review.cards[0] if review.cards else empty_card(memory, memory_id)
    unlinked = [receipt for receipt in receipts if not receipt.commit_hash and not receipt.outcome_signal]
    verdict = usefulness_verdict(use_card, len(unlinked))
    governance_state = governance_summary(memory, receipts, challenges_by_stable)
    return UsefulnessAnalyticsCard(
        memory_id=memory_id,
        title=use_card.memory_title or (memory.title if memory is not None else ""),
        memory_type=memory.type.value if memory is not None else "unknown",
        total_uses=use_card.total_uses,
        linked_uses=use_card.linked_uses,
        unlinked_uses=len(unlinked),
        strong_committed=use_card.strong_committed,
        drag_signals=use_card.drag_signals,
        resolved_without_commit=use_card.resolved_without_commit,
        retrieval_adjustment=usage_adjustment(receipts),
        verdict=verdict,
        evidence_readiness=evidence_readiness(use_card, len(unlinked)),
        governance_state=governance_state,
        source_summary=format_source_counts(use_card.source_counts),
        semantic_summary=semantic_summary(use_card),
        next_action=analytics_next_action(verdict, governance_state, use_card, len(unlinked)),
    )


def usefulness_verdict(card: UseReviewCard, unlinked_uses: int) -> str:
    if card.total_uses == 0:
        return "evidence-gap"
    if card.linked_uses == 0 or unlinked_uses > card.linked_uses:
        return "evidence-gap"
    if card.drag_signals and card.strong_committed:
        return "mixed"
    if card.status == "Review suggested" or card.drag_signals:
        return "drag"
    if card.status == "Strengthen evidence suggested" or card.strong_committed >= 2:
        return "useful"
    return "neutral"


def evidence_readiness(card: UseReviewCard, unlinked_uses: int) -> str:
    if card.total_uses == 0:
        return "no receipts yet"
    if card.linked_uses == 0:
        return "link or resolve receipts before judging"
    if unlinked_uses:
        return "partial evidence; close unresolved receipts before threshold changes"
    return "closed enough for first-pass judgment"


def governance_summary(
    memory: Memory | None,
    receipts: list[MemoryUseReceipt],
    challenges_by_stable: dict[str, list[Memory]],
) -> str:
    if memory is None:
        return "unknown memory; repair receipt linkage"
    if memory.type not in STABLE_TYPES:
        return "not stable memory"
    card = governance_card(memory, receipts, challenges_by_stable.get(memory.id, []))
    return card.state


def analytics_next_action(
    verdict: str,
    governance_state: str,
    card: UseReviewCard,
    unlinked_uses: int,
) -> str:
    if verdict == "evidence-gap":
        return "link or resolve receipts before claiming usefulness or drag"
    if governance_state.startswith("blocked:"):
        return f"resolve governance first; analytics verdict is {verdict} but stable trust is {governance_state}"
    if verdict == "useful":
        return "strengthen evidence, consider promotion readiness, or keep following within scope"
    if verdict == "mixed":
        return "inspect drag receipts before changing thresholds; narrow, challenge, or keep collecting focused evidence"
    if verdict == "drag":
        return "review scope and wording; for stable memory use challenge, split, retire, or approved narrowing"
    if unlinked_uses:
        return "close unresolved receipts and keep collecting evidence"
    return card.suggested_action


def semantic_summary(card: UseReviewCard) -> str:
    modes = format_source_counts(card.semantic_mode_counts)
    matches = format_source_counts(card.semantic_match_counts)
    return f"modes {modes}; matches {matches}"


def empty_card(memory: Memory | None, memory_id: str) -> UseReviewCard:
    review = use_review([], [memory] if memory is not None else [], memory_id)
    return review.cards[0]
