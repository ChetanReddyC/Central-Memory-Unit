from __future__ import annotations

from dataclasses import dataclass, field

from .authority import authority_state, review_expiry_state
from .challenges import challenged_memory_id, is_challenge_candidate
from .models import Memory, MemoryScope, MemoryStatus, MemoryType
from .usage import MemoryUseReceipt, UseReviewCard, use_review


STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}


@dataclass
class GovernanceCard:
    memory_id: str
    title: str
    memory_type: str
    status: str
    authority: str
    authority_review: str
    scope: str
    state: str
    use_evidence: str
    challenge_state: str
    allowed_paths: list[str]
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [{self.memory_type}/{self.status}] {self.title}",
                f"  Authority: {self.authority}",
                f"  Authority Review: {self.authority_review}",
                f"  Scope: {self.scope}",
                f"  State: {self.state}",
                f"  Use Evidence: {self.use_evidence}",
                f"  Challenge State: {self.challenge_state}",
                f"  Allowed Paths: {format_list(self.allowed_paths)}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class GovernanceReport:
    cards: list[GovernanceCard] = field(default_factory=list)
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Practice/Anchor Governance",
            "Mode: read-only stable-memory governance proof; no memories or receipts are mutated.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Stable Memories: {len(self.cards)}",
                f"- Approved: {sum(1 for card in self.cards if card.authority.startswith('approved by '))}",
                f"- Missing Authority: {sum(1 for card in self.cards if card.authority == 'missing explicit approval')}",
                f"- Active Challenges: {sum(1 for card in self.cards if card.challenge_state != 'none')}",
                f"- Strengthen Ready: {sum(1 for card in self.cards if card.state == 'ready: strengthen evidence')}",
                f"- Review Ready: {sum(1 for card in self.cards if card.state == 'ready: governance review')}",
                f"- Following: {sum(1 for card in self.cards if card.state == 'following: approved stable memory')}",
                "",
                "Governance Cards:",
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
                "Proof Meaning: this report connects stable-memory authority, linked use evidence, challenge pressure, and allowed follow-up paths before CMU trusts Practice/Anchor memory more broadly.",
            ]
        )
        return "\n".join(lines)


def governance_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
) -> GovernanceReport:
    stable = [
        memory
        for memory in memories
        if memory.type in STABLE_TYPES and memory.status == MemoryStatus.ACTIVE and (not memory_id or memory.id == memory_id)
    ]
    challenges_by_stable = active_challenges_by_stable(memories)
    cards = [
        governance_card(memory, receipts, challenges_by_stable.get(memory.id, []))
        for memory in sorted(stable, key=lambda item: (item.type.value, item.title))
    ]
    return GovernanceReport(cards=cards, memory_filter=memory_id)


def governance_card(
    memory: Memory,
    receipts: list[MemoryUseReceipt],
    active_challenges: list[Memory],
) -> GovernanceCard:
    review = use_review(receipts, [memory], memory.id)
    use_card = review.cards[0] if review.cards else None
    authority = f"approved by {memory.approved_by}" if memory.approved_by else "missing explicit approval"
    state, next_action = governance_state(memory, use_card, active_challenges)
    return GovernanceCard(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        status=memory.status.value,
        authority=authority,
        authority_review=review_expiry_state(memory),
        scope=format_scope(memory.scope),
        state=state,
        use_evidence=use_card.signal_summary() if use_card is not None else "no use-review card",
        challenge_state=format_challenge_state(active_challenges),
        allowed_paths=allowed_governance_paths(memory, active_challenges),
        next_action=next_action,
    )


def governance_state(
    memory: Memory,
    use_card: UseReviewCard | None,
    active_challenges: list[Memory],
) -> tuple[str, str]:
    if active_challenges:
        return "blocked: active challenge", "resolve active challenge before broadening trust or treating this stable memory as fully settled"
    authority_status, authority_next = authority_state(memory)
    if authority_status in {"missing authority", "review expired", "permission blocked"}:
        return f"blocked: {authority_status}", authority_next
    if use_card is not None and use_card.status == "Strengthen evidence suggested":
        return "ready: strengthen evidence", "prepare/apply approved strengthen evidence if the current scope is still accurate"
    if use_card is not None and use_card.status == "Review suggested":
        return "ready: governance review", "challenge, narrow, split, retire, or strengthen based on the review evidence"
    return "following: approved stable memory", "follow within scope and keep collecting linked use evidence"


def active_challenges_by_stable(memories: list[Memory]) -> dict[str, list[Memory]]:
    by_stable: dict[str, list[Memory]] = {}
    for memory in memories:
        if is_challenge_candidate(memory):
            stable_id = challenged_memory_id(memory)
            if stable_id:
                by_stable.setdefault(stable_id, []).append(memory)
    return by_stable


def allowed_governance_paths(memory: Memory, active_challenges: list[Memory]) -> list[str]:
    if active_challenges:
        return ["resolve exception", "resolve strengthen", "resolve update", "resolve retire", "resolve split"]
    paths = ["follow within scope", "strengthen", "challenge", "scope-review"]
    if memory.type in STABLE_TYPES:
        paths.extend(["split", "retire"])
    return paths


def format_challenge_state(active_challenges: list[Memory]) -> str:
    if not active_challenges:
        return "none"
    items = [f"{memory.id} {memory.title}" for memory in sorted(active_challenges, key=lambda item: item.title)]
    return f"{len(active_challenges)} active challenge(s): {format_list(items)}"


def format_scope(scope: MemoryScope) -> str:
    parts = []
    if scope.ownership:
        parts.append(f"ownership={format_list(scope.ownership)}")
    if scope.code:
        parts.append(f"code={format_list(scope.code)}")
    if scope.workflow:
        parts.append(f"workflow={format_list(scope.workflow)}")
    if scope.environment:
        parts.append(f"environment={format_list(scope.environment)}")
    if scope.actor:
        parts.append(f"actor={format_list(scope.actor)}")
    if scope.time:
        parts.append(f"time={format_list(scope.time)}")
    return "; ".join(parts) if parts else "unscoped"


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
