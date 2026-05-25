from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from json import JSONDecodeError
import json
from math import sqrt
from pathlib import Path

from .models import ActionNote, Memory, MemoryRelationType, MemoryRelationship, MemoryType


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

GRAPH_LINK_WEIGHT = {
    MemoryRelationType.RELATED_PRACTICE: 1.1,
    MemoryRelationType.EXCEPTION_TO: 0.95,
    MemoryRelationType.SUPPORTS: 0.8,
    MemoryRelationType.SAME_SITUATION: 0.75,
    MemoryRelationType.DERIVED_FROM: 0.65,
    MemoryRelationType.CHALLENGES: 0.55,
}

RELATION_TARGET_TYPES = {
    MemoryRelationType.RELATED_PRACTICE: {MemoryType.PRACTICE},
    MemoryRelationType.EXCEPTION_TO: {MemoryType.PRACTICE, MemoryType.ANCHOR},
    MemoryRelationType.CHALLENGES: {MemoryType.PRACTICE, MemoryType.ANCHOR},
    MemoryRelationType.DERIVED_FROM: set(MemoryType),
    MemoryRelationType.SAME_SITUATION: set(MemoryType),
    MemoryRelationType.SUPPORTS: set(MemoryType),
}

STABLE_GRAPH_RELATIONS = {
    MemoryRelationType.RELATED_PRACTICE,
    MemoryRelationType.EXCEPTION_TO,
    MemoryRelationType.SUPPORTS,
    MemoryRelationType.SAME_SITUATION,
}

SEMANTIC_PROPOSAL_MIN_SCORE = 0.35


@dataclass(frozen=True)
class SemanticSignal:
    label: str
    score: float = 0.0
    available: bool = False

    def contribution(self) -> float:
        return self.score if self.available else 0.0


class SemanticIndex:
    def score(self, memory: Memory, query: "PreflightQuery") -> SemanticSignal:
        return SemanticSignal(label="unavailable")


class HashingEmbeddingProvider:
    def __init__(self, *, dimensions: int = 64, label: str = "local hashing embeddings") -> None:
        if dimensions < 8:
            raise ValueError("embedding dimensions must be at least 8")
        self.dimensions = dimensions
        self.label = label

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            add_hashed_feature(vector, f"token:{token}", weight=1.0)
            for feature in token_character_features(token):
                add_hashed_feature(vector, feature, weight=0.35)
        return normalize_vector(vector)


class PersistentSemanticIndex(SemanticIndex):
    def __init__(self, path: Path | str, provider: HashingEmbeddingProvider | None = None) -> None:
        self.path = Path(path)
        self.provider = provider or HashingEmbeddingProvider()
        self.vectors: dict[str, list[float]] = {}
        self.fingerprints: dict[str, str] = {}
        self._load()

    @classmethod
    def load_or_build(
        cls,
        path: Path | str,
        memories: list[Memory],
        provider: HashingEmbeddingProvider | None = None,
    ) -> "PersistentSemanticIndex":
        index = cls(path, provider=provider)
        index.refresh(memories)
        return index

    def refresh(self, memories: list[Memory]) -> None:
        active_ids = {memory.id for memory in memories}
        changed = False
        for memory_id in list(self.vectors):
            if memory_id not in active_ids:
                self.vectors.pop(memory_id, None)
                self.fingerprints.pop(memory_id, None)
                changed = True
        for memory in memories:
            fingerprint = memory_fingerprint(memory)
            if self.fingerprints.get(memory.id) == fingerprint:
                continue
            self.vectors[memory.id] = self.provider.embed(memory_text(memory))
            self.fingerprints[memory.id] = fingerprint
            changed = True
        if changed:
            self.save()

    def score(self, memory: Memory, query: "PreflightQuery") -> SemanticSignal:
        vector = self.vectors.get(memory.id)
        if vector is None:
            return SemanticSignal(label=f"{self.provider.label} missing vector")
        query_vector = self.provider.embed(query.text())
        similarity = cosine_similarity(query_vector, vector)
        score = max(0.0, similarity) * 1.5
        return SemanticSignal(label=self.provider.label, score=round(score, 3), available=True)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, JSONDecodeError):
            return
        if data.get("provider") != self.provider.label or data.get("dimensions") != self.provider.dimensions:
            return
        self.vectors = {
            memory_id: [float(value) for value in vector]
            for memory_id, vector in data.get("vectors", {}).items()
            if isinstance(vector, list)
        }
        self.fingerprints = {
            memory_id: str(fingerprint)
            for memory_id, fingerprint in data.get("fingerprints", {}).items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.path.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "provider": self.provider.label,
                    "dimensions": self.provider.dimensions,
                    "fingerprints": self.fingerprints,
                    "vectors": self.vectors,
                },
                handle,
                indent=2,
                ensure_ascii=True,
            )
            handle.write("\n")
        temp_file.replace(self.path)


class InMemorySemanticIndex(SemanticIndex):
    def __init__(self, signals: dict[str, SemanticSignal | float], label: str = "in-memory semantic index") -> None:
        self.signals = signals
        self.label = label

    def score(self, memory: Memory, query: "PreflightQuery") -> SemanticSignal:
        signal = self.signals.get(memory.id)
        if signal is None:
            return SemanticSignal(label="unavailable")
        if isinstance(signal, SemanticSignal):
            return signal
        return SemanticSignal(label=self.label, score=signal, available=True)


DEFAULT_SEMANTIC_INDEX = SemanticIndex()


@dataclass
class PreflightQuery:
    prompt: str
    actor: str = "developer"
    area: str = ""
    files: list[str] | None = None
    workflow: list[str] | None = None
    environment: list[str] | None = None
    risk: str = "medium"

    def text(self) -> str:
        return " ".join(
            [
                self.prompt,
                self.area,
                " ".join(self.files or []),
                " ".join(self.workflow or []),
                " ".join(self.environment or []),
            ]
        )


@dataclass
class Match:
    memory: Memory
    score: float
    matched_terms: list[str]
    score_breakdown: list[str] = field(default_factory=list)
    graph_source_id: str = ""
    graph_source_title: str = ""
    graph_relation_type: str = ""
    graph_relation_reason: str = ""

    def is_graph_expanded(self) -> bool:
        return bool(self.graph_relation_type and self.graph_source_id)


def preflight(memories: list[Memory], query: PreflightQuery, semantic_index: SemanticIndex | None = None) -> ActionNote | None:
    matches = rank_memories(memories, query, semantic_index=semantic_index)
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


def rank_memories(memories: list[Memory], query: PreflightQuery, semantic_index: SemanticIndex | None = None) -> list[Match]:
    query_terms = tokenize(query.text())
    matches: list[Match] = []
    semantic_index = semantic_index or DEFAULT_SEMANTIC_INDEX
    for memory in memories:
        if scope_conflicts_with_query(memory, query):
            continue
        memory_terms = tokenize(memory_text(memory))
        overlap = sorted(query_terms & memory_terms)
        context_bonus = context_signal_score(memory, query)
        semantic_signal = semantic_signal_score(memory, query, semantic_index)
        semantic_bonus = semantic_signal.contribution()
        proposal_grounding = semantic_proposal_grounding(memory, query, semantic_signal) if not overlap and context_bonus <= 0 else []
        if not overlap and context_bonus <= 0 and not proposal_grounding:
            continue
        actor_bonus = actor_signal_score(memory, query) if overlap or context_bonus > 0 else 0.0
        hard_signal_bonus = context_bonus + actor_bonus
        proposal_bonus = semantic_proposal_bonus(proposal_grounding)
        text_score = len(overlap) * 0.5
        liability_bonus = memory.liability_score * 0.2
        confidence_bonus = memory.confidence * 0.3
        score = (text_score + semantic_bonus + hard_signal_bonus + proposal_bonus + liability_bonus + confidence_bonus) * TYPE_WEIGHT[memory.type]
        matches.append(
            Match(
                memory=memory,
                score=round(score, 3),
                matched_terms=overlap[:8] if overlap else [f"semantic:{item}" for item in proposal_grounding[:3]],
                score_breakdown=direct_score_breakdown(
                    memory=memory,
                    overlap=overlap,
                    text_score=text_score,
                    semantic_signal=semantic_signal,
                    context_bonus=context_bonus,
                    actor_bonus=actor_bonus,
                    proposal_grounding=proposal_grounding,
                    proposal_bonus=proposal_bonus,
                    liability_bonus=liability_bonus,
                    confidence_bonus=confidence_bonus,
                ),
            )
        )
    matches = expand_graph_matches(memories, matches, query)
    return sorted(matches, key=lambda item: item.score, reverse=True)


def expand_graph_matches(memories: list[Memory], matches: list[Match], query: PreflightQuery) -> list[Match]:
    if not matches:
        return matches
    memory_by_id = {memory.id: memory for memory in memories}
    match_by_id = {match.memory.id: match for match in matches}
    expanded = list(matches)
    for match in sorted(matches, key=lambda item: item.score, reverse=True):
        if match.score < action_threshold(query.risk):
            continue
        for relationship in match.memory.relationships:
            target = memory_by_id.get(relationship.target_id)
            if target is None:
                continue
            if not graph_relationship_can_expand(target, relationship, query):
                continue
            graph_score = graph_expansion_score(match, target, relationship)
            existing_match = match_by_id.get(target.id)
            if existing_match is not None:
                if not existing_match.is_graph_expanded():
                    existing_match.matched_terms = existing_match.matched_terms[:6] + [
                        f"graph:{relationship.type.value}",
                        f"via:{match.memory.id}",
                    ]
                    existing_match.graph_source_id = match.memory.id
                    existing_match.graph_source_title = match.memory.title
                    existing_match.graph_relation_type = relationship.type.value
                    existing_match.graph_relation_reason = relationship.reason
                    existing_match.score_breakdown.extend(graph_score_breakdown(match, target, relationship))
                existing_match.score = max(existing_match.score, graph_score)
                continue
            graph_match = Match(
                memory=target,
                score=graph_score,
                matched_terms=[f"graph:{relationship.type.value}", f"via:{match.memory.id}"],
                score_breakdown=graph_score_breakdown(match, target, relationship),
                graph_source_id=match.memory.id,
                graph_source_title=match.memory.title,
                graph_relation_type=relationship.type.value,
                graph_relation_reason=relationship.reason,
            )
            expanded.append(graph_match)
            match_by_id[target.id] = graph_match
    return expanded


def graph_relationship_can_expand(target: Memory, relationship: MemoryRelationship, query: PreflightQuery) -> bool:
    if target.type not in RELATION_TARGET_TYPES[relationship.type]:
        return False
    if target.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and relationship.type not in STABLE_GRAPH_RELATIONS:
        return False
    if target.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and not stable_scope_is_grounded(target, query):
        return False
    return not scope_conflicts_with_query(target, query)


def scope_conflicts_with_query(memory: Memory, query: PreflightQuery) -> bool:
    if axis_conflicts(memory.scope.actor, [query.actor]):
        return True
    if axis_conflicts(memory.scope.code, query_code_signals(query)):
        return True
    if axis_conflicts(memory.scope.workflow, query.workflow or []):
        return True
    if axis_conflicts(memory.scope.environment, query.environment or []):
        return True
    return False


def stable_scope_is_grounded(memory: Memory, query: PreflightQuery) -> bool:
    action_scope = memory.scope.code + memory.scope.workflow + memory.scope.environment
    if not action_scope:
        return False
    query_action_scope = query_code_signals(query) + (query.workflow or []) + (query.environment or [])
    if not query_action_scope:
        return False
    return (
        axis_overlaps(memory.scope.code, query_code_signals(query))
        or axis_overlaps(memory.scope.workflow, query.workflow or [])
        or axis_overlaps(memory.scope.environment, query.environment or [])
    )


def query_code_signals(query: PreflightQuery) -> list[str]:
    signals = list(query.files or [])
    if query.area:
        signals.append(query.area)
    return signals


def axis_conflicts(memory_values: list[str], query_values: list[str]) -> bool:
    clean_memory_values = clean_axis_values(memory_values)
    clean_query_values = clean_axis_values(query_values)
    return bool(clean_memory_values and clean_query_values and not axis_overlaps(clean_memory_values, clean_query_values))


def axis_overlaps(memory_values: list[str], query_values: list[str]) -> bool:
    clean_memory_values = clean_axis_values(memory_values)
    clean_query_values = clean_axis_values(query_values)
    return any(axis_value_matches(memory_value, query_value) for memory_value in clean_memory_values for query_value in clean_query_values)


def clean_axis_values(values: list[str]) -> list[str]:
    return [value.strip().lower() for value in values if value and value.strip()]


def axis_value_matches(memory_value: str, query_value: str) -> bool:
    return memory_value in query_value or query_value in memory_value


def graph_expansion_score(match: Match, target: Memory, relationship: MemoryRelationship) -> float:
    link_weight = GRAPH_LINK_WEIGHT[relationship.type]
    score = (match.score * 0.55) + (link_weight * TYPE_WEIGHT[target.type])
    return round(max(0.1, score), 3)


def semantic_signal_score(memory: Memory, query: PreflightQuery, semantic_index: SemanticIndex) -> SemanticSignal:
    return semantic_index.score(memory, query)


def semantic_proposal_grounding(memory: Memory, query: PreflightQuery, semantic_signal: SemanticSignal) -> list[str]:
    if not semantic_signal.available or semantic_signal.contribution() < SEMANTIC_PROPOSAL_MIN_SCORE:
        return []
    grounding: list[str] = []
    if axis_overlaps(memory.scope.code, query_code_signals(query)):
        grounding.append("code scope")
    if axis_overlaps(memory.scope.workflow, query.workflow or []):
        grounding.append("workflow scope")
    if axis_overlaps(memory.scope.environment, query.environment or []):
        grounding.append("environment scope")
    if memory.evidence:
        grounding.append("evidence")
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and memory.approved_by:
        grounding.append("authority")
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and "authority" not in grounding:
        return []
    if not any(item in grounding for item in ("code scope", "workflow scope", "environment scope")):
        return []
    if "evidence" not in grounding and "authority" not in grounding:
        return []
    return grounding


def semantic_proposal_bonus(proposal_grounding: list[str]) -> float:
    if not proposal_grounding:
        return 0.0
    scope_bonus = 0.45 if any(item in proposal_grounding for item in ("code scope", "workflow scope", "environment scope")) else 0.0
    evidence_bonus = 0.25 if "evidence" in proposal_grounding else 0.0
    authority_bonus = 0.2 if "authority" in proposal_grounding else 0.0
    return scope_bonus + evidence_bonus + authority_bonus


def memory_fingerprint(memory: Memory) -> str:
    payload = "\n".join([memory.id, memory.updated_at, memory_text(memory)])
    return sha256(payload.encode("utf-8")).hexdigest()


def add_hashed_feature(vector: list[float], feature: str, *, weight: float) -> None:
    digest = sha256(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(vector)
    vector[index] += weight


def token_character_features(token: str) -> list[str]:
    compact = token.replace("_", "").replace("-", "").replace(".", "").replace("/", "")
    if len(compact) < 4:
        return []
    return [f"chars:{compact[index:index + 4]}" for index in range(len(compact) - 3)]


def normalize_vector(vector: list[float]) -> list[float]:
    magnitude = sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right))


def direct_score_breakdown(
    *,
    memory: Memory,
    overlap: list[str],
    text_score: float,
    semantic_signal: SemanticSignal,
    context_bonus: float,
    actor_bonus: float,
    proposal_grounding: list[str],
    proposal_bonus: float,
    liability_bonus: float,
    confidence_bonus: float,
) -> list[str]:
    breakdown = [
        f"type weight: {memory.type.value} x{TYPE_WEIGHT[memory.type]}",
        f"liability: {memory.liability_score}/5 -> +{liability_bonus:.2f}",
        f"confidence: {memory.confidence:.2f} -> +{confidence_bonus:.2f}",
    ]
    if overlap:
        breakdown.insert(0, f"text overlap: {', '.join(overlap[:6])} -> +{text_score:.2f}")
    breakdown.append(semantic_score_breakdown(semantic_signal))
    if context_bonus:
        breakdown.append(f"hard scope signals -> +{context_bonus:.2f}")
    if proposal_grounding:
        breakdown.append(f"semantic proposal grounded by {', '.join(proposal_grounding)} -> +{proposal_bonus:.2f}")
    if actor_bonus:
        breakdown.append(f"actor signal: {', '.join(memory.scope.actor[:2])} -> +{actor_bonus:.2f}")
    return breakdown


def semantic_score_breakdown(signal: SemanticSignal) -> str:
    if not signal.available:
        return f"semantic signal: {signal.label} -> +0.00"
    sign = "+" if signal.score >= 0 else ""
    return f"semantic signal: {signal.label} -> {sign}{signal.score:.2f}"


def graph_score_breakdown(match: Match, target: Memory, relationship: MemoryRelationship) -> list[str]:
    return [
        f"graph link: {relationship.type.value} via {match.memory.id} -> +{GRAPH_LINK_WEIGHT[relationship.type]:.2f}",
        f"source score carry: {match.score} x0.55",
        f"target type weight: {target.type.value} x{TYPE_WEIGHT[target.type]}",
    ]


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
