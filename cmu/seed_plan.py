from __future__ import annotations

from dataclasses import dataclass, field

from .doc_curation import DocumentCurationDecision
from .models import Memory, MemoryRelationType, MemoryStatus, MemoryType
from .promotion import review_promotion
from .readiness import readiness_report
from .usage import MemoryUseReceipt


@dataclass(frozen=True)
class SeedPlanItem:
    priority: int
    category: str
    subject_id: str
    title: str
    reason: str
    command: str

    def render(self) -> str:
        subject = self.subject_id if self.subject_id else "memory-base"
        title = f" {self.title}" if self.title else ""
        return "\n".join(
            [
                f"- P{self.priority} {self.category}: {subject}{title}",
                f"  Reason: {self.reason}",
                f"  Command: {self.command}",
            ]
        )


@dataclass
class SeedPlanReport:
    memories_reviewed: int
    receipts_reviewed: int
    doc_decisions_reviewed: int = 0
    items: list[SeedPlanItem] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Memory Seeding Plan",
            "Mode: read-only workbench; no memories, receipts, relationships, or authority metadata are mutated.",
            "",
            "Summary:",
            f"- Memories Reviewed: {self.memories_reviewed}",
            f"- Use Receipts Reviewed: {self.receipts_reviewed}",
            f"- Document Curation Decisions Reviewed: {self.doc_decisions_reviewed}",
            f"- Suggested Actions: {len(self.items)}",
            f"- Critical/First: {sum(1 for item in self.items if item.priority == 0)}",
            f"- High Priority: {sum(1 for item in self.items if item.priority == 1)}",
            f"- Medium Priority: {sum(1 for item in self.items if item.priority == 2)}",
            "",
            "Seed Queue:",
        ]
        if not self.items:
            lines.append("- None")
        else:
            for item in self.items:
                lines.append(item.render())
        lines.extend(
            [
                "",
                "Proof Meaning: this plan bridges cautious document curation and governed memory seeding without silently promoting, broadening, or linking memory.",
            ]
        )
        return "\n".join(lines)


def seed_plan_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    doc_decisions: list[DocumentCurationDecision] | None = None,
) -> SeedPlanReport:
    active_memories = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE]
    doc_decisions = doc_decisions or []
    items: list[SeedPlanItem] = []
    items.extend(candidate_promotion_items(active_memories))
    items.extend(coverage_items(active_memories, receipts))
    items.extend(rejected_document_items(doc_decisions))
    items.extend(graph_suggestion_items(active_memories))
    items = sorted(items, key=lambda item: (item.priority, item.category, item.subject_id, item.title))
    return SeedPlanReport(
        memories_reviewed=len(active_memories),
        receipts_reviewed=len(receipts),
        doc_decisions_reviewed=len(doc_decisions),
        items=items,
    )


def candidate_promotion_items(memories: list[Memory]) -> list[SeedPlanItem]:
    items: list[SeedPlanItem] = []
    for memory in memories:
        if memory.type != MemoryType.CANDIDATE:
            continue
        review = review_promotion(memories, memory.id, MemoryType.SITUATION)
        if review.gate_passed:
            command = f"cmu review {memory.id} --to situation && cmu promote {memory.id} --to situation"
            reason = "Candidate has the required fields for Situation review; still inspect before promotion."
            priority = 1
        else:
            command = f"cmu review {memory.id} --to situation"
            missing = ", ".join(review.missing) if review.missing else f"duplicate of {review.duplicate.id}"
            reason = f"Candidate is not ready for Situation promotion: {missing}"
            priority = 2
        items.append(
            SeedPlanItem(
                priority=priority,
                category="promotion",
                subject_id=memory.id,
                title=memory.title,
                reason=reason,
                command=command,
            )
        )
    return items


def coverage_items(memories: list[Memory], receipts: list[MemoryUseReceipt]) -> list[SeedPlanItem]:
    report = readiness_report(memories, receipts)
    items: list[SeedPlanItem] = []
    for issue in report.issues:
        if issue.category != "coverage":
            continue
        if issue.subject_id == "anti-pattern":
            command = (
                "cmu add --type anti-pattern --title <title> --summary <tempting mistake> "
                "--avoid <unsafe path> --use-path <safer path> --evidence <evidence>"
            )
        elif issue.subject_id == "question":
            command = (
                "cmu add --type question --title <title> --summary <unresolved costly uncertainty> "
                "--challenge <resolution condition> --evidence <why it matters>"
            )
        else:
            command = issue.next_action
        items.append(
            SeedPlanItem(
                priority=1,
                category="coverage",
                subject_id=issue.subject_id,
                title=issue.title,
                reason=issue.state,
                command=command,
            )
        )
    return items


def rejected_document_items(decisions: list[DocumentCurationDecision]) -> list[SeedPlanItem]:
    items: list[SeedPlanItem] = []
    for decision in decisions:
        markers = set(decision.evidence.reusable_markers)
        if decision.status in {"stale-rejected", "superseded-rejected"} and markers & {"drag", "anti-pattern"}:
            items.append(
                SeedPlanItem(
                    priority=1,
                    category="anti-pattern-draft",
                    subject_id=decision.evidence.path,
                    title=decision.evidence.title,
                    reason="Rejected markdown still describes a tempting source of memory drag; draft warning manually instead of importing the doc.",
                    command=(
                        "cmu add --type anti-pattern --title <stale-doc drag warning> "
                        "--summary <tempting stale import> --avoid <blind markdown import> "
                        "--use-path <curate against current evidence> --evidence "
                        f"\"Rejected doc-curate source: {decision.evidence.path}\""
                    ),
                )
            )
        if markers & {"question", "known gap", "unfinished"}:
            items.append(
                SeedPlanItem(
                    priority=2,
                    category="question-draft",
                    subject_id=decision.evidence.path,
                    title=decision.evidence.title,
                    reason="Document exposes unresolved uncertainty; keep it as a Question unless current evidence resolves it.",
                    command=(
                        "cmu add --type question --title <open question> "
                        "--summary <unresolved costly uncertainty> --challenge <resolution condition> "
                        f"--evidence \"Doc-curate source: {decision.evidence.path}\""
                    ),
                )
            )
    return items


def graph_suggestion_items(memories: list[Memory]) -> list[SeedPlanItem]:
    candidates = [
        memory
        for memory in memories
        if memory.type in {MemoryType.CANDIDATE, MemoryType.SITUATION, MemoryType.PRACTICE}
    ]
    anti_patterns = [memory for memory in memories if memory.type == MemoryType.ANTI_PATTERN]
    questions = [memory for memory in memories if memory.type == MemoryType.QUESTION]
    items: list[SeedPlanItem] = []
    for anti_pattern in anti_patterns:
        target = first_unlinked_target(anti_pattern, candidates)
        if target is not None:
            items.append(
                SeedPlanItem(
                    priority=2,
                    category="graph",
                    subject_id=anti_pattern.id,
                    title=anti_pattern.title,
                    reason="Anti-Pattern should point at the safer related practice/situation before graph retrieval can explain the path.",
                    command=(
                        f"cmu relate {anti_pattern.id} --type {MemoryRelationType.RELATED_PRACTICE.value} "
                        f"--target {target.id} --reason <why this safer path replaces the trap>"
                    ),
                )
            )
    for question in questions:
        target = first_unlinked_target(question, candidates)
        if target is not None:
            items.append(
                SeedPlanItem(
                    priority=3,
                    category="graph",
                    subject_id=question.id,
                    title=question.title,
                    reason="Question should be connected to the nearest situation/practice it can affect.",
                    command=(
                        f"cmu relate {question.id} --type {MemoryRelationType.RELATED_PRACTICE.value} "
                        f"--target {target.id} --reason <why this uncertainty affects the target memory>"
                    ),
                )
            )
    return items


def first_unlinked_target(source: Memory, targets: list[Memory]) -> Memory | None:
    existing_targets = {relationship.target_id for relationship in source.relationships}
    for target in targets:
        if target.id == source.id:
            continue
        if target.id not in existing_targets:
            return target
    return None
