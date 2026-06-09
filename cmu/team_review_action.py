from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .authority import set_memory_authority
from .models import Memory
from .store import MemoryStore
from .team_directory import TeamDirectoryStore, TeamScopeRecord


TEAM_REVIEW_ACTION_VERSION = "cmu-team-review-action/v1"


@dataclass(frozen=True)
class TeamReviewActionResult:
    applied: bool
    action: str
    subject_id: str
    reason: str
    memory: Memory | None = None
    team_scope: TeamScopeRecord | None = None
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
                "Proof Meaning: owner/team handoff cards now have a controlled apply path for authority metadata and team-scope review metadata instead of only command suggestions.",
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
