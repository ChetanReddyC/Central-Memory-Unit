from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory, MemoryRelationType, MemoryStatus, MemoryType
from .promotion import review_promotion
from .usage import MemoryUseReceipt, use_summary


@dataclass
class GravityPull:
    name: str
    score: float
    reason: str

    def render(self) -> str:
        return f"  - {self.name}: {self.score:+.2f} - {self.reason}"


@dataclass
class GravityCard:
    memory_id: str
    title: str
    memory_type: str
    status: str
    center: str
    total_score: float
    pulls: list[GravityPull] = field(default_factory=list)
    pressures: list[str] = field(default_factory=list)
    next_action: str = ""

    def render(self) -> str:
        lines = [
            f"- {self.memory_id} [{self.memory_type}/{self.status}] {self.title}",
            f"  Center: {self.center}",
            f"  Gravity Score: {self.total_score:.2f}",
            "  Pulls:",
        ]
        if self.pulls:
            lines.extend(pull.render() for pull in self.pulls)
        else:
            lines.append("  - none: +0.00 - no placement signals found")
        lines.append(f"  Pressures: {format_list(self.pressures)}")
        lines.append(f"  Next: {self.next_action}")
        return "\n".join(lines)


@dataclass
class GravityReport:
    cards: list[GravityCard]
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Memory Gravity",
            "Mode: read-only placement/settling proof; no memories or receipts are mutated.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Memories Reviewed: {len(self.cards)}",
                f"- Ready To Settle: {sum(1 for card in self.cards if 'settle' in card.next_action)}",
                f"- Promotion Pressure: {sum(1 for card in self.cards if any('promotion' in pressure for pressure in card.pressures))}",
                f"- Merge/Split Pressure: {sum(1 for card in self.cards if any(pressure in {'merge pressure', 'split pressure'} for pressure in card.pressures))}",
                f"- Decay/Review Pressure: {sum(1 for card in self.cards if any(pressure in {'decay pressure', 'governance review pressure'} for pressure in card.pressures))}",
                "",
                "Gravity Cards:",
            ]
        )
        if not self.cards:
            lines.append("- None")
        else:
            lines.extend(card.render() for card in self.cards)
        lines.extend(
            [
                "",
                "Proof Meaning: this report makes memory placement pressure inspectable across scope, relationships, liability, evidence, use receipts, and lifecycle gates before CMU mutates trust or scope.",
            ]
        )
        return "\n".join(lines)


def gravity_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
) -> GravityReport:
    memory_by_id = {memory.id: memory for memory in memories}
    filtered = [memory for memory in memories if not memory_id or memory.id == memory_id]
    cards = [
        gravity_card(memory, memories, receipts, memory_by_id=memory_by_id)
        for memory in sorted(filtered, key=lambda item: (-gravity_score(item, memories, receipts, memory_by_id), item.title))
    ]
    return GravityReport(cards=cards, memory_filter=memory_id)


def gravity_card(
    memory: Memory,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_by_id: dict[str, Memory],
) -> GravityCard:
    pulls = gravity_pulls(memory, memories, receipts, memory_by_id=memory_by_id)
    pressures = gravity_pressures(memory, memories, receipts, pulls=pulls)
    return GravityCard(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        status=memory.status.value,
        center=center_of_gravity(memory),
        total_score=round(sum(pull.score for pull in pulls), 2),
        pulls=pulls,
        pressures=pressures,
        next_action=gravity_next_action(memory, pressures),
    )


def gravity_pulls(
    memory: Memory,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_by_id: dict[str, Memory],
) -> list[GravityPull]:
    pulls: list[GravityPull] = []
    scope_axes = scoped_axes(memory)
    if scope_axes:
        pulls.append(
            GravityPull(
                name="scope",
                score=round(0.45 * len(scope_axes), 2),
                reason=f"{len(scope_axes)} scoped axes: {format_list(scope_axes)}",
            )
        )
    else:
        pulls.append(GravityPull(name="scope gap", score=-0.8, reason="memory has no scoped axes, so placement is weak"))

    outgoing = [relationship for relationship in memory.relationships if relationship.target_id in memory_by_id]
    incoming = incoming_relationships(memory, memories)
    relation_count = len(outgoing) + len(incoming)
    if relation_count:
        relation_labels = relationship_labels(memory, outgoing, incoming, memory_by_id)
        pulls.append(
            GravityPull(
                name="graph",
                score=round(0.5 * relation_count, 2),
                reason=f"{relation_count} relationship signal(s): {format_list(relation_labels[:4])}",
            )
        )

    evidence_count = len(memory.evidence)
    if evidence_count:
        pulls.append(
            GravityPull(
                name="evidence",
                score=round(min(1.2, 0.25 * evidence_count), 2),
                reason=f"{evidence_count} evidence item(s) support placement",
            )
        )

    use = use_summary(receipts, memory.id)
    if use.total:
        score = 0.35 * use.committed + 0.15 * use.checkpoints - 0.35 * use.reverted - 0.12 * use.low_confidence - 0.1 * use.mixed
        pulls.append(
            GravityPull(
                name="use evidence",
                score=round(score, 2),
                reason=f"{use.committed} committed, {use.checkpoints} checkpoints, {use.reverted} reverted, {use.low_confidence} low-confidence, {use.mixed} mixed",
            )
        )

    liability_score = 0.25 * memory.liability_score
    pulls.append(
        GravityPull(
            name="liability",
            score=round(liability_score, 2),
            reason=f"future cost of forgetting is {memory.liability_score}/5",
        )
    )

    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
        authority_score = 0.8 if memory.approved_by else -0.7
        reason = f"approved by {memory.approved_by}" if memory.approved_by else "stable memory lacks explicit authority"
        pulls.append(GravityPull(name="authority", score=authority_score, reason=reason))

    return pulls


def gravity_pressures(
    memory: Memory,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    pulls: list[GravityPull],
) -> list[str]:
    pressures: list[str] = []
    use = use_summary(receipts, memory.id)
    score = sum(pull.score for pull in pulls)
    if memory.type == MemoryType.CANDIDATE:
        try:
            review = review_promotion(memories, memory.id, MemoryType.SITUATION)
            if review.gate_passed:
                pressures.append("promotion pressure")
            else:
                pressures.append("draft-quality pressure")
        except KeyError:
            pressures.append("draft-quality pressure")
    if memory.type == MemoryType.SITUATION:
        stable_ready = []
        if review_promotion(memories, memory.id, MemoryType.PRACTICE).gate_passed:
            stable_ready.append("practice")
        if review_promotion(memories, memory.id, MemoryType.ANCHOR).gate_passed:
            stable_ready.append("anchor")
        if stable_ready:
            pressures.append("stable promotion pressure")
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and not memory.approved_by:
        pressures.append("governance review pressure")
    if use.reverted or use.low_confidence or use.mixed >= 2:
        pressures.append("decay pressure")
    if duplicate_like_count(memory, memories) > 0:
        pressures.append("merge pressure")
    if broad_scope(memory) and use.total and (use.low_confidence or use.reverted or use.mixed):
        pressures.append("split pressure")
    if score >= 3.2 and scoped_axes(memory):
        pressures.append("settle pressure")
    return sorted(set(pressures))


def gravity_next_action(memory: Memory, pressures: list[str]) -> str:
    if "merge pressure" in pressures:
        return "review duplicate/related memories before promotion or broader retrieval use"
    if "split pressure" in pressures:
        return "split or narrow scope before trusting this memory broadly"
    if "governance review pressure" in pressures:
        return "add authority or challenge this stable memory before broader trust"
    if "decay pressure" in pressures:
        return "inspect drag/revert evidence before strengthening or broadening"
    if "promotion pressure" in pressures:
        return "promote candidate to situation if reviewer agrees with the placement"
    if "stable promotion pressure" in pressures:
        return "prepare authority review for practice/anchor promotion"
    if "settle pressure" in pressures:
        return "settle in current scope and keep collecting focused evidence"
    if "draft-quality pressure" in pressures:
        return "add scope, evidence, and reusable worked/failed lesson before promotion"
    if memory.status == MemoryStatus.RETIRED:
        return "keep retired memory as history unless archival/export policy removes it"
    return "keep collecting placement evidence"


def gravity_score(
    memory: Memory,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    memory_by_id: dict[str, Memory],
) -> float:
    return sum(pull.score for pull in gravity_pulls(memory, memories, receipts, memory_by_id=memory_by_id))


def center_of_gravity(memory: Memory) -> str:
    parts = []
    if memory.scope.ownership:
        parts.append(f"owner={format_list(memory.scope.ownership)}")
    if memory.scope.code:
        parts.append(f"code={format_list(memory.scope.code)}")
    if memory.scope.workflow:
        parts.append(f"workflow={format_list(memory.scope.workflow)}")
    if memory.scope.environment:
        parts.append(f"env={format_list(memory.scope.environment)}")
    if memory.scope.actor:
        parts.append(f"actor={format_list(memory.scope.actor)}")
    if memory.scope.time:
        parts.append(f"time={format_list(memory.scope.time)}")
    return "; ".join(parts) if parts else "unsettled: no scope center"


def scoped_axes(memory: Memory) -> list[str]:
    axes = []
    for axis in ["ownership", "code", "workflow", "environment", "actor", "time"]:
        if getattr(memory.scope, axis):
            axes.append(axis)
    return axes


def incoming_relationships(memory: Memory, memories: list[Memory]) -> list[tuple[Memory, MemoryRelationType]]:
    incoming = []
    for source in memories:
        for relationship in source.relationships:
            if relationship.target_id == memory.id:
                incoming.append((source, relationship.type))
    return incoming


def relationship_labels(
    memory: Memory,
    outgoing,
    incoming: list[tuple[Memory, MemoryRelationType]],
    memory_by_id: dict[str, Memory],
) -> list[str]:
    labels = []
    for relationship in outgoing:
        target = memory_by_id.get(relationship.target_id)
        if target is not None:
            labels.append(f"{relationship.type.value}->{target.title}")
    for source, relation_type in incoming:
        labels.append(f"{relation_type.value}<-{source.title}")
    return labels


def broad_scope(memory: Memory) -> bool:
    return any(len(getattr(memory.scope, axis)) > 2 for axis in ["ownership", "code", "workflow", "environment", "actor", "time"])


def duplicate_like_count(memory: Memory, memories: list[Memory]) -> int:
    current_terms = normalized_terms(memory)
    count = 0
    for other in memories:
        if other.id == memory.id:
            continue
        other_terms = normalized_terms(other)
        if len(current_terms & other_terms) >= 6:
            count += 1
    return count


def normalized_terms(memory: Memory) -> set[str]:
    values = [
        memory.title,
        memory.summary,
        memory.use_this_path,
        memory.avoid_this,
        memory.challenge_only_if,
        " ".join(memory.signals),
        " ".join(memory.evidence),
        " ".join(memory.scope.flattened()),
    ]
    stopwords = {"memory", "candidate", "situation", "practice", "should", "when", "with", "this", "that"}
    return {term for term in " ".join(values).lower().replace("/", " ").replace("-", " ").split() if len(term) > 3 and term not in stopwords}


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
