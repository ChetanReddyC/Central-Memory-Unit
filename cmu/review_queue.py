from __future__ import annotations

from dataclasses import dataclass, field

from .challenges import challenged_memory_id, is_challenge_candidate
from .governance import governance_report
from .models import Memory, MemoryStatus, MemoryType
from .promotion import review_promotion
from .quality import quality_report
from .team_directory import TeamScopeRecord, coverage_for_record
from .usage import MemoryUseReceipt


REVIEW_QUEUE_VERSION = "cmu-review-queue/v1"


@dataclass(frozen=True)
class ReviewQueueCard:
    priority: str
    category: str
    memory_id: str
    title: str
    reason: str
    command: str
    evidence: str = ""

    def render(self) -> str:
        lines = [
            f"- [{self.priority}] {self.category}: {self.memory_id} {self.title}",
            f"  Reason: {self.reason}",
            f"  Command: {self.command}",
        ]
        if self.evidence:
            lines.append(f"  Evidence: {self.evidence}")
        return "\n".join(lines)


@dataclass
class ReviewQueueReport:
    cards: list[ReviewQueueCard] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Review Queue",
            f"Version: {REVIEW_QUEUE_VERSION}",
            "Mode: compact human approval/review queue; no memories or receipts are mutated.",
            "",
            "Summary:",
            f"- Total Cards: {len(self.cards)}",
            f"- P0: {sum(1 for card in self.cards if card.priority == 'P0')}",
            f"- P1: {sum(1 for card in self.cards if card.priority == 'P1')}",
            f"- P2: {sum(1 for card in self.cards if card.priority == 'P2')}",
            "",
            "Review Cards:",
        ]
        if not self.cards:
            lines.append("- None")
        else:
            lines.extend(card.render() for card in self.cards)
        lines.extend(
            [
                "",
                "Proof Meaning: this queue gathers promotion, stable authority, team-scope coverage, challenge, usefulness, and decay review moments into compact cards while preserving the existing explicit approval commands.",
            ]
        )
        return "\n".join(lines)


def review_queue(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    team_scopes: list[TeamScopeRecord] | None = None,
) -> ReviewQueueReport:
    cards: list[ReviewQueueCard] = []
    active = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE]
    cards.extend(candidate_promotion_cards(active))
    cards.extend(stable_proposal_cards(active))
    cards.extend(challenge_resolution_cards(active))
    cards.extend(team_scope_cards(active, team_scopes or []))
    cards.extend(governance_cards(active, receipts))
    cards.extend(quality_cards(active, receipts))
    return ReviewQueueReport(cards=sorted(cards, key=card_sort_key))


def candidate_promotion_cards(memories: list[Memory]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    for memory in memories:
        if memory.type != MemoryType.CANDIDATE or is_challenge_candidate(memory):
            continue
        review = review_promotion(memories, memory.id, MemoryType.SITUATION)
        if review.gate_passed:
            cards.append(
                ReviewQueueCard(
                    priority="P1",
                    category="candidate-promotion",
                    memory_id=memory.id,
                    title=memory.title,
                    reason="Candidate passes the Situation promotion gate.",
                    command=f"cmu promote {memory.id}",
                    evidence=f"scope={format_count(memory.scope.flattened())}; evidence={format_count(memory.evidence)}",
                )
            )
    return cards


def stable_proposal_cards(memories: list[Memory]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    for memory in memories:
        if memory.type != MemoryType.SITUATION:
            continue
        ready: list[str] = []
        for target in [MemoryType.PRACTICE, MemoryType.ANCHOR]:
            review = review_promotion(memories, memory.id, target)
            if review.gate_passed:
                ready.append(target.value)
        for target in ready:
            cards.append(
                ReviewQueueCard(
                    priority="P1",
                    category=f"{target}-approval",
                    memory_id=memory.id,
                    title=memory.title,
                    reason=f"Situation is ready for {target} authority review.",
                    command=f"cmu promote {memory.id} --to {target} --approved-by <owner-or-team>",
                    evidence=f"liability={memory.liability_score}/5; confidence={memory.confidence:.2f}",
                )
            )
    return cards


def challenge_resolution_cards(memories: list[Memory]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    for memory in memories:
        if not is_challenge_candidate(memory):
            continue
        stable_id = challenged_memory_id(memory) or "<stable-memory-id>"
        cards.append(
            ReviewQueueCard(
                priority="P0",
                category="challenge-resolution",
                memory_id=memory.id,
                title=memory.title,
                reason=f"Active challenge blocks stable-memory trust for {stable_id}.",
                command=f"cmu resolve-challenge {memory.id} --outcome <exception|strengthen|update|retire|split> --approved-by <owner-or-team> --evidence <evidence>",
                evidence=memory.summary,
            )
        )
    return cards


def team_scope_cards(memories: list[Memory], records: list[TeamScopeRecord]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    for record in records:
        coverage = coverage_for_record(record, memories)
        title = f"{record.repo}/{record.team}"
        if not coverage.matched_memory_ids:
            cards.append(
                ReviewQueueCard(
                    priority="P1",
                    category="team-scope-coverage",
                    memory_id=record.id,
                    title=title,
                    reason="Team scope has no active matching memory; add or curate scoped memory before broad reuse.",
                    command="cmu team-scope",
                    evidence=f"{record.render_summary()}; missing_metadata={format_missing(coverage.missing_axes)}",
                )
            )
        elif coverage.missing_axes:
            cards.append(
                ReviewQueueCard(
                    priority="P2",
                    category="team-scope-metadata",
                    memory_id=record.id,
                    title=title,
                    reason="Team scope boundary is covered by memory but lacks complete owner/scope/authority/consequence metadata.",
                    command="cmu team-scope",
                    evidence=f"matched={', '.join(coverage.matched_memory_ids)}; missing_metadata={format_missing(coverage.missing_axes)}",
                )
            )
    return cards


def governance_cards(memories: list[Memory], receipts: list[MemoryUseReceipt]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    report = governance_report(memories, receipts)
    for card in report.cards:
        if card.state.startswith("blocked: missing authority") or card.authority == "missing explicit approval":
            cards.append(
                ReviewQueueCard(
                    priority="P0",
                    category="authority-approval",
                    memory_id=card.memory_id,
                    title=card.title,
                    reason=card.next_action,
                    command=f"cmu authority-set {card.memory_id} --approved-by <owner-or-team> --owner <owner-or-team> --approver-role owner --consequence high",
                    evidence=f"authority={card.authority}; review={card.authority_review}",
                )
            )
        elif card.state == "ready: strengthen evidence":
            cards.append(
                ReviewQueueCard(
                    priority="P1",
                    category="strengthen-approval",
                    memory_id=card.memory_id,
                    title=card.title,
                    reason=card.next_action,
                    command=f"cmu use-review {card.memory_id} --prepare strengthen --apply --approved-by <owner-or-team>",
                    evidence=card.use_evidence,
                )
            )
        elif card.state == "ready: governance review":
            cards.append(
                ReviewQueueCard(
                    priority="P1",
                    category="governance-review",
                    memory_id=card.memory_id,
                    title=card.title,
                    reason=card.next_action,
                    command=f"cmu use-review {card.memory_id} --prepare <challenge|scope-review|strengthen>",
                    evidence=card.use_evidence,
                )
            )
    return cards


def quality_cards(memories: list[Memory], receipts: list[MemoryUseReceipt]) -> list[ReviewQueueCard]:
    cards: list[ReviewQueueCard] = []
    for card in quality_report(memories, receipts).cards:
        if card.state == "decay-ready":
            cards.append(
                ReviewQueueCard(
                    priority="P0",
                    category="decay-review",
                    memory_id=card.memory_id,
                    title=card.title,
                    reason=card.recommended_action,
                    command=f"cmu decay-apply {card.memory_id} --action <weaken|demote|retire> --reason <evidence-backed-reason>",
                    evidence=f"quality={card.score:.2f}/10; signals={', '.join(card.signals)}",
                )
            )
        elif card.state == "review":
            cards.append(
                ReviewQueueCard(
                    priority="P2",
                    category="quality-review",
                    memory_id=card.memory_id,
                    title=card.title,
                    reason=card.recommended_action,
                    command=f"cmu quality --memory {card.memory_id}",
                    evidence=f"quality={card.score:.2f}/10; signals={', '.join(card.signals)}",
                )
            )
    return cards


def card_sort_key(card: ReviewQueueCard) -> tuple[int, str, str]:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(card.priority, 9)
    return priority_rank, card.category, card.title.lower()


def format_count(values: list[str]) -> str:
    return str(len(values))


def format_missing(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
