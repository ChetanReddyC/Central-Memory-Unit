from __future__ import annotations

from dataclasses import dataclass, field

from .analytics import analytics_card
from .governance import active_challenges_by_stable
from .models import Memory, MemoryRelationType, MemoryStatus, MemoryType
from .retrieval import Match, PreflightQuery, SemanticIndex, action_threshold, rank_memories, scope_summary
from .usage import MemoryUseReceipt


@dataclass
class AntiPatternCard:
    memory_id: str
    title: str
    status: str
    scope: str
    trap: str
    avoid: str
    safer_path: str
    review_if: str
    retrieval: str
    relationships: str
    evidence: str
    usefulness: str
    state: str
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [anti-pattern/{self.status}] {self.title}",
                f"  Scope: {self.scope}",
                f"  Trap: {self.trap}",
                f"  Avoid: {self.avoid}",
                f"  Safer Path: {self.safer_path}",
                f"  Review If: {self.review_if}",
                f"  Retrieval: {self.retrieval}",
                f"  Relationships: {self.relationships}",
                f"  Evidence: {self.evidence}",
                f"  Usefulness: {self.usefulness}",
                f"  State: {self.state}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class AntiPatternReport:
    cards: list[AntiPatternCard] = field(default_factory=list)
    prompt: str = ""
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Anti-Pattern Workflow",
            "Mode: read-only anti-pattern proof; no memories or receipts are mutated.",
        ]
        if self.prompt:
            lines.append(f"Prompt: {self.prompt}")
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Anti-Patterns: {len(self.cards)}",
                f"- Active Warnings: {sum(1 for card in self.cards if card.state == 'active warning')}",
                f"- Relationship Gaps: {sum(1 for card in self.cards if card.state == 'relationship gap')}",
                f"- Evidence Gaps: {sum(1 for card in self.cards if card.state == 'evidence gap')}",
                f"- Review Ready: {sum(1 for card in self.cards if card.state == 'review warning')}",
                "",
                "Creation Path:",
                "- Use `cmu add --type anti-pattern` with trap summary, safer path, avoid warning, scope, evidence, and review condition.",
                "- Relate anti-patterns to supporting Situations or challenged Practices with `cmu relate`.",
                "",
                "Anti-Pattern Cards:",
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
                "Proof Meaning: this report makes tempting paths to avoid first-class by connecting anti-pattern retrieval, safer alternatives, scope, evidence, relationships, use receipts, and review pressure.",
            ]
        )
        return "\n".join(lines)


def anti_pattern_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    query: PreflightQuery | None = None,
    memory_id: str = "",
    semantic_index: SemanticIndex | None = None,
) -> AntiPatternReport:
    anti_patterns = [
        memory
        for memory in memories
        if memory.type == MemoryType.ANTI_PATTERN and (not memory_id or memory.id == memory_id)
    ]
    match_by_id = anti_pattern_matches(anti_patterns, query, semantic_index)
    memory_by_id = {memory.id: memory for memory in memories}
    incoming = incoming_relationships(memories)
    challenges = active_challenges_by_stable(memories)
    cards = [
        anti_pattern_card(
            memory,
            receipts,
            memory_by_id,
            incoming.get(memory.id, []),
            match_by_id.get(memory.id),
            query,
            challenges,
        )
        for memory in sorted(anti_patterns, key=lambda item: anti_pattern_sort_key(item, match_by_id.get(item.id), query))
    ]
    return AntiPatternReport(
        cards=cards,
        prompt=query.prompt if query is not None else "",
        memory_filter=memory_id,
    )


def anti_pattern_card(
    memory: Memory,
    receipts: list[MemoryUseReceipt],
    memory_by_id: dict[str, Memory],
    incoming: list[tuple[Memory, MemoryRelationType]],
    match: Match | None,
    query: PreflightQuery | None,
    challenges_by_stable: dict[str, list[Memory]],
) -> AntiPatternCard:
    memory_receipts = [receipt for receipt in receipts if receipt.memory_id == memory.id]
    analytics = analytics_card(memory, memory_receipts, challenges_by_stable, memory.id)
    relationships = relationship_summary(memory, memory_by_id, incoming)
    retrieval = retrieval_summary(match, query)
    state = anti_pattern_state(memory, analytics.verdict, relationships, match, query)
    return AntiPatternCard(
        memory_id=memory.id,
        title=memory.title,
        status=memory.status.value,
        scope=scope_summary(memory),
        trap=memory.summary or memory.title,
        avoid=memory.avoid_this or "Avoidance warning not recorded.",
        safer_path=memory.use_this_path or "Safer replacement path not recorded.",
        review_if=memory.challenge_only_if or "Review when the avoided path becomes safe or context changes.",
        retrieval=retrieval,
        relationships=relationships,
        evidence=f"{len(memory.evidence)} memory evidence; {analytics.linked_uses}/{analytics.total_uses} linked uses; {analytics.unlinked_uses} unresolved",
        usefulness=f"{analytics.verdict}; {analytics.strong_committed} strong, {analytics.drag_signals} drag; adjustment {analytics.retrieval_adjustment:+.2f}",
        state=state,
        next_action=anti_pattern_next_action(state, analytics.verdict),
    )


def anti_pattern_matches(
    anti_patterns: list[Memory],
    query: PreflightQuery | None,
    semantic_index: SemanticIndex | None,
) -> dict[str, Match]:
    if query is None:
        return {}
    matches = rank_memories(anti_patterns, query, semantic_index=semantic_index)
    return {match.memory.id: match for match in matches}


def incoming_relationships(memories: list[Memory]) -> dict[str, list[tuple[Memory, MemoryRelationType]]]:
    incoming: dict[str, list[tuple[Memory, MemoryRelationType]]] = {}
    for source in memories:
        for relationship in source.relationships:
            incoming.setdefault(relationship.target_id, []).append((source, relationship.type))
    return incoming


def relationship_summary(
    memory: Memory,
    memory_by_id: dict[str, Memory],
    incoming: list[tuple[Memory, MemoryRelationType]],
) -> str:
    labels = []
    for relationship in memory.relationships:
        target = memory_by_id.get(relationship.target_id)
        target_label = target.title if target is not None else relationship.target_id
        labels.append(f"{relationship.type.value}->{target_label}")
    for source, relation_type in incoming:
        labels.append(f"{relation_type.value}<-{source.title}")
    return ", ".join(labels) if labels else "none"


def retrieval_summary(match: Match | None, query: PreflightQuery | None) -> str:
    if query is None:
        return "not evaluated; provide a task prompt to test warning fit"
    if match is None:
        return "not matched for this task"
    threshold = action_threshold(query.risk)
    status = "active warning" if match.score >= threshold else "below threshold"
    terms = ", ".join(match.matched_terms) if match.matched_terms else "scope/signals"
    return f"{status}; score {match.score:.3f} vs threshold {threshold:.3f}; matched {terms}"


def anti_pattern_state(
    memory: Memory,
    analytics_verdict: str,
    relationships: str,
    match: Match | None,
    query: PreflightQuery | None,
) -> str:
    if memory.status == MemoryStatus.RETIRED:
        return "retired"
    if query is not None and match is not None and match.score >= action_threshold(query.risk):
        return "active warning"
    if analytics_verdict in {"drag", "mixed"}:
        return "review warning"
    if not memory.evidence:
        return "evidence gap"
    if relationships == "none":
        return "relationship gap"
    return "watch"


def anti_pattern_next_action(state: str, analytics_verdict: str) -> str:
    if state == "active warning":
        return "surface the avoidance warning and follow the safer path before acting"
    if state == "review warning":
        return "inspect use receipts; narrow, retire, or rewrite the warning if it is creating drag"
    if state == "evidence gap":
        return "add concrete failure, incident, or review evidence before relying on this warning"
    if state == "relationship gap":
        return "relate this warning to the Situation, Practice, or Exception it protects"
    if state == "retired":
        return "keep for history; do not surface as current avoidance guidance"
    if analytics_verdict == "useful":
        return "keep warning active and strengthen evidence if repeated useful avoidance continues"
    return "keep collecting evidence and test against relevant task prompts"


def anti_pattern_sort_key(memory: Memory, match: Match | None, query: PreflightQuery | None) -> tuple[int, str]:
    if query is not None and match is not None and match.score >= action_threshold(query.risk):
        return (0, memory.title)
    return (1, memory.title)
