from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Memory, MemoryScope, MemoryType
from .retrieval import tokenize


VALID_TRIGGER_LABELS = {
    "repeated error",
    "hard-won fix",
    "surprising root cause",
    "tradeoff decision",
    "accepted workaround",
    "unsafe path avoided",
    "new convention",
    "hidden dependency",
    "ownership ambiguity",
    "explained failure",
    "contract change",
    "practice challenge",
    "anchor challenge",
    "onboarding confusion",
    "human correction",
    "incident or rollback",
    "hotspot",
    "prompt pattern",
    "tooling quirk",
}

DUPLICATE_STOPWORDS = {
    "about",
    "across",
    "after",
    "again",
    "because",
    "before",
    "candidate",
    "check",
    "cmu",
    "code",
    "commit",
    "could",
    "details",
    "evidence",
    "future",
    "memory",
    "path",
    "project",
    "root",
    "should",
    "signal",
    "signals",
    "stable",
    "through",
    "when",
    "where",
    "with",
    "work",
}


@dataclass
class RememberRequest:
    situation: str
    title: str = ""
    signals: list[str] | None = None
    outcome: str = ""
    worked: str = ""
    failed: str = ""
    future_use: str = ""
    evidence: list[str] | None = None
    liability_score: int = 1
    suggested_next_type: MemoryType = MemoryType.SITUATION
    scope: MemoryScope | None = None
    confidence: float = 0.6

    def to_memory(self) -> Memory:
        return Memory.create(
            type=MemoryType.CANDIDATE,
            title=self.title.strip() or infer_title(self.situation),
            summary=self.situation,
            signals=clean_signals(self.signals or []),
            scope=self.scope or MemoryScope(),
            evidence=self.evidence or ([self.outcome] if self.outcome else []),
            use_this_path=self.worked,
            avoid_this=self.failed,
            challenge_only_if=self.future_use,
            liability_score=self.liability_score,
            confidence=self.confidence,
        )


@dataclass
class RememberDecision:
    saved: bool
    reason: str
    liability_score: int
    suggested_next_type: MemoryType
    memory: Memory | None = None

    def render(self) -> str:
        if not self.saved or self.memory is None:
            return "\n".join(
                [
                    "Candidate Memory Not Saved",
                    f"Reason: {self.reason}",
                    f"Liability: {self.liability_score}/5",
                    f"Suggested Next Type: {self.suggested_next_type.value}",
                ]
            )
        return "\n".join(
            [
                "Candidate Memory Saved",
                f"ID: {self.memory.id}",
                f"Title: {self.memory.title}",
                f"Situation: {self.memory.summary}",
                f"Signals: {format_list(self.memory.signals)}",
                f"What Worked: {self.memory.use_this_path or 'Not specified yet.'}",
                f"What Failed: {self.memory.avoid_this or 'Not specified yet.'}",
                f"Scope: {format_list(self.memory.scope.flattened())}",
                f"Future-Use Reason: {self.memory.challenge_only_if}",
                f"Evidence: {format_list(self.memory.evidence)}",
                f"Liability: {self.liability_score}/5",
                f"Suggested Next Type: {self.suggested_next_type.value}",
                f"Confidence: {self.memory.confidence:.2f}",
            ]
        )


def remember_candidate(existing_memories: list[Memory], request: RememberRequest) -> RememberDecision:
    duplicate = find_duplicate(existing_memories, request)
    if duplicate is not None:
        return RememberDecision(
            saved=False,
            reason=f"Likely duplicate of existing memory {duplicate.id}: {duplicate.title}",
            liability_score=request.liability_score,
            suggested_next_type=request.suggested_next_type,
        )
    missing = missing_required_candidate_fields(request)
    if missing:
        return RememberDecision(
            saved=False,
            reason=f"Missing required Candidate Memory fields: {', '.join(missing)}.",
            liability_score=request.liability_score,
            suggested_next_type=request.suggested_next_type,
        )
    if request.liability_score <= 1 and not request.signals:
        return RememberDecision(
            saved=False,
            reason="Low-liability work without a reusable trigger should stay out of CMU.",
            liability_score=request.liability_score,
            suggested_next_type=request.suggested_next_type,
        )
    return RememberDecision(
        saved=True,
        reason="Stored direct agent-submitted Candidate Memory.",
        liability_score=request.liability_score,
        suggested_next_type=request.suggested_next_type,
        memory=request.to_memory(),
    )


def missing_required_candidate_fields(request: RememberRequest) -> list[str]:
    missing: list[str] = []
    if not request.situation.strip():
        missing.append("situation")
    if not request.future_use.strip():
        missing.append("future_use")
    if not (request.evidence or request.outcome.strip()):
        missing.append("evidence_or_outcome")
    if not (request.scope and request.scope.flattened()):
        missing.append("scope")
    if not request.worked.strip() and not request.failed.strip():
        missing.append("worked_or_failed")
    return missing


def find_duplicate(existing_memories: list[Memory], request: RememberRequest) -> Memory | None:
    request_terms = significant_duplicate_terms(
        " ".join([request.title, request.situation, request.worked, request.failed, request.future_use])
    )
    for memory in existing_memories:
        memory_terms = significant_duplicate_terms(
            " ".join(
                [
                    memory.title,
                    memory.summary,
                    memory.use_this_path,
                    memory.avoid_this,
                    memory.challenge_only_if,
                    " ".join(memory.signals),
                    " ".join(memory.evidence),
                ]
            )
        )
        overlap = request_terms & memory_terms
        smaller_term_set = max(1, min(len(request_terms), len(memory_terms)))
        overlap_ratio = len(overlap) / smaller_term_set
        if len(overlap) >= 5 and overlap_ratio >= 0.45:
            return memory
    return None


def significant_duplicate_terms(text: str) -> set[str]:
    return {term for term in tokenize(text) if term not in DUPLICATE_STOPWORDS}


def clean_signals(signals: list[str]) -> list[str]:
    cleaned = []
    for signal in signals:
        value = signal.strip()
        if value and value in VALID_TRIGGER_LABELS:
            cleaned.append(value)
    return sorted(set(cleaned))


def infer_title(situation: str) -> str:
    words = re.findall(r"[A-Za-z0-9_./-]+", situation)
    title = " ".join(words[:8]).strip()
    return title if title else "Untitled candidate memory"


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "None"
