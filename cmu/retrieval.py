from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ActionNote, Memory, MemoryType


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "not",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "when",
    "with",
}

TYPE_WEIGHT = {
    MemoryType.PRACTICE: 1.4,
    MemoryType.ANCHOR: 1.35,
    MemoryType.ANTI_PATTERN: 1.2,
    MemoryType.EXCEPTION: 1.15,
    MemoryType.SITUATION: 1.0,
    MemoryType.QUESTION: 0.9,
    MemoryType.CANDIDATE: 0.75,
}


@dataclass
class PreflightQuery:
    prompt: str
    actor: str = "developer"
    area: str = ""
    files: list[str] | None = None
    risk: str = "medium"

    def text(self) -> str:
        return " ".join([self.prompt, self.area, " ".join(self.files or [])])


@dataclass
class Match:
    memory: Memory
    score: float
    matched_terms: list[str]


def preflight(memories: list[Memory], query: PreflightQuery) -> ActionNote | None:
    matches = rank_memories(memories, query)
    if not matches:
        return None
    best = matches[0]
    if best.score < action_threshold(query.risk):
        return None
    return build_action_note(best)


def action_threshold(risk: str) -> float:
    if risk == "low":
        return 2.4
    if risk == "high":
        return 1.2
    return 1.6


def rank_memories(memories: list[Memory], query: PreflightQuery) -> list[Match]:
    query_terms = tokenize(query.text())
    matches: list[Match] = []
    for memory in memories:
        memory_terms = tokenize(memory_text(memory))
        overlap = sorted(query_terms & memory_terms)
        context_bonus = context_signal_score(memory, query)
        if not overlap and context_bonus <= 0:
            continue
        actor_bonus = actor_signal_score(memory, query) if overlap or context_bonus > 0 else 0.0
        hard_signal_bonus = context_bonus + actor_bonus
        text_score = len(overlap) * 0.5
        liability_bonus = memory.liability_score * 0.2
        confidence_bonus = memory.confidence * 0.3
        score = (text_score + hard_signal_bonus + liability_bonus + confidence_bonus) * TYPE_WEIGHT[memory.type]
        matches.append(Match(memory=memory, score=round(score, 3), matched_terms=overlap[:8]))
    return sorted(matches, key=lambda item: item.score, reverse=True)


def build_action_note(match: Match) -> ActionNote:
    memory = match.memory
    matched = ", ".join(match.matched_terms) if match.matched_terms else "scope/signals"
    evidence = "; ".join(memory.evidence[:2]) if memory.evidence else "Stored CMU memory"
    return ActionNote(
        recognized_situation=memory.title,
        why_it_matches=f"Matched {matched}; liability {memory.liability_score}/5.",
        use_this_path=memory.use_this_path or memory.summary,
        respect_this_memory=f"{memory.type.value} memory in scope: {scope_summary(memory)}.",
        avoid_this=memory.avoid_this or "Do not broaden this memory beyond its stated scope.",
        challenge_only_if=memory.challenge_only_if or "Current constraints differ from the stored scope or evidence.",
        evidence=evidence,
        confidence=f"{round(memory.confidence * 100)}% (score {match.score})",
    )


def memory_text(memory: Memory) -> str:
    parts = [
        memory.title,
        memory.summary,
        memory.use_this_path,
        memory.avoid_this,
        memory.challenge_only_if,
        " ".join(memory.signals),
        " ".join(memory.scope.flattened()),
        " ".join(memory.evidence),
    ]
    return " ".join(parts)


def context_signal_score(memory: Memory, query: PreflightQuery) -> float:
    score = 0.0
    scope = [item.lower() for item in memory.scope.flattened()]
    files = [item.lower() for item in query.files or []]
    if query.area and any(query.area.lower() in item or item in query.area.lower() for item in scope):
        score += 1.0
    for file in files:
        if any(file in item or item in file for item in scope):
            score += 1.2
    return score


def actor_signal_score(memory: Memory, query: PreflightQuery) -> float:
    scope = [item.lower() for item in memory.scope.flattened()]
    if query.actor and any(query.actor.lower() in item or item in query.actor.lower() for item in scope):
        return 0.6
    return 0.0


def scope_summary(memory: Memory) -> str:
    values = memory.scope.flattened()
    return ", ".join(values[:4]) if values else "narrow/local until evidence expands it"


def tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9_./-]+", text.lower()))
    return {token for token in tokens if len(token) > 2 and token not in STOP_WORDS}
