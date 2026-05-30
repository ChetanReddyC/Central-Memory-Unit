from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from .retrieval import Match, PreflightQuery, SemanticIndex, action_threshold, rank_memories, scope_summary


@dataclass
class QuestionCard:
    memory_id: str
    title: str
    status: str
    scope: str
    owner: str
    question: str
    investigation_path: str
    avoid_assuming: str
    resolution_condition: str
    retrieval: str
    relationships: str
    evidence: str
    state: str
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [question/{self.status}] {self.title}",
                f"  Scope: {self.scope}",
                f"  Owner: {self.owner}",
                f"  Question: {self.question}",
                f"  Investigation Path: {self.investigation_path}",
                f"  Avoid Assuming: {self.avoid_assuming}",
                f"  Resolution Condition: {self.resolution_condition}",
                f"  Retrieval: {self.retrieval}",
                f"  Relationships: {self.relationships}",
                f"  Evidence: {self.evidence}",
                f"  State: {self.state}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class QuestionReport:
    cards: list[QuestionCard] = field(default_factory=list)
    prompt: str = ""
    memory_filter: str = ""

    def render(self) -> str:
        lines = [
            "CMU Question Workflow",
            "Mode: read-only question tracking proof; no memories are mutated.",
        ]
        if self.prompt:
            lines.append(f"Prompt: {self.prompt}")
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Questions: {len(self.cards)}",
                f"- Active Questions: {sum(1 for card in self.cards if card.state == 'active question')}",
                f"- Ownership Gaps: {sum(1 for card in self.cards if card.state == 'ownership gap')}",
                f"- Evidence Gaps: {sum(1 for card in self.cards if card.state == 'evidence gap')}",
                f"- Relationship Gaps: {sum(1 for card in self.cards if card.state == 'relationship gap')}",
                f"- Retired: {sum(1 for card in self.cards if card.state == 'retired')}",
                "",
                "Creation Path:",
                "- Use `cmu add --type question` with uncertainty summary, investigation path, premature-assumption warning, scope, ownership, evidence, and resolution condition.",
                "- Relate questions to Situations, Practices, Exceptions, or other evidence-bearing memories with `cmu relate`.",
                "- Resolve answered questions with `cmu resolve-question <id> --outcome retire|situation|exception --answer <answer> --resolved-by <owner> --evidence <evidence>`.",
                "",
                "Question Cards:",
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
                "Proof Meaning: this report keeps costly unresolved uncertainty visible by connecting question surfacing, ownership, evidence, relationships, and explicit answer/retirement paths.",
            ]
        )
        return "\n".join(lines)


@dataclass
class ResolveQuestionRequest:
    question_id: str
    outcome: str
    answer: str
    resolved_by: str
    evidence: list[str] = field(default_factory=list)
    title: str = ""
    use_path: str = ""
    avoid: str = ""
    review_if: str = ""


@dataclass
class ResolveQuestionDecision:
    applied: bool
    reason: str
    question: Memory | None = None
    outcome_memory: Memory | None = None

    def render(self) -> str:
        if not self.applied or self.question is None:
            return "\n".join(["CMU Question Resolution Not Applied", f"Reason: {self.reason}"])
        lines = [
            "CMU Question Resolution Applied",
            f"Question: {self.question.id} [{self.question.status.value}] {self.question.title}",
            f"Reason: {self.reason}",
        ]
        if self.outcome_memory is not None:
            lines.append(
                f"Outcome Memory: {self.outcome_memory.id} [{self.outcome_memory.type.value}] {self.outcome_memory.title}"
            )
        return "\n".join(lines)


def question_report(
    memories: list[Memory],
    *,
    query: PreflightQuery | None = None,
    memory_id: str = "",
    include_retired: bool = False,
    semantic_index: SemanticIndex | None = None,
) -> QuestionReport:
    questions = [
        memory
        for memory in memories
        if memory.type == MemoryType.QUESTION
        and (include_retired or memory.status == MemoryStatus.ACTIVE)
        and (not memory_id or memory.id == memory_id)
    ]
    match_by_id = question_matches(questions, query, semantic_index)
    memory_by_id = {memory.id: memory for memory in memories}
    incoming = incoming_relationships(memories)
    cards = [
        question_card(
            memory,
            memory_by_id,
            incoming.get(memory.id, []),
            match_by_id.get(memory.id),
            query,
        )
        for memory in sorted(questions, key=lambda item: question_sort_key(item, match_by_id.get(item.id), query))
    ]
    return QuestionReport(
        cards=cards,
        prompt=query.prompt if query is not None else "",
        memory_filter=memory_id,
    )


def question_card(
    memory: Memory,
    memory_by_id: dict[str, Memory],
    incoming: list[tuple[Memory, MemoryRelationType]],
    match: Match | None,
    query: PreflightQuery | None,
) -> QuestionCard:
    relationships = relationship_summary(memory, memory_by_id, incoming)
    state = question_state(memory, relationships, match, query)
    return QuestionCard(
        memory_id=memory.id,
        title=memory.title,
        status=memory.status.value,
        scope=scope_summary(memory),
        owner=", ".join(memory.scope.ownership) if memory.scope.ownership else "missing explicit owner",
        question=memory.summary or memory.title,
        investigation_path=memory.use_this_path or "Investigation path not recorded.",
        avoid_assuming=memory.avoid_this or "Premature-assumption warning not recorded.",
        resolution_condition=memory.challenge_only_if or "Resolution condition not recorded.",
        retrieval=retrieval_summary(match, query),
        relationships=relationships,
        evidence=f"{len(memory.evidence)} evidence item(s)",
        state=state,
        next_action=question_next_action(state, memory.id),
    )


def resolve_question(memories: list[Memory], request: ResolveQuestionRequest) -> ResolveQuestionDecision:
    question = find_memory(memories, request.question_id)
    if question.type != MemoryType.QUESTION or question.status != MemoryStatus.ACTIVE:
        return ResolveQuestionDecision(
            applied=False,
            reason="memory is not an active Question",
            question=question,
        )
    if request.outcome not in {"retire", "situation", "exception"}:
        return ResolveQuestionDecision(
            applied=False,
            reason=f"unsupported question outcome: {request.outcome}",
            question=question,
        )
    missing = missing_resolution_fields(request)
    if missing:
        return ResolveQuestionDecision(
            applied=False,
            reason=f"question resolution requires: {', '.join(missing)}",
            question=question,
        )
    outcome_memory = create_outcome_memory(question, request) if request.outcome != "retire" else None
    question.status = MemoryStatus.RETIRED
    question.evidence.extend(
        [
            *request.evidence,
            f"Question resolved as {request.outcome} by {request.resolved_by.strip()}",
            f"Answer: {request.answer.strip()}",
        ]
    )
    if outcome_memory is not None:
        question.evidence.append(f"Resolved into memory: {outcome_memory.id}")
    return ResolveQuestionDecision(
        applied=True,
        reason=f"Question answered and retired as {request.outcome}.",
        question=question,
        outcome_memory=outcome_memory,
    )


def create_outcome_memory(question: Memory, request: ResolveQuestionRequest) -> Memory:
    outcome_type = MemoryType.SITUATION if request.outcome == "situation" else MemoryType.EXCEPTION
    return Memory.create(
        type=outcome_type,
        title=request.title.strip() or f"Resolved: {question.title}",
        summary=request.answer,
        signals=list(question.signals),
        scope=copy_scope(question.scope),
        evidence=[
            *question.evidence,
            *request.evidence,
            f"Resolved from Question Memory: {question.id}",
            f"Question resolved by: {request.resolved_by.strip()}",
        ],
        use_this_path=request.use_path.strip() or question.use_this_path,
        avoid_this=request.avoid.strip() or question.avoid_this,
        challenge_only_if=request.review_if.strip() or question.challenge_only_if,
        relationships=[
            MemoryRelationship(
                type=MemoryRelationType.DERIVED_FROM,
                target_id=question.id,
                reason="Resolved answer derived from Question Memory.",
            )
        ],
        liability_score=question.liability_score,
        confidence=max(question.confidence, 0.7),
        approved_by=request.resolved_by,
    )


def missing_resolution_fields(request: ResolveQuestionRequest) -> list[str]:
    missing = []
    if not request.answer.strip():
        missing.append("answer")
    if not request.resolved_by.strip():
        missing.append("resolved_by")
    if not request.evidence:
        missing.append("evidence")
    return missing


def question_matches(
    questions: list[Memory],
    query: PreflightQuery | None,
    semantic_index: SemanticIndex | None,
) -> dict[str, Match]:
    if query is None:
        return {}
    return {match.memory.id: match for match in rank_memories(questions, query, semantic_index=semantic_index)}


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
        return "not evaluated; provide a task prompt to test question relevance"
    if match is None:
        return "not matched for this task"
    threshold = action_threshold(query.risk)
    status = "surface question" if match.score >= threshold else "below threshold"
    terms = ", ".join(match.matched_terms) if match.matched_terms else "scope/signals"
    return f"{status}; score {match.score:.3f} vs threshold {threshold:.3f}; matched {terms}"


def question_state(
    memory: Memory,
    relationships: str,
    match: Match | None,
    query: PreflightQuery | None,
) -> str:
    if memory.status == MemoryStatus.RETIRED:
        return "retired"
    if not memory.scope.ownership:
        return "ownership gap"
    if not memory.evidence:
        return "evidence gap"
    if query is not None and match is not None and match.score >= action_threshold(query.risk):
        return "active question"
    if relationships == "none":
        return "relationship gap"
    return "watch"


def question_next_action(state: str, memory_id: str) -> str:
    if state == "active question":
        return f"surface this uncertainty before acting; resolve with `cmu resolve-question {memory_id} ...` when evidence answers it"
    if state == "ownership gap":
        return "add ownership scope so the uncertainty has a responsible reviewer"
    if state == "evidence gap":
        return "add evidence showing why forgetting this uncertainty is costly"
    if state == "relationship gap":
        return "relate this question to the Situation, Practice, or Exception it may affect"
    if state == "retired":
        return "keep the answered question for history; use its resolved memory for future guidance"
    return "keep visible until evidence answers it or the uncertainty becomes irrelevant"


def question_sort_key(memory: Memory, match: Match | None, query: PreflightQuery | None) -> tuple[int, str]:
    if query is not None and match is not None and match.score >= action_threshold(query.risk):
        return (0, memory.title)
    return (1, memory.title)


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def copy_scope(scope: MemoryScope) -> MemoryScope:
    return MemoryScope(
        ownership=list(scope.ownership),
        code=list(scope.code),
        workflow=list(scope.workflow),
        environment=list(scope.environment),
        actor=list(scope.actor),
        time=list(scope.time),
    )
