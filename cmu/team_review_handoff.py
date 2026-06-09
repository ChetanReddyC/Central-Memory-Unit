from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory, MemoryStatus, MemoryType
from .team_directory import TeamScopeRecord, coverage_for_record, format_list


TEAM_REVIEW_HANDOFF_VERSION = "cmu-team-review-handoff/v1"
STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}


@dataclass(frozen=True)
class TeamReviewHandoffCard:
    priority: str
    category: str
    subject_id: str
    owner: str
    title: str
    reason: str
    command: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- [{self.priority}] {self.category}: {self.subject_id} {self.title}",
                f"  Owner: {self.owner or 'unassigned'}",
                f"  Reason: {self.reason}",
                f"  Command: {self.command}",
            ]
        )


@dataclass
class TeamReviewHandoffReport:
    cards: list[TeamReviewHandoffCard] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.cards) and all(card.command and card.owner for card in self.cards if card.priority in {"P0", "P1"})

    def render(self) -> str:
        lines = [
            "CMU Team Review Handoffs",
            f"Version: {TEAM_REVIEW_HANDOFF_VERSION}",
            "Mode: read-only owner/team handoff cards; no memories, authority metadata, or team scopes are mutated.",
            "",
            "Summary:",
            f"- Cards: {len(self.cards)}",
            f"- P0: {sum(1 for card in self.cards if card.priority == 'P0')}",
            f"- P1: {sum(1 for card in self.cards if card.priority == 'P1')}",
            f"- P2: {sum(1 for card in self.cards if card.priority == 'P2')}",
            "",
            "Handoffs:",
        ]
        lines.extend(card.render() for card in self.cards) if self.cards else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: owner and team review now has a focused handoff surface with exact follow-up commands instead of forcing humans to infer next steps from broad diagnostics.",
            ]
        )
        return "\n".join(lines)


def team_review_handoffs(memories: list[Memory], team_scopes: list[TeamScopeRecord]) -> TeamReviewHandoffReport:
    cards: list[TeamReviewHandoffCard] = []
    active = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE]
    for record in team_scopes:
        coverage = coverage_for_record(record, active)
        if coverage.missing_axes:
            cards.append(
                TeamReviewHandoffCard(
                    priority="P1",
                    category="team-scope-metadata",
                    subject_id=record.id,
                    owner=record.owner,
                    title=f"{record.repo}/{record.team}",
                    reason="Team boundary is missing review metadata: " + format_list(coverage.missing_axes),
                    command=f"cmu team-scope-add --repo {quote(record.repo)} --team {quote(record.team)} --owner {quote(record.owner or '<owner>')} --authority-role owner --consequence high",
                )
            )
        if not coverage.matched_memory_ids:
            cards.append(
                TeamReviewHandoffCard(
                    priority="P1",
                    category="team-scope-coverage",
                    subject_id=record.id,
                    owner=record.owner,
                    title=f"{record.repo}/{record.team}",
                    reason="No active memory is scoped to this repo/team boundary.",
                    command="cmu seed-plan --doc <evidence-doc> or cmu add --type situation --scope-owner "
                    + quote(record.owner or "<owner>")
                    + " --scope-code <path>",
                )
            )
    stable_memories = [memory for memory in active if memory.type in STABLE_TYPES]
    for memory in stable_memories:
        if not memory.authority_owner or not memory.authority_role or not memory.authority_consequence:
            cards.append(
                TeamReviewHandoffCard(
                    priority="P0" if memory.liability_score >= 4 else "P1",
                    category="stable-authority-handoff",
                    subject_id=memory.id,
                    owner=memory.authority_owner or first(memory.scope.ownership),
                    title=memory.title,
                    reason="Stable memory needs explicit owner, approver role, and consequence metadata.",
                    command=f"cmu authority-set {memory.id} --owner <owner-or-team> --approved-by <owner-or-team> --approver-role owner --consequence high --review-due <iso-date>",
                )
            )
    return TeamReviewHandoffReport(cards=sorted(cards, key=sort_key))


def sort_key(card: TeamReviewHandoffCard) -> tuple[int, str, str]:
    return {"P0": 0, "P1": 1, "P2": 2}.get(card.priority, 9), card.category, card.title.lower()


def first(values: list[str]) -> str:
    return values[0] if values else ""


def quote(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value
