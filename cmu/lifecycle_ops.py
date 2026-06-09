from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .authority import STABLE_TYPES, role_can_approve
from .challenges import copy_scope
from .json_store import update_json
from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from .promotion import review_promotion
from .usage import scope_change_is_safe_narrowing


LIFECYCLE_OPS_VERSION = "cmu-lifecycle-ops/v1"


@dataclass(frozen=True)
class LifecycleOpItem:
    memory_id: str
    title: str
    action: str
    status: str
    reason: str
    command: str = ""

    def render(self) -> str:
        line = f"- {self.status}: {self.memory_id} {self.title} action={self.action} - {self.reason}"
        if self.command:
            line += f"\n  Command: {self.command}"
        return line


@dataclass
class LifecycleOpReport:
    title: str
    mode: str
    applied: bool = False
    ok: bool = True
    items: list[LifecycleOpItem] = field(default_factory=list)
    changed_memories: list[Memory] = field(default_factory=list)
    created_memories: list[Memory] = field(default_factory=list)
    archive_file: Path | None = None

    def render(self) -> str:
        lines = [
            self.title,
            f"Version: {LIFECYCLE_OPS_VERSION}",
            f"Mode: {self.mode}",
            f"Summary: total={len(self.items)} applied={'yes' if self.applied else 'no'} ok={'yes' if self.ok else 'no'}",
            "",
            "Lifecycle Items:",
        ]
        lines.extend(item.render() for item in self.items)
        if not self.items:
            lines.append("- None")
        if self.archive_file is not None:
            lines.append(f"Archive File: {self.archive_file}")
        lines.extend(
            [
                "",
                "Proof Meaning: lifecycle operations now have controlled proposal, merge, demotion, archival, and scope-change record paths instead of only read-only inspection.",
            ]
        )
        return "\n".join(lines)


def lifecycle_proposals(memories: list[Memory], *, target: str = "all", limit: int = 50) -> LifecycleOpReport:
    normalized_target = target.strip().lower() or "all"
    items: list[LifecycleOpItem] = []
    for memory in [item for item in memories if item.type == MemoryType.SITUATION and item.status == MemoryStatus.ACTIVE][
        : max(1, limit)
    ]:
        targets = [MemoryType.PRACTICE, MemoryType.ANCHOR]
        if normalized_target != "all":
            targets = [MemoryType(normalized_target)]
        for proposed in targets:
            review = review_promotion(memories, memory.id, proposed)
            if review.gate_passed:
                items.append(
                    LifecycleOpItem(
                        memory_id=memory.id,
                        title=memory.title,
                        action=f"propose-{proposed.value}",
                        status="ready",
                        reason="Situation passes proposal gate and needs explicit authority approval.",
                        command=f"cmu review {memory.id} --to {proposed.value}",
                    )
                )
            else:
                items.append(
                    LifecycleOpItem(
                        memory_id=memory.id,
                        title=memory.title,
                        action=f"propose-{proposed.value}",
                        status="blocked",
                        reason=f"missing {format_list(review.missing)}",
                        command=f"cmu review {memory.id} --to {proposed.value}",
                    )
                )
    return LifecycleOpReport(
        title="CMU Lifecycle Stable Proposal Workbench",
        mode="read-only assisted Situation -> Practice/Anchor proposal generation.",
        items=items,
    )


def lifecycle_merge(
    memories: list[Memory],
    *,
    target_id: str,
    source_id: str,
    reason: str,
    approved_by: str,
    apply: bool = False,
) -> LifecycleOpReport:
    target = find_memory(memories, target_id)
    source = find_memory(memories, source_id)
    missing = []
    if target.id == source.id:
        missing.append("different_source_and_target")
    if not reason.strip():
        missing.append("reason")
    if not approved_by.strip():
        missing.append("approved_by")
    if target.status != MemoryStatus.ACTIVE or source.status != MemoryStatus.ACTIVE:
        missing.append("active_memories")
    if missing:
        return blocked_report("CMU Lifecycle Merge", "controlled merge preview/apply.", target, "merge", missing)
    item_status = "merged" if apply else "would-merge"
    if apply:
        target.signals = dedupe(target.signals + source.signals)
        target.evidence = dedupe(
            target.evidence
            + source.evidence
            + [
                f"Merged memory: {source.id}",
                f"Merge approved by: {approved_by.strip()}",
                f"Merge reason: {reason.strip()}",
            ]
        )
        target.relationships = dedupe_relationships(
            target.relationships
            + source.relationships
            + [MemoryRelationship(type=MemoryRelationType.DERIVED_FROM, target_id=source.id, reason=reason.strip())]
        )
        target.confidence = max(target.confidence, source.confidence)
        source.status = MemoryStatus.RETIRED
        source.evidence = dedupe(
            source.evidence
            + [
                f"Merged into memory: {target.id}",
                f"Merge approved by: {approved_by.strip()}",
                f"Merge reason: {reason.strip()}",
            ]
        )
    return LifecycleOpReport(
        title="CMU Lifecycle Merge",
        mode="controlled merge; source memory is retired only with --apply.",
        applied=apply,
        items=[
            LifecycleOpItem(target.id, target.title, "merge-target", item_status, f"source={source.id}; {reason.strip()}"),
            LifecycleOpItem(source.id, source.title, "merge-source", item_status, f"target={target.id}; {reason.strip()}"),
        ],
        changed_memories=[target, source] if apply else [],
    )


def lifecycle_demote(
    memories: list[Memory],
    *,
    memory_id: str,
    reason: str,
    approved_by: str = "",
    approver_role: str = "",
    apply: bool = False,
) -> LifecycleOpReport:
    memory = find_memory(memories, memory_id)
    missing = []
    if memory.type == MemoryType.CANDIDATE:
        missing.append("demotable_type")
    if not reason.strip():
        missing.append("reason")
    if memory.type in STABLE_TYPES:
        consequence = memory.authority_consequence or ("critical" if memory.type == MemoryType.ANCHOR else "high")
        if not approved_by.strip() or not role_can_approve(approver_role, consequence):
            missing.append(f"sufficient_{consequence}_authority")
    if missing:
        return blocked_report("CMU Lifecycle Demotion", "controlled demotion preview/apply.", memory, "demote", missing)
    old_type = memory.type
    new_type = demoted_type(memory.type)
    if apply:
        memory.type = new_type
        memory.confidence = round(max(0.05, memory.confidence - 0.1), 2)
        if old_type in STABLE_TYPES:
            memory.approved_by = ""
            memory.authority_role = ""
            memory.authority_approved_at = ""
        memory.evidence = dedupe(
            memory.evidence
            + [
                f"Lifecycle demotion: {old_type.value} -> {new_type.value}",
                f"Demotion reason: {reason.strip()}",
                *([f"Demotion approved by: {approved_by.strip()} ({approver_role.strip().lower()})"] if approved_by.strip() else []),
            ]
        )
    return LifecycleOpReport(
        title="CMU Lifecycle Demotion",
        mode="controlled demotion; stable memory requires sufficient authority.",
        applied=apply,
        items=[
            LifecycleOpItem(
                memory.id,
                memory.title,
                "demote",
                "demoted" if apply else "would-demote",
                f"{old_type.value} -> {new_type.value}; {reason.strip()}",
            )
        ],
        changed_memories=[memory] if apply else [],
    )


def lifecycle_archive(
    memories: list[Memory],
    *,
    root: str | Path,
    memory_id: str = "",
    apply: bool = False,
) -> LifecycleOpReport:
    retired = [memory for memory in memories if memory.status == MemoryStatus.RETIRED and (not memory_id or memory.id == memory_id)]
    archive_file = Path(root) / ".cmu" / "memory_archive.json"
    items = [
        LifecycleOpItem(
            memory.id,
            memory.title,
            "archive",
            "archived" if apply else "would-archive",
            "retired memory copied into durable local archive.",
        )
        for memory in retired
    ]
    if apply and retired:
        write_archive_records(archive_file, retired)
    return LifecycleOpReport(
        title="CMU Lifecycle Archive",
        mode="controlled archival of retired memories; active memories are never archived by this path.",
        applied=apply,
        items=items,
        archive_file=archive_file,
    )


def lifecycle_scope_record(
    memories: list[Memory],
    *,
    memory_id: str,
    proposed_scope: MemoryScope,
    reason: str,
    requested_by: str,
    apply: bool = False,
) -> LifecycleOpReport:
    memory = find_memory(memories, memory_id)
    missing = []
    if not proposed_scope.flattened():
        missing.append("proposed_scope")
    if not reason.strip():
        missing.append("reason")
    if not requested_by.strip():
        missing.append("requested_by")
    if memory.type in STABLE_TYPES and proposed_scope.flattened() and scope_change_is_safe_narrowing(memory.scope, proposed_scope):
        missing.append("broad_or_ambiguous_scope_change")
    if missing:
        return blocked_report(
            "CMU Lifecycle Scope Change Record",
            "records broad or ambiguous scope changes as Candidate review records.",
            memory,
            "scope-record",
            missing,
        )
    candidate = Memory.create(
        type=MemoryType.CANDIDATE,
        title=f"Scope change proposal for {memory.title}",
        summary=f"Proposed scope change for {memory.id}: {reason.strip()}",
        signals=["scope change proposal", f"{memory.type.value} scope review"],
        scope=copy_scope(proposed_scope),
        evidence=[
            f"Scope change target: {memory.id}",
            f"Current scope: {format_scope(memory.scope)}",
            f"Proposed scope: {format_scope(proposed_scope)}",
            f"Requested by: {requested_by.strip()}",
        ],
        use_this_path="Review this Candidate before expanding, shifting, or materially changing memory scope.",
        avoid_this="Do not silently broaden stable memory scope from this record.",
        challenge_only_if="Apply only through an explicit approved challenge, split, or authority review path.",
        liability_score=memory.liability_score,
        confidence=0.65,
    )
    return LifecycleOpReport(
        title="CMU Lifecycle Scope Change Record",
        mode="controlled Candidate record for broad or ambiguous scope changes.",
        applied=apply,
        items=[
            LifecycleOpItem(
                memory.id,
                memory.title,
                "scope-record",
                "recorded" if apply else "would-record",
                f"proposed={format_scope(proposed_scope)}",
            )
        ],
        created_memories=[candidate] if apply else [],
    )


def write_archive_records(path: Path, memories: list[Memory]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def update(data: dict) -> dict:
        records = data.setdefault("archived_memories", [])
        existing = {item.get("id") for item in records}
        for memory in memories:
            if memory.id not in existing:
                records.append(memory.to_dict())
                existing.add(memory.id)
        return data

    update_json(path, {"version": 1, "archived_memories": []}, update)


def blocked_report(title: str, mode: str, memory: Memory, action: str, missing: list[str]) -> LifecycleOpReport:
    return LifecycleOpReport(
        title=title,
        mode=mode,
        ok=False,
        items=[
            LifecycleOpItem(
                memory.id,
                memory.title,
                action,
                "blocked",
                f"missing {format_list(missing)}",
            )
        ],
    )


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def demoted_type(memory_type: MemoryType) -> MemoryType:
    if memory_type in STABLE_TYPES:
        return MemoryType.SITUATION
    if memory_type in {MemoryType.SITUATION, MemoryType.EXCEPTION, MemoryType.ANTI_PATTERN, MemoryType.QUESTION}:
        return MemoryType.CANDIDATE
    return MemoryType.CANDIDATE


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def dedupe_relationships(relationships: list[MemoryRelationship]) -> list[MemoryRelationship]:
    seen: set[tuple[str, str, str]] = set()
    cleaned: list[MemoryRelationship] = []
    for relationship in relationships:
        key = (relationship.type.value, relationship.target_id, relationship.reason)
        if key not in seen:
            seen.add(key)
            cleaned.append(relationship)
    return cleaned


def format_scope(scope: MemoryScope) -> str:
    return format_list(scope.flattened())


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
