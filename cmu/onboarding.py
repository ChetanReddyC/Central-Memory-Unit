from __future__ import annotations

from dataclasses import dataclass

from .models import Memory
from .retrieval import PreflightQuery, SemanticIndex, action_threshold, rank_memories, scope_summary


@dataclass
class OnboardingSeed:
    where_working: str
    must_not_violate: str
    default_path: str
    trap_to_avoid: str
    call_cmu_again: str
    source_memory_id: str = ""
    confidence: str = ""

    def render(self) -> str:
        lines = [
            "CMU Onboarding Seed",
            f"Where Working: {self.where_working}",
            f"Must Not Violate: {self.must_not_violate}",
            f"Default Path: {self.default_path}",
            f"Trap To Avoid: {self.trap_to_avoid}",
            f"Call CMU Again: {self.call_cmu_again}",
        ]
        if self.source_memory_id:
            lines.append(f"Source Memory: {self.source_memory_id}")
        if self.confidence:
            lines.append(f"Confidence: {self.confidence}")
        return "\n".join(lines)


def build_onboarding_seed(
    memories: list[Memory],
    query: PreflightQuery,
    semantic_index: SemanticIndex | None = None,
) -> OnboardingSeed:
    matches = rank_memories(memories, query, semantic_index=semantic_index)
    actionable = [match for match in matches if match.score >= action_threshold(query.risk)]
    if not actionable:
        return fallback_seed(query)
    match = actionable[0]
    memory = match.memory
    return OnboardingSeed(
        where_working=compact(scope_summary(memory), fallback=query_area_summary(query)),
        must_not_violate=compact(memory.challenge_only_if or "Stay inside the stated scope unless evidence shows it changed."),
        default_path=compact(memory.use_this_path or memory.summary),
        trap_to_avoid=compact(memory.avoid_this or "Do not generalize this memory beyond its evidence."),
        call_cmu_again=call_again_guidance(query, matched=True),
        source_memory_id=memory.id,
        confidence=f"{round(memory.confidence * 100)}% (score {match.score})",
    )


def fallback_seed(query: PreflightQuery) -> OnboardingSeed:
    return OnboardingSeed(
        where_working=query_area_summary(query),
        must_not_violate="Do not invent project rules without a matching memory or local evidence.",
        default_path="Inspect the local code and existing patterns before changing behavior.",
        trap_to_avoid="Do not turn a low-risk local task into a broad memory or practice.",
        call_cmu_again=call_again_guidance(query, matched=False),
        confidence="no matching memory",
    )


def query_area_summary(query: PreflightQuery) -> str:
    parts: list[str] = []
    if query.area:
        parts.append(query.area)
    parts.extend(query.files or [])
    parts.extend(query.workflow or [])
    parts.extend(query.environment or [])
    return compact(", ".join(parts), fallback="task-local scope")


def call_again_guidance(query: PreflightQuery, *, matched: bool) -> str:
    if query.risk == "high":
        return "Call CMU again at decision points, repeated errors, or before hard-to-rollback changes."
    if matched:
        return "Call CMU again if the task leaves this scope or the memory stops fitting."
    return "Call CMU again if uncertainty, repeated errors, or shared-contract risk appears."


def compact(text: str, *, fallback: str = "narrow task scope", limit: int = 180) -> str:
    clean = " ".join(text.split()).strip() or fallback
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."
