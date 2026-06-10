from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .models import Memory, MemoryStatus, MemoryType
from .team_directory import TeamScopeRecord, memory_matches_record
from .usage import MemoryUseReceipt


ORG_MEMORY_REVIEW_VERSION = "cmu-org-memory-review/v1"
STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}


@dataclass(frozen=True)
class OrgMemoryItem:
    category: str
    priority: str
    subject_id: str
    title: str
    owner: str
    status: str
    reason: str
    command: str
    repos: list[str] = field(default_factory=list)

    def render(self) -> str:
        repos = f" repos={', '.join(self.repos)}" if self.repos else ""
        return "\n".join(
            [
                f"- [{self.priority}] {self.category}: {self.subject_id} {self.title}",
                f"  Owner: {self.owner or 'unassigned'}{repos}",
                f"  Status: {self.status}",
                f"  Reason: {self.reason}",
                f"  Command: {self.command}",
            ]
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrgMemoryReviewReport:
    owner_filter: str = ""
    items: list[OrgMemoryItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(item.status in {"local-boundary", "review-ready", "evidence-backed-pattern"} for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Organization Memory Review",
            f"Version: {ORG_MEMORY_REVIEW_VERSION}",
            "Mode: read-only multi-repo/team/org expansion gate; no memories, receipts, or team scopes are mutated.",
            f"Filter: owner={self.owner_filter}" if self.owner_filter else "Filter: all owners",
            "",
            "Summary:",
            f"- Items: {len(self.items)}",
            f"- P0: {sum(1 for item in self.items if item.priority == 'P0')}",
            f"- P1: {sum(1 for item in self.items if item.priority == 'P1')}",
            f"- P2: {sum(1 for item in self.items if item.priority == 'P2')}",
            "",
            "Review Items:",
        ]
        lines.extend(item.render() for item in self.items) if self.items else lines.append("- None")
        lines.extend(
            [
                "",
                "Expansion Rules:",
                "- Keep memory local when it matches one repo/team boundary or lacks cross-repo evidence.",
                "- Route multi-repo candidates to the named owner/team before broadening scope.",
                "- Require stable authority owner, role, consequence, and approval before cross-repo Practice/Anchor use.",
                "- Treat organization-level patterns as candidates only after strong linked evidence exists in at least two repo boundaries.",
                "",
                "Proof Meaning: CMU now has a real multi-repo and organization review gate that makes scope expansion, delegated ownership, cross-repo authority, and org-pattern evidence inspectable before memory silently transfers across repositories.",
            ]
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": ORG_MEMORY_REVIEW_VERSION,
                "owner_filter": self.owner_filter,
                "passed": self.passed,
                "items": [item.to_dict() for item in self.items],
            },
            indent=2,
            ensure_ascii=True,
        )


def org_memory_review(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    team_scopes: list[TeamScopeRecord],
    *,
    owner: str = "",
) -> OrgMemoryReviewReport:
    active = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE]
    items: list[OrgMemoryItem] = []
    for memory in active:
        matches = [record for record in team_scopes if memory_matches_record(memory, record)]
        repos = sorted({record.repo for record in matches if record.repo})
        owners = sorted({record.owner for record in matches if record.owner})
        item_owner = memory.authority_owner or first(owners) or first(memory.scope.ownership)
        if owner and owner.lower() not in item_owner.lower():
            continue
        items.extend(boundary_items(memory, receipts, matches, repos, item_owner))
    return OrgMemoryReviewReport(owner_filter=owner, items=sorted(items, key=item_sort_key))


def boundary_items(
    memory: Memory,
    receipts: list[MemoryUseReceipt],
    matches: list[TeamScopeRecord],
    repos: list[str],
    owner: str,
) -> list[OrgMemoryItem]:
    if len(repos) <= 1:
        return [
            OrgMemoryItem(
                category="repo-boundary",
                priority="P2",
                subject_id=memory.id,
                title=memory.title,
                owner=owner,
                status="local-boundary",
                reason="Memory matches one repo/team boundary; keep scope local until evidence supports expansion.",
                command=f"cmu team-scope && cmu use-summary {memory.id}",
                repos=repos,
            )
        ]

    items = [
        OrgMemoryItem(
            category="multi-repo-boundary",
            priority="P1",
            subject_id=memory.id,
            title=memory.title,
            owner=owner,
            status="review-ready",
            reason=f"Memory overlaps {len(repos)} repo boundaries; delegate review before broadening or transferring scope.",
            command=f"cmu org-memory-review --owner {quote(owner or '<owner>')}",
            repos=repos,
        )
    ]
    items.append(cross_repo_authority_item(memory, repos, owner))
    pattern = org_pattern_item(memory, receipts, matches, repos, owner)
    if pattern is not None:
        items.append(pattern)
    return items


def cross_repo_authority_item(memory: Memory, repos: list[str], owner: str) -> OrgMemoryItem:
    missing = [
        name
        for name, value in [
            ("authority_owner", memory.authority_owner),
            ("authority_role", memory.authority_role),
            ("authority_consequence", memory.authority_consequence),
            ("approved_by", memory.approved_by),
        ]
        if not value
    ]
    if memory.type not in STABLE_TYPES:
        return OrgMemoryItem(
            category="cross-repo-authority",
            priority="P1",
            subject_id=memory.id,
            title=memory.title,
            owner=owner,
            status="candidate-review",
            reason="Cross-repo non-stable memory must stay review-bound until promoted with explicit authority.",
            command=f"cmu review {memory.id} --to practice",
            repos=repos,
        )
    if missing:
        return OrgMemoryItem(
            category="cross-repo-authority",
            priority="P0",
            subject_id=memory.id,
            title=memory.title,
            owner=owner,
            status="blocked-missing-authority",
            reason="Cross-repo stable memory is missing authority metadata: " + ", ".join(missing),
            command=f"cmu authority-set {memory.id} --owner <owner-or-team> --approved-by <owner-or-team> --approver-role org --consequence high",
            repos=repos,
        )
    return OrgMemoryItem(
        category="cross-repo-authority",
        priority="P1",
        subject_id=memory.id,
        title=memory.title,
        owner=owner,
        status="review-ready",
        reason="Cross-repo stable memory has explicit authority; keep review evidence current before expanding further.",
        command=f"cmu use-review {memory.id}",
        repos=repos,
    )


def org_pattern_item(
    memory: Memory,
    receipts: list[MemoryUseReceipt],
    matches: list[TeamScopeRecord],
    repos: list[str],
    owner: str,
) -> OrgMemoryItem | None:
    strong_repos = strong_use_repos(memory, receipts, matches)
    if len(strong_repos) < 2:
        return OrgMemoryItem(
            category="org-pattern",
            priority="P1",
            subject_id=memory.id,
            title=memory.title,
            owner=owner,
            status="needs-cross-repo-evidence",
            reason="Organization-level pattern is not earned until strong linked use appears in at least two repo boundaries.",
            command=f"cmu use-review {memory.id}",
            repos=repos,
        )
    return OrgMemoryItem(
        category="org-pattern",
        priority="P1",
        subject_id=memory.id,
        title=memory.title,
        owner=owner,
        status="evidence-backed-pattern",
        reason=f"Strong linked evidence appears in {len(strong_repos)} repo boundaries: {', '.join(strong_repos)}.",
        command=f"cmu lifecycle-scope-record {memory.id} --reason <org-pattern-evidence> --requested-by {quote(owner or '<owner>')}",
        repos=strong_repos,
    )


def strong_use_repos(memory: Memory, receipts: list[MemoryUseReceipt], matches: list[TeamScopeRecord]) -> list[str]:
    strong = [receipt for receipt in receipts if receipt.memory_id == memory.id and is_strong_receipt(receipt)]
    repos: set[str] = set()
    for receipt in strong:
        for record in matches:
            if overlaps(receipt.files, record.code) or overlaps(receipt.workflow, record.workflow):
                repos.add(record.repo)
    return sorted(repo for repo in repos if repo)


def is_strong_receipt(receipt: MemoryUseReceipt) -> bool:
    return (
        receipt.outcome_signal in {"committed", "checkpoint"}
        and receipt.link_confidence >= 0.7
        and "mixed_commit" not in receipt.flags
        and "drag" not in receipt.flags
    )


def overlaps(left: list[str], right: list[str]) -> bool:
    normalized_right = [item.lower() for item in right]
    return any(item.lower() in normalized_right for item in left)


def first(values: list[str]) -> str:
    return values[0] if values else ""


def quote(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def item_sort_key(item: OrgMemoryItem) -> tuple[int, str, str]:
    return {"P0": 0, "P1": 1, "P2": 2}.get(item.priority, 9), item.category, item.title.lower()
