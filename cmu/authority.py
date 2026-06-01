from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Memory, MemoryStatus, MemoryType, utc_now


STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}
ROLE_RANK = {"agent": 0, "member": 1, "owner": 2, "team": 3, "org": 4}
CONSEQUENCE_ROLE = {"low": "agent", "medium": "member", "high": "owner", "critical": "org"}


@dataclass
class AuthorityCard:
    memory_id: str
    title: str
    memory_type: str
    owner: str
    approver: str
    approver_role: str
    consequence: str
    permission: str
    review_state: str
    state: str
    next_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [{self.memory_type}] {self.title}",
                f"  Owner: {self.owner}",
                f"  Approval: {self.approver}",
                f"  Approver Role: {self.approver_role}",
                f"  Consequence: {self.consequence}",
                f"  Permission: {self.permission}",
                f"  Review Expiry: {self.review_state}",
                f"  State: {self.state}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class AuthorityReport:
    cards: list[AuthorityCard] = field(default_factory=list)
    memory_filter: str = ""
    include_all: bool = False

    def render(self) -> str:
        lines = [
            "CMU Team and Authority Model",
            "Mode: read-only authority proof; use authority-set for explicit controlled metadata changes.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Policy:",
                "- low consequence -> agent or higher",
                "- medium consequence -> member or higher",
                "- high consequence -> owner or higher",
                "- critical consequence -> org authority",
                "",
                "Summary:",
                f"- Memories Reviewed: {len(self.cards)}",
                f"- Current Authority: {sum(1 for card in self.cards if card.state == 'current authority')}",
                f"- Legacy Metadata: {sum(1 for card in self.cards if card.state == 'legacy approval metadata')}",
                f"- Missing Authority: {sum(1 for card in self.cards if card.state == 'missing authority')}",
                f"- Expired Reviews: {sum(1 for card in self.cards if card.state == 'review expired')}",
                f"- Permission Blocks: {sum(1 for card in self.cards if card.state == 'permission blocked')}",
                "",
                "Authority Cards:",
            ]
        )
        lines.extend(card.render() for card in self.cards)
        if not self.cards:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: authority is now inspectable as ownership, consequence, approver permission, and review expiry instead of a single approval label.",
            ]
        )
        return "\n".join(lines)


@dataclass
class AuthorityDecision:
    applied: bool
    reason: str
    memory: Memory | None = None
    missing: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.applied or self.memory is None:
            lines = ["CMU Authority Assignment Not Applied", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {', '.join(self.missing)}")
            return "\n".join(lines)
        return "\n".join(
            [
                "CMU Authority Assignment Applied",
                f"Memory: {self.memory.id} [{self.memory.type.value}] {self.memory.title}",
                f"Owner: {self.memory.authority_owner}",
                f"Approved By: {self.memory.approved_by}",
                f"Approver Role: {self.memory.authority_role}",
                f"Consequence: {self.memory.authority_consequence}",
                f"Review Due: {self.memory.authority_review_due_at or 'not set'}",
                f"Reason: {self.reason}",
            ]
        )


def authority_report(memories: list[Memory], *, memory_id: str = "", include_all: bool = False) -> AuthorityReport:
    filtered = [
        memory
        for memory in memories
        if (include_all or memory.type in STABLE_TYPES) and (not memory_id or memory.id == memory_id)
    ]
    return AuthorityReport(
        cards=[authority_card(memory) for memory in sorted(filtered, key=lambda item: (item.type.value, item.title))],
        memory_filter=memory_id,
        include_all=include_all,
    )


def authority_card(memory: Memory, *, now: datetime | None = None) -> AuthorityCard:
    consequence = memory_consequence(memory)
    role = memory.authority_role or ("legacy-unspecified" if memory.approved_by else "none")
    permission = permission_summary(role, consequence)
    review_state = review_expiry_state(memory, now=now)
    state, next_action = authority_state(memory, now=now)
    return AuthorityCard(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        owner=memory.authority_owner or format_scope_owner(memory),
        approver=memory.approved_by or "none",
        approver_role=role,
        consequence=consequence,
        permission=permission,
        review_state=review_state,
        state=state,
        next_action=next_action,
    )


def set_memory_authority(
    memory: Memory,
    *,
    owner: str,
    approved_by: str,
    approver_role: str,
    consequence: str,
    review_due_at: str = "",
) -> AuthorityDecision:
    missing = [
        name
        for name, value in [
            ("owner", owner),
            ("approved_by", approved_by),
            ("approver_role", approver_role),
            ("consequence", consequence),
        ]
        if not value.strip()
    ]
    if missing:
        return AuthorityDecision(applied=False, reason="authority assignment requires ownership, approver, role, and consequence", memory=memory, missing=missing)
    role = normalize_role(approver_role)
    level = normalize_consequence(consequence)
    if role not in ROLE_RANK:
        return AuthorityDecision(applied=False, reason=f"unsupported authority role: {approver_role}", memory=memory)
    if level not in CONSEQUENCE_ROLE:
        return AuthorityDecision(applied=False, reason=f"unsupported consequence level: {consequence}", memory=memory)
    if not role_can_approve(role, level):
        return AuthorityDecision(
            applied=False,
            reason=f"{role} authority cannot approve {level} consequence memory; requires {CONSEQUENCE_ROLE[level]} or higher",
            memory=memory,
        )
    if review_due_at.strip() and parse_iso(review_due_at) is None:
        return AuthorityDecision(applied=False, reason="review_due_at must be an ISO-8601 timestamp", memory=memory)
    memory.authority_owner = owner.strip()
    memory.approved_by = approved_by.strip()
    memory.authority_role = role
    memory.authority_consequence = level
    memory.authority_approved_at = utc_now()
    memory.authority_review_due_at = review_due_at.strip()
    memory.evidence = dedupe(
        memory.evidence
        + [
            f"Authority owner: {memory.authority_owner}",
            f"Authority approval: {memory.approved_by} ({memory.authority_role})",
            f"Authority consequence: {memory.authority_consequence}",
        ]
    )
    return AuthorityDecision(applied=True, reason="Stored explicit consequence-based authority metadata.", memory=memory)


def authority_state(memory: Memory, *, now: datetime | None = None) -> tuple[str, str]:
    if memory.status == MemoryStatus.RETIRED:
        return "retired", "keep authority metadata as history"
    if not memory.approved_by:
        return "missing authority", "assign an accountable owner and permitted approver before broader stable trust"
    if review_is_expired(memory, now=now):
        return "review expired", "review and renew authority before treating this memory as settled"
    if not memory.authority_role or not memory.authority_consequence or not memory.authority_owner:
        return "legacy approval metadata", "enrich the legacy approval with owner, consequence, role, and optional review expiry"
    if not role_can_approve(memory.authority_role, memory.authority_consequence):
        return "permission blocked", f"reassign approval with {CONSEQUENCE_ROLE[memory.authority_consequence]} authority or higher"
    return "current authority", "follow within scope and renew authority before review expiry"


def role_can_approve(role: str, consequence: str) -> bool:
    normalized_role = normalize_role(role)
    normalized_consequence = normalize_consequence(consequence)
    return (
        normalized_role in ROLE_RANK
        and normalized_consequence in CONSEQUENCE_ROLE
        and ROLE_RANK[normalized_role] >= ROLE_RANK[CONSEQUENCE_ROLE[normalized_consequence]]
    )


def memory_consequence(memory: Memory) -> str:
    if memory.authority_consequence:
        return normalize_consequence(memory.authority_consequence)
    if memory.type == MemoryType.ANCHOR or memory.liability_score >= 5:
        return "critical"
    if memory.type == MemoryType.PRACTICE or memory.liability_score >= 4:
        return "high"
    if memory.liability_score >= 2:
        return "medium"
    return "low"


def review_is_expired(memory: Memory, *, now: datetime | None = None) -> bool:
    due = parse_iso(memory.authority_review_due_at)
    if due is None:
        return False
    return due < (now or datetime.now(timezone.utc))


def review_expiry_state(memory: Memory, *, now: datetime | None = None) -> str:
    if not memory.authority_review_due_at:
        return "not set"
    if review_is_expired(memory, now=now):
        return f"expired at {memory.authority_review_due_at}"
    return f"current until {memory.authority_review_due_at}"


def permission_summary(role: str, consequence: str) -> str:
    if role == "legacy-unspecified":
        return "legacy approval accepted; explicit role not yet recorded"
    if role == "none":
        return f"blocked; {CONSEQUENCE_ROLE[consequence]} or higher required"
    if role_can_approve(role, consequence):
        return f"allowed; {role} satisfies {consequence} consequence"
    return f"blocked; {CONSEQUENCE_ROLE[consequence]} or higher required"


def normalize_role(value: str) -> str:
    return value.strip().lower()


def normalize_consequence(value: str) -> str:
    return value.strip().lower()


def parse_iso(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_scope_owner(memory: Memory) -> str:
    return ", ".join(memory.scope.ownership) if memory.scope.ownership else "none"


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))
