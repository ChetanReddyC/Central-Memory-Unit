from __future__ import annotations

from dataclasses import dataclass

from .authority import set_memory_authority
from .models import Memory, MemoryType, utc_now
from .retrieval import tokenize


@dataclass
class PromotionReview:
    memory: Memory
    target_type: MemoryType
    gate_passed: bool
    missing: list[str]
    duplicate: Memory | None = None

    def render(self) -> str:
        if self.target_type in {MemoryType.ANCHOR, MemoryType.PRACTICE}:
            return self.render_authority_proposal()
        lines = [
            "CMU Promotion Review",
            f"Memory: {self.memory.id} [{self.memory.type.value}] {self.memory.title}",
            f"Target: {self.target_type.value}",
            f"Gate: {'PASS' if self.gate_passed else 'BLOCKED'}",
            f"Situation: {self.memory.summary}",
            f"Signals: {format_list(self.memory.signals)}",
            f"Scope: {format_list(self.memory.scope.flattened())}",
            f"What Worked: {self.memory.use_this_path or 'Not specified yet.'}",
            f"What Failed: {self.memory.avoid_this or 'Not specified yet.'}",
            f"Future-Use Reason: {self.memory.challenge_only_if or 'Not specified yet.'}",
            f"Evidence: {format_list(self.memory.evidence)}",
            f"Liability: {self.memory.liability_score}/5",
            f"Confidence: {self.memory.confidence:.2f}",
        ]
        if self.duplicate is not None:
            lines.append(f"Duplicate Risk: {self.duplicate.id} {self.duplicate.title}")
        if self.missing:
            lines.append(f"Missing: {format_list(self.missing)}")
        lines.append("Choices: Approve, Narrow/Edit, or Keep as Candidate.")
        return "\n".join(lines)

    def render_authority_proposal(self) -> str:
        target_label = self.target_type.value.title()
        lines = [
            f"CMU {target_label} Proposal Review",
            f"Memory: {self.memory.id} [{self.memory.type.value}] {self.memory.title}",
            f"Target: {self.target_type.value}",
            f"Proposal Gate: {'READY FOR AUTHORITY REVIEW' if self.gate_passed else 'NEEDS NARROWING'}",
            f"Why This Qualifies: {proposal_reason(self.memory, self.target_type)}",
            f"Proposed Scope: {format_list(self.memory.scope.flattened())}",
            f"Key Evidence: {format_list(self.memory.evidence)}",
            f"Liability: {self.memory.liability_score}/5",
            f"Confidence: {self.memory.confidence:.2f}",
            f"Use This Path: {self.memory.use_this_path or 'Not specified yet.'}",
            f"Avoid This: {self.memory.avoid_this or 'Not specified yet.'}",
            f"Challenge Only If: {self.memory.challenge_only_if or 'Not specified yet.'}",
            "Authority Needed: Explicit owner/team approval before promotion.",
        ]
        if self.missing:
            lines.append(f"Missing/Risks: {format_list(self.missing)}")
        lines.extend(
            [
                "Choices: Approve, Narrow/Edit, or Keep as Situation.",
                "Status: Proposal only. No promotion has been applied.",
            ]
        )
        return "\n".join(lines)


@dataclass
class PromotionDecision:
    promoted: bool
    reason: str
    memory: Memory | None = None
    approved_by: str = ""

    def render(self) -> str:
        if not self.promoted or self.memory is None:
            return "\n".join(["Promotion Not Applied", f"Reason: {self.reason}"])
        lines = [
            "Promotion Applied",
            f"Memory: {self.memory.id}",
            f"Type: {self.memory.type.value}",
            f"Title: {self.memory.title}",
        ]
        if self.approved_by:
            lines.append(f"Approved By: {self.approved_by}")
        return "\n".join(lines)


def review_promotion(memories: list[Memory], memory_id: str, target_type: MemoryType) -> PromotionReview:
    memory = find_memory(memories, memory_id)
    missing = promotion_gate_missing(memory, target_type)
    duplicate = find_duplicate_situation(memories, memory) if target_type == MemoryType.SITUATION else None
    gate_passed = not missing and duplicate is None
    return PromotionReview(
        memory=memory,
        target_type=target_type,
        gate_passed=gate_passed,
        missing=missing,
        duplicate=duplicate,
    )


def promote_memory(
    memories: list[Memory],
    memory_id: str,
    target_type: MemoryType,
    approved_by: str = "",
    authority_owner: str = "",
    approver_role: str = "",
    consequence: str = "",
    review_due_at: str = "",
) -> PromotionDecision:
    review = review_promotion(memories, memory_id, target_type)
    if not review.gate_passed:
        reasons = []
        if review.missing:
            reasons.append(f"missing {', '.join(review.missing)}")
        if review.duplicate is not None:
            reasons.append(f"duplicate of {review.duplicate.id}")
        return PromotionDecision(promoted=False, reason="; ".join(reasons), memory=review.memory)
    normalized_approver = approved_by.strip()
    requires_authority = target_type in {MemoryType.ANCHOR, MemoryType.PRACTICE}
    if requires_authority and not normalized_approver:
        return PromotionDecision(
            promoted=False,
            reason="explicit owner/team approval required",
            memory=review.memory,
        )
    original = review.memory.to_dict()
    review.memory.type = target_type
    review.memory.confidence = max(review.memory.confidence, stable_confidence_floor(target_type))
    if requires_authority:
        review.memory.approved_by = normalized_approver
        approval_evidence = f"Authority approval: {normalized_approver}"
        if approval_evidence not in review.memory.evidence:
            review.memory.evidence.append(approval_evidence)
        if authority_owner or approver_role or consequence or review_due_at:
            authority = set_memory_authority(
                review.memory,
                owner=authority_owner,
                approved_by=normalized_approver,
                approver_role=approver_role,
                consequence=consequence,
                review_due_at=review_due_at,
            )
            if not authority.applied:
                restored = Memory.from_dict(original)
                review.memory.__dict__.update(restored.__dict__)
                return PromotionDecision(promoted=False, reason=authority.reason, memory=review.memory)
    review.memory.updated_at = utc_now()
    return PromotionDecision(
        promoted=True,
        reason=f"Promoted to {target_type.value}.",
        memory=review.memory,
        approved_by=normalized_approver if requires_authority else "",
    )


def promotion_gate_missing(memory: Memory, target_type: MemoryType) -> list[str]:
    if target_type == MemoryType.ANCHOR:
        return anchor_proposal_missing(memory)
    if target_type == MemoryType.PRACTICE:
        return practice_proposal_missing(memory)
    if target_type != MemoryType.SITUATION:
        return [f"{target_type.value}_promotion_not_supported"]
    missing: list[str] = []
    if memory.type != MemoryType.CANDIDATE:
        missing.append("candidate_type")
    if not memory.summary.strip():
        missing.append("reusable_scenario")
    if not memory.evidence:
        missing.append("evidence_or_outcome")
    if not memory.scope.flattened():
        missing.append("scope")
    if not memory.challenge_only_if.strip():
        missing.append("future_use_reason")
    if not memory.use_this_path.strip() and not memory.avoid_this.strip():
        missing.append("worked_or_failed_lesson")
    return missing


def anchor_proposal_missing(memory: Memory) -> list[str]:
    missing: list[str] = []
    if memory.type != MemoryType.SITUATION:
        missing.append("situation_type")
    if not memory.summary.strip():
        missing.append("stable_knowledge")
    if not memory.evidence:
        missing.append("key_evidence")
    if not memory.scope.flattened():
        missing.append("defined_scope")
    if memory.liability_score < 4:
        missing.append("high_memory_liability")
    return missing


def practice_proposal_missing(memory: Memory) -> list[str]:
    missing: list[str] = []
    if memory.type != MemoryType.SITUATION:
        missing.append("situation_type")
    if not memory.use_this_path.strip():
        missing.append("trusted_default_path")
    if not memory.challenge_only_if.strip():
        missing.append("conditions_or_challenge_signals")
    if not memory.evidence:
        missing.append("supporting_evidence")
    if not memory.scope.flattened():
        missing.append("defined_scope")
    if memory.confidence < 0.7:
        missing.append("trusted_confidence")
    return missing


def proposal_reason(memory: Memory, target_type: MemoryType) -> str:
    if target_type == MemoryType.ANCHOR:
        if memory.liability_score >= 4:
            return "Forgetting this appears costly enough to justify stable memory."
        return "Anchor requires clearer high future cost before it should become stable."
    if target_type == MemoryType.PRACTICE:
        if memory.use_this_path and memory.challenge_only_if:
            return "This contains a default path plus challenge signals for scoped future work."
        return "Practice requires a trusted default path with conditions before it can guide behavior."
    return "Promotion target is evaluated by its gate."


def stable_confidence_floor(target_type: MemoryType) -> float:
    if target_type == MemoryType.ANCHOR:
        return 0.8
    if target_type == MemoryType.PRACTICE:
        return 0.75
    return 0.7


def find_duplicate_situation(memories: list[Memory], candidate: Memory) -> Memory | None:
    candidate_terms = tokenize(
        " ".join(
            [
                candidate.title,
                candidate.summary,
                candidate.use_this_path,
                candidate.avoid_this,
                candidate.challenge_only_if,
                " ".join(candidate.signals),
                " ".join(candidate.evidence),
            ]
        )
    )
    for memory in memories:
        if memory.id == candidate.id or memory.type != MemoryType.SITUATION:
            continue
        memory_terms = tokenize(
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
        if len(candidate_terms & memory_terms) >= 5:
            return memory
    return None


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "None"
