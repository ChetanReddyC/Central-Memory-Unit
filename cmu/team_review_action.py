from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .authority import set_memory_authority
from .challenges import ChallengeRequest, ResolveChallengeRequest, challenge_stable_memory, resolve_challenge
from .models import Memory, MemoryScope, MemoryStatus, MemoryType, utc_now
from .store import MemoryStore
from .team_directory import TeamDirectoryStore, TeamScopeRecord
from .usage import scope_change_is_safe_narrowing


TEAM_REVIEW_ACTION_VERSION = "cmu-team-review-action/v1"


@dataclass(frozen=True)
class TeamReviewActionResult:
    applied: bool
    action: str
    subject_id: str
    reason: str
    memory: Memory | None = None
    team_scope: TeamScopeRecord | None = None
    outcome_memory: Memory | None = None
    challenge_memory: Memory | None = None
    missing: list[str] = field(default_factory=list)

    def render(self) -> str:
        title = "CMU Team Review Action Applied" if self.applied else "CMU Team Review Action Not Applied"
        lines = [
            title,
            f"Version: {TEAM_REVIEW_ACTION_VERSION}",
            f"Action: {self.action}",
            f"Subject: {self.subject_id}",
            f"Reason: {self.reason}",
        ]
        if self.memory is not None:
            lines.extend(
                [
                    f"Memory: {self.memory.id} [{self.memory.type.value}] {self.memory.title}",
                    f"Authority Owner: {self.memory.authority_owner or 'none'}",
                    f"Approver: {self.memory.approved_by or 'none'}",
                    f"Consequence: {self.memory.authority_consequence or 'none'}",
                ]
            )
        if self.challenge_memory is not None:
            lines.append(
                f"Challenge Memory: {self.challenge_memory.id} [{self.challenge_memory.status.value}] {self.challenge_memory.title}"
            )
        if self.outcome_memory is not None:
            lines.append(f"Outcome Memory: {self.outcome_memory.id} [{self.outcome_memory.type.value}] {self.outcome_memory.title}")
        if self.team_scope is not None:
            lines.extend(
                [
                    f"Team Scope: {self.team_scope.id} {self.team_scope.repo}/{self.team_scope.team}",
                    f"Owner: {self.team_scope.owner or 'none'}",
                    f"Authority Role: {self.team_scope.authority_role or 'none'}",
                    f"Consequence: {self.team_scope.consequence or 'none'}",
                ]
            )
        if self.missing:
            lines.append(f"Missing: {', '.join(self.missing)}")
        lines.extend(
            [
                "",
                "Proof Meaning: owner/team handoff cards now have controlled apply paths for authority, team metadata, challenge, strengthen, retire, split, and narrow-scope outcomes instead of only command suggestions.",
            ]
        )
        return "\n".join(lines)


def apply_team_review_action(
    root: Path | str,
    subject_id: str,
    *,
    action: str,
    owner: str = "",
    approved_by: str = "",
    approver_role: str = "",
    consequence: str = "",
    review_due: str = "",
    mismatch: str = "",
    benefit: str = "",
    risk: str = "",
    rollback: str = "",
    challenged_by: str = "",
    evidence: list[str] | None = None,
    retirement_reason: str = "",
    split_title: str = "",
    split_summary: str = "",
    split_use_path: str = "",
    split_avoid: str = "",
    split_challenge: str = "",
    scope: MemoryScope | None = None,
) -> TeamReviewActionResult:
    if action == "authority":
        return apply_memory_authority_action(
            root,
            subject_id,
            owner=owner,
            approved_by=approved_by,
            approver_role=approver_role,
            consequence=consequence,
            review_due=review_due,
        )
    if action == "team-metadata":
        return apply_team_metadata_action(
            root,
            subject_id,
            owner=owner,
            approver_role=approver_role,
            consequence=consequence,
        )
    if action == "challenge":
        return apply_challenge_action(
            root,
            subject_id,
            mismatch=mismatch,
            benefit=benefit,
            risk=risk,
            rollback=rollback,
            challenged_by=challenged_by,
            evidence=evidence or [],
        )
    if action in {"strengthen", "retire", "split"}:
        return apply_challenge_resolution_action(
            root,
            subject_id,
            action=action,
            approved_by=approved_by,
            evidence=evidence or [],
            retirement_reason=retirement_reason,
            split_title=split_title,
            split_summary=split_summary,
            split_use_path=split_use_path,
            split_avoid=split_avoid,
            split_challenge=split_challenge,
            split_scope=scope,
        )
    if action == "narrow-scope":
        return apply_narrow_scope_action(
            root,
            subject_id,
            approved_by=approved_by,
            proposed_scope=scope or MemoryScope(),
            evidence=evidence or [],
        )
    return TeamReviewActionResult(False, action, subject_id, f"unsupported team-review action: {action}")


def apply_memory_authority_action(
    root: Path | str,
    memory_id: str,
    *,
    owner: str,
    approved_by: str,
    approver_role: str,
    consequence: str,
    review_due: str,
) -> TeamReviewActionResult:
    store = MemoryStore(root)
    memory = next((item for item in store.list() if item.id == memory_id), None)
    if memory is None:
        return TeamReviewActionResult(False, "authority", memory_id, "memory not found")
    decision = set_memory_authority(
        memory,
        owner=owner,
        approved_by=approved_by,
        approver_role=approver_role,
        consequence=consequence,
        review_due_at=review_due,
    )
    if not decision.applied or decision.memory is None:
        return TeamReviewActionResult(
            False,
            "authority",
            memory_id,
            decision.reason,
            memory=memory,
            missing=decision.missing,
        )
    store.update(decision.memory)
    return TeamReviewActionResult(
        True,
        "authority",
        memory_id,
        decision.reason,
        memory=decision.memory,
    )


def apply_team_metadata_action(
    root: Path | str,
    record_id: str,
    *,
    owner: str,
    approver_role: str,
    consequence: str,
) -> TeamReviewActionResult:
    directory = TeamDirectoryStore(root)
    record = next((item for item in directory.list() if item.id == record_id), None)
    if record is None:
        return TeamReviewActionResult(False, "team-metadata", record_id, "team scope not found")
    missing = [
        name
        for name, value in [
            ("owner", owner or record.owner),
            ("approver_role", approver_role or record.authority_role),
            ("consequence", consequence or record.consequence),
        ]
        if not value.strip()
    ]
    if missing:
        return TeamReviewActionResult(
            False,
            "team-metadata",
            record_id,
            "team metadata update requires owner, approver role, and consequence",
            team_scope=record,
            missing=missing,
        )
    record.owner = (owner or record.owner).strip()
    record.authority_role = (approver_role or record.authority_role).strip().lower()
    record.consequence = (consequence or record.consequence).strip().lower()
    directory.update(record)
    return TeamReviewActionResult(
        True,
        "team-metadata",
        record_id,
        "Stored explicit owner/team review metadata.",
        team_scope=record,
    )


def apply_challenge_action(
    root: Path | str,
    memory_id: str,
    *,
    mismatch: str,
    benefit: str,
    risk: str,
    rollback: str,
    challenged_by: str,
    evidence: list[str],
) -> TeamReviewActionResult:
    store = MemoryStore(root)
    memories = store.list()
    try:
        memory = find_memory(memories, memory_id)
    except KeyError:
        return TeamReviewActionResult(False, "challenge", memory_id, "memory not found")
    decision = challenge_stable_memory(
        memories,
        ChallengeRequest(
            memory_id=memory_id,
            mismatch=mismatch,
            benefit=benefit,
            risk=risk,
            rollback=rollback,
            challenged_by=challenged_by or "cmu team-review-action",
            evidence=evidence,
            confidence=0.65,
        ),
    )
    if not decision.saved or decision.challenge_memory is None:
        return TeamReviewActionResult(
            False,
            "challenge",
            memory_id,
            decision.reason,
            memory=memory,
            missing=decision.missing or [],
        )
    store.add(decision.challenge_memory)
    return TeamReviewActionResult(
        True,
        "challenge",
        memory_id,
        "Stored controlled owner/team challenge Candidate.",
        memory=memory,
        challenge_memory=decision.challenge_memory,
    )


def apply_challenge_resolution_action(
    root: Path | str,
    challenge_id: str,
    *,
    action: str,
    approved_by: str,
    evidence: list[str],
    retirement_reason: str,
    split_title: str,
    split_summary: str,
    split_use_path: str,
    split_avoid: str,
    split_challenge: str,
    split_scope: MemoryScope | None,
) -> TeamReviewActionResult:
    store = MemoryStore(root)
    memories = store.list()
    request = ResolveChallengeRequest(
        challenge_id=challenge_id,
        outcome=action,
        approved_by=approved_by,
        retirement_reason=retirement_reason,
        split_title=split_title,
        split_summary=split_summary,
        split_use_path=split_use_path,
        split_avoid=split_avoid,
        split_challenge=split_challenge,
        split_scope=split_scope,
        evidence=evidence,
    )
    decision = resolve_challenge(memories, request)
    if not decision.applied:
        return TeamReviewActionResult(
            False,
            action,
            challenge_id,
            decision.reason,
            memory=decision.stable_memory,
            challenge_memory=decision.challenge_memory,
            missing=decision.missing or [],
        )
    if decision.outcome_memory is not None:
        store.add(decision.outcome_memory)
    if decision.stable_memory is not None:
        store.update(decision.stable_memory)
    if decision.challenge_memory is not None:
        store.update(decision.challenge_memory)
    return TeamReviewActionResult(
        True,
        action,
        challenge_id,
        decision.reason,
        memory=decision.stable_memory,
        challenge_memory=decision.challenge_memory,
        outcome_memory=decision.outcome_memory,
    )


def apply_narrow_scope_action(
    root: Path | str,
    memory_id: str,
    *,
    approved_by: str,
    proposed_scope: MemoryScope,
    evidence: list[str],
) -> TeamReviewActionResult:
    store = MemoryStore(root)
    memory = next((item for item in store.list() if item.id == memory_id), None)
    if memory is None:
        return TeamReviewActionResult(False, "narrow-scope", memory_id, "memory not found")
    missing: list[str] = []
    if not approved_by.strip():
        missing.append("approved_by")
    if not proposed_scope.flattened():
        missing.append("scope")
    if not evidence:
        missing.append("evidence")
    if missing:
        return TeamReviewActionResult(
            False,
            "narrow-scope",
            memory_id,
            "narrow-scope requires explicit approval, replacement scope, and evidence",
            memory=memory,
            missing=missing,
        )
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and not scope_change_is_safe_narrowing(memory.scope, proposed_scope):
        return TeamReviewActionResult(
            False,
            "narrow-scope",
            memory_id,
            "stable memory narrowing cannot broaden or shift scope; use challenge/split instead",
            memory=memory,
            missing=["safe_narrowing"],
        )
    old_scope = format_scope(memory.scope)
    memory.scope = proposed_scope
    memory.evidence = dedupe(
        memory.evidence
        + [
            f"Scope narrowed by {approved_by.strip()}",
            f"Previous scope: {old_scope}",
            f"New scope: {format_scope(proposed_scope)}",
        ]
        + evidence
    )
    memory.updated_at = utc_now()
    store.update(memory)
    return TeamReviewActionResult(
        True,
        "narrow-scope",
        memory_id,
        "Applied approved safe scope narrowing.",
        memory=memory,
    )


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def format_scope(scope: MemoryScope) -> str:
    values = scope.flattened()
    return ", ".join(values) if values else "none"
