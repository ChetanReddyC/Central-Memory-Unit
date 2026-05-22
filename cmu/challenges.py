from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Memory, MemoryScope, MemoryStatus, MemoryType


RESOLVABLE_OUTCOMES = {"exception", "strengthen", "update", "retire", "split"}
MUTATING_OUTCOMES = {"update", "retire", "split"}


@dataclass
class ChallengeRequest:
    memory_id: str
    mismatch: str
    benefit: str
    risk: str
    rollback: str
    challenged_by: str = ""
    evidence: list[str] | None = None
    confidence: float = 0.6


@dataclass
class ResolveChallengeRequest:
    challenge_id: str
    outcome: str
    approved_by: str
    replacement_title: str = ""
    replacement_summary: str = ""
    replacement_use_path: str = ""
    replacement_avoid: str = ""
    replacement_challenge: str = ""
    retirement_reason: str = ""
    split_title: str = ""
    split_summary: str = ""
    split_use_path: str = ""
    split_avoid: str = ""
    split_challenge: str = ""
    split_scope: MemoryScope | None = None
    evidence: list[str] | None = None


@dataclass
class ChallengeDecision:
    saved: bool
    reason: str
    stable_memory: Memory | None = None
    challenge_memory: Memory | None = None
    missing: list[str] | None = None

    def render(self) -> str:
        if not self.saved or self.stable_memory is None or self.challenge_memory is None:
            lines = ["Stable Memory Challenge Not Saved", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {format_list(self.missing)}")
            return "\n".join(lines)
        return "\n".join(
            [
                "CMU Stable Memory Challenge",
                f"Stable Memory: {self.stable_memory.id} [{self.stable_memory.type.value}] {self.stable_memory.title}",
                f"Challenge Record: {self.challenge_memory.id} [candidate] {self.challenge_memory.title}",
                f"Mismatch: {self.challenge_memory.summary}",
                f"Expected Benefit: {self.challenge_memory.use_this_path}",
                f"Risk: {self.challenge_memory.avoid_this}",
                f"Rollback Path: {self.challenge_memory.challenge_only_if}",
                f"Evidence: {format_list(self.challenge_memory.evidence)}",
                "Status: Challenge recorded. Stable memory was not changed.",
                "Possible Outcomes: update the memory, create an exception, retire/split it, or strengthen the precedent.",
            ]
        )


@dataclass
class ChallengeResolution:
    applied: bool
    reason: str
    outcome: str
    challenge_memory: Memory | None = None
    stable_memory: Memory | None = None
    outcome_memory: Memory | None = None
    missing: list[str] | None = None

    def render(self) -> str:
        if not self.applied:
            lines = ["Challenge Resolution Not Applied", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {format_list(self.missing)}")
            return "\n".join(lines)
        lines = [
            "CMU Challenge Resolution Applied",
            f"Outcome: {self.outcome}",
        ]
        if self.challenge_memory is not None:
            lines.append(f"Challenge: {self.challenge_memory.id} [{self.challenge_memory.status.value}]")
        if self.stable_memory is not None:
            lines.append(f"Stable Memory: {self.stable_memory.id} [{self.stable_memory.type.value}] {self.stable_memory.title}")
        if self.outcome_memory is not None:
            lines.append(f"Created Memory: {self.outcome_memory.id} [{self.outcome_memory.type.value}] {self.outcome_memory.title}")
        lines.append(f"Reason: {self.reason}")
        return "\n".join(lines)


def challenge_stable_memory(memories: list[Memory], request: ChallengeRequest) -> ChallengeDecision:
    stable_memory = find_memory(memories, request.memory_id)
    if stable_memory.type not in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
        return ChallengeDecision(
            saved=False,
            reason="only Practice or Anchor memory can be challenged through this path",
            stable_memory=stable_memory,
        )
    missing = missing_challenge_fields(request)
    if missing:
        return ChallengeDecision(
            saved=False,
            reason="challenge requires mismatch, expected benefit, risk, and rollback path",
            stable_memory=stable_memory,
            missing=missing,
        )
    challenge_memory = Memory.create(
        type=MemoryType.CANDIDATE,
        title=f"Challenge to {stable_memory.title}",
        summary=request.mismatch,
        signals=[challenge_signal(stable_memory)],
        scope=copy_scope(stable_memory.scope),
        evidence=challenge_evidence(stable_memory, request),
        use_this_path=request.benefit,
        avoid_this=request.risk,
        challenge_only_if=request.rollback,
        liability_score=stable_memory.liability_score,
        confidence=request.confidence,
    )
    return ChallengeDecision(
        saved=True,
        reason="Stored stable-memory challenge Candidate.",
        stable_memory=stable_memory,
        challenge_memory=challenge_memory,
    )


def resolve_challenge(memories: list[Memory], request: ResolveChallengeRequest) -> ChallengeResolution:
    outcome = request.outcome.strip().lower()
    if not request.approved_by.strip():
        return ChallengeResolution(
            applied=False,
            reason="explicit owner/team approval required",
            outcome=outcome,
        )
    if outcome not in RESOLVABLE_OUTCOMES:
        return ChallengeResolution(
            applied=False,
            reason=f"unsupported challenge outcome: {request.outcome}",
            outcome=outcome,
        )
    challenge_memory = find_memory(memories, request.challenge_id)
    if not is_challenge_candidate(challenge_memory):
        return ChallengeResolution(
            applied=False,
            reason="memory is not an active Practice/Anchor challenge Candidate",
            outcome=outcome,
            challenge_memory=challenge_memory,
        )
    stable_memory_id = challenged_memory_id(challenge_memory)
    if not stable_memory_id:
        return ChallengeResolution(
            applied=False,
            reason="challenge Candidate does not link to a stable memory",
            outcome=outcome,
            challenge_memory=challenge_memory,
        )
    stable_memory = find_memory(memories, stable_memory_id)
    if stable_memory.type not in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
        return ChallengeResolution(
            applied=False,
            reason="linked memory is no longer Practice or Anchor memory",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
        )
    missing = missing_resolution_fields(request)
    if missing:
        return ChallengeResolution(
            applied=False,
            reason=f"{outcome} outcome requires explicit approved resolution details",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
            missing=missing,
        )
    if outcome == "exception":
        exception_memory = create_exception_memory(challenge_memory, stable_memory, request.approved_by)
        challenge_memory.status = MemoryStatus.RETIRED
        challenge_memory.evidence.append(resolution_evidence("exception", request.approved_by))
        return ChallengeResolution(
            applied=True,
            reason="Created scoped Exception Memory from approved challenge.",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
            outcome_memory=exception_memory,
        )
    if outcome == "strengthen":
        strengthen_memory(stable_memory, challenge_memory, request.approved_by)
        challenge_memory.status = MemoryStatus.RETIRED
        challenge_memory.evidence.append(resolution_evidence("strengthen", request.approved_by))
        return ChallengeResolution(
            applied=True,
            reason="Stable memory kept and strengthened with approved challenge evidence.",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
        )
    if outcome == "update":
        update_memory(stable_memory, challenge_memory, request)
        challenge_memory.status = MemoryStatus.RETIRED
        challenge_memory.evidence.append(resolution_evidence("update", request.approved_by))
        return ChallengeResolution(
            applied=True,
            reason="Stable memory updated through approved challenge resolution.",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
        )
    if outcome == "retire":
        retire_memory(stable_memory, challenge_memory, request)
        challenge_memory.status = MemoryStatus.RETIRED
        challenge_memory.evidence.append(resolution_evidence("retire", request.approved_by))
        return ChallengeResolution(
            applied=True,
            reason="Stable memory retired through approved challenge resolution.",
            outcome=outcome,
            challenge_memory=challenge_memory,
            stable_memory=stable_memory,
        )
    split_memory = create_split_memory(challenge_memory, stable_memory, request)
    note_split_on_original(stable_memory, challenge_memory, split_memory, request)
    challenge_memory.status = MemoryStatus.RETIRED
    challenge_memory.evidence.append(resolution_evidence("split", request.approved_by))
    return ChallengeResolution(
        applied=True,
        reason="Created scoped stable memory split from approved challenge.",
        outcome=outcome,
        challenge_memory=challenge_memory,
        stable_memory=stable_memory,
        outcome_memory=split_memory,
    )


def missing_challenge_fields(request: ChallengeRequest) -> list[str]:
    missing: list[str] = []
    if not request.mismatch.strip():
        missing.append("mismatch")
    if not request.benefit.strip():
        missing.append("benefit")
    if not request.risk.strip():
        missing.append("risk")
    if not request.rollback.strip():
        missing.append("rollback")
    return missing


def missing_resolution_fields(request: ResolveChallengeRequest) -> list[str]:
    outcome = request.outcome.strip().lower()
    if outcome not in MUTATING_OUTCOMES:
        return []
    missing: list[str] = []
    if not cleaned_evidence(request):
        missing.append("resolution_evidence")
    if outcome == "update":
        if not request.replacement_summary.strip():
            missing.append("replacement_summary")
        if not request.replacement_use_path.strip():
            missing.append("replacement_use_path")
        if not request.replacement_avoid.strip():
            missing.append("replacement_avoid")
        if not request.replacement_challenge.strip():
            missing.append("replacement_challenge")
    elif outcome == "retire":
        if not request.retirement_reason.strip():
            missing.append("retirement_reason")
    elif outcome == "split":
        if not request.split_title.strip():
            missing.append("split_title")
        if not request.split_summary.strip():
            missing.append("split_summary")
        if not request.split_use_path.strip():
            missing.append("split_use_path")
        if not request.split_avoid.strip():
            missing.append("split_avoid")
        if not request.split_challenge.strip():
            missing.append("split_challenge")
        if request.split_scope is None or not request.split_scope.flattened():
            missing.append("split_scope")
    return missing


def challenge_signal(memory: Memory) -> str:
    if memory.type == MemoryType.ANCHOR:
        return "anchor challenge"
    return "practice challenge"


def is_challenge_candidate(memory: Memory) -> bool:
    return (
        memory.type == MemoryType.CANDIDATE
        and memory.status == MemoryStatus.ACTIVE
        and any(signal in {"practice challenge", "anchor challenge"} for signal in memory.signals)
    )


def challenged_memory_id(memory: Memory) -> str:
    for evidence in memory.evidence:
        match = re.fullmatch(r"Challenges stable memory: (mem_[A-Za-z0-9]+)", evidence.strip())
        if match:
            return match.group(1)
    return ""


def create_exception_memory(challenge_memory: Memory, stable_memory: Memory, approved_by: str) -> Memory:
    return Memory.create(
        type=MemoryType.EXCEPTION,
        title=f"Exception to {stable_memory.title}",
        summary=challenge_memory.summary,
        signals=list(challenge_memory.signals),
        scope=copy_scope(challenge_memory.scope),
        evidence=exception_evidence(challenge_memory, stable_memory, approved_by),
        use_this_path=challenge_memory.use_this_path,
        avoid_this=challenge_memory.avoid_this,
        challenge_only_if=f"Rollback path: {challenge_memory.challenge_only_if}",
        liability_score=challenge_memory.liability_score,
        confidence=max(challenge_memory.confidence, 0.7),
        approved_by=approved_by,
    )


def exception_evidence(challenge_memory: Memory, stable_memory: Memory, approved_by: str) -> list[str]:
    evidence = list(challenge_memory.evidence)
    evidence.extend(
        [
            f"Exception to stable memory: {stable_memory.id}",
            resolution_evidence("exception", approved_by),
        ]
    )
    return dedupe(evidence)


def strengthen_memory(stable_memory: Memory, challenge_memory: Memory, approved_by: str) -> None:
    stable_memory.evidence = dedupe(
        stable_memory.evidence
        + [
            f"Challenge reviewed and precedent strengthened: {challenge_memory.id}",
            resolution_evidence("strengthen", approved_by),
            f"Rejected mismatch: {challenge_memory.summary}",
        ]
    )
    stable_memory.confidence = max(stable_memory.confidence, 0.8)


def update_memory(stable_memory: Memory, challenge_memory: Memory, request: ResolveChallengeRequest) -> None:
    replacement_title = request.replacement_title.strip()
    if replacement_title:
        stable_memory.title = replacement_title
    stable_memory.summary = request.replacement_summary.strip()
    stable_memory.use_this_path = request.replacement_use_path.strip()
    stable_memory.avoid_this = request.replacement_avoid.strip()
    stable_memory.challenge_only_if = request.replacement_challenge.strip()
    stable_memory.approved_by = request.approved_by.strip()
    stable_memory.evidence = dedupe(
        stable_memory.evidence
        + [
            f"Stable memory updated from challenge: {challenge_memory.id}",
            resolution_evidence("update", request.approved_by),
            f"Accepted mismatch: {challenge_memory.summary}",
            f"Rollback path from challenge: {challenge_memory.challenge_only_if}",
        ]
        + cleaned_evidence(request)
    )
    stable_memory.confidence = max(stable_memory.confidence, stable_confidence_floor(stable_memory))


def retire_memory(stable_memory: Memory, challenge_memory: Memory, request: ResolveChallengeRequest) -> None:
    stable_memory.status = MemoryStatus.RETIRED
    stable_memory.approved_by = request.approved_by.strip()
    stable_memory.evidence = dedupe(
        stable_memory.evidence
        + [
            f"Stable memory retired from challenge: {challenge_memory.id}",
            resolution_evidence("retire", request.approved_by),
            f"Retirement reason: {request.retirement_reason.strip()}",
            f"Rollback path from challenge: {challenge_memory.challenge_only_if}",
        ]
        + cleaned_evidence(request)
    )


def create_split_memory(challenge_memory: Memory, stable_memory: Memory, request: ResolveChallengeRequest) -> Memory:
    return Memory.create(
        type=stable_memory.type,
        title=request.split_title,
        summary=request.split_summary,
        signals=dedupe(stable_memory.signals + list(challenge_memory.signals)),
        scope=copy_scope(request.split_scope or MemoryScope()),
        evidence=split_evidence(challenge_memory, stable_memory, request),
        use_this_path=request.split_use_path,
        avoid_this=request.split_avoid,
        challenge_only_if=request.split_challenge,
        liability_score=stable_memory.liability_score,
        confidence=max(challenge_memory.confidence, stable_confidence_floor(stable_memory)),
        approved_by=request.approved_by,
    )


def note_split_on_original(
    stable_memory: Memory,
    challenge_memory: Memory,
    split_memory: Memory,
    request: ResolveChallengeRequest,
) -> None:
    stable_memory.evidence = dedupe(
        stable_memory.evidence
        + [
            f"Stable memory split from challenge: {challenge_memory.id}",
            f"Split-off stable memory: {split_memory.id}",
            resolution_evidence("split", request.approved_by),
            f"Rollback path from challenge: {challenge_memory.challenge_only_if}",
        ]
        + cleaned_evidence(request)
    )
    stable_memory.confidence = max(stable_memory.confidence, stable_confidence_floor(stable_memory))


def split_evidence(challenge_memory: Memory, stable_memory: Memory, request: ResolveChallengeRequest) -> list[str]:
    return dedupe(
        challenge_memory.evidence
        + [
            f"Split from stable memory: {stable_memory.id}",
            resolution_evidence("split", request.approved_by),
            f"Rollback path from challenge: {challenge_memory.challenge_only_if}",
        ]
        + cleaned_evidence(request)
    )


def cleaned_evidence(request: ResolveChallengeRequest) -> list[str]:
    return [item.strip() for item in request.evidence or [] if item.strip()]


def stable_confidence_floor(memory: Memory) -> float:
    if memory.type == MemoryType.ANCHOR:
        return 0.8
    return 0.75


def resolution_evidence(outcome: str, approved_by: str) -> str:
    return f"Challenge resolved as {outcome} by {approved_by.strip()}"


def challenge_evidence(stable_memory: Memory, request: ChallengeRequest) -> list[str]:
    evidence = [
        f"Challenges stable memory: {stable_memory.id}",
        f"Stable memory type: {stable_memory.type.value}",
    ]
    if stable_memory.approved_by:
        evidence.append(f"Original authority: {stable_memory.approved_by}")
    if request.challenged_by.strip():
        evidence.append(f"Challenged by: {request.challenged_by.strip()}")
    evidence.extend(item.strip() for item in request.evidence or [] if item.strip())
    return evidence


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    cleaned = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def copy_scope(scope: MemoryScope) -> MemoryScope:
    return MemoryScope(
        ownership=list(scope.ownership),
        code=list(scope.code),
        workflow=list(scope.workflow),
        environment=list(scope.environment),
        actor=list(scope.actor),
        time=list(scope.time),
    )


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "None"
