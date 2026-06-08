from __future__ import annotations

from dataclasses import dataclass, field

from .governance import governance_report
from .graphview import graph_memory_view_report
from .models import Memory, MemoryStatus, MemoryType
from .quality import quality_report
from .usage import MemoryUseReceipt


STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}


@dataclass(frozen=True)
class ReadinessIssue:
    severity: int
    category: str
    subject_id: str
    title: str
    state: str
    evidence: str
    next_action: str

    def render(self) -> str:
        subject = self.subject_id if self.subject_id else "memory-base"
        title = f" {self.title}" if self.title else ""
        return "\n".join(
            [
                f"- P{self.severity} {self.category}: {subject}{title}",
                f"  State: {self.state}",
                f"  Evidence: {self.evidence}",
                f"  Next: {self.next_action}",
            ]
        )


@dataclass
class ReadinessReport:
    memories_reviewed: int
    receipts_reviewed: int
    stable_memories: int
    anti_patterns: int
    questions: int
    issues: list[ReadinessIssue] = field(default_factory=list)
    include_retired: bool = False

    def render(self) -> str:
        lines = [
            "CMU Memory Base Readiness",
            "Mode: read-only cleanup/readiness workflow; no memories or receipts are mutated.",
            f"History: {'active + retired' if self.include_retired else 'active only'}",
            "",
            "Summary:",
            f"- Memories Reviewed: {self.memories_reviewed}",
            f"- Use Receipts Reviewed: {self.receipts_reviewed}",
            f"- Stable Memories: {self.stable_memories}",
            f"- Anti-Patterns: {self.anti_patterns}",
            f"- Questions: {self.questions}",
            f"- Cleanup Issues: {len(self.issues)}",
            f"- Critical/Blocked: {sum(1 for issue in self.issues if issue.severity == 0)}",
            f"- High Priority: {sum(1 for issue in self.issues if issue.severity == 1)}",
            f"- Medium Priority: {sum(1 for issue in self.issues if issue.severity == 2)}",
            "",
            "Cleanup Queue:",
        ]
        if not self.issues:
            lines.append("- None")
        else:
            lines.extend(issue.render() for issue in self.issues)
        lines.extend(
            [
                "",
                f"Readiness Verdict: {readiness_verdict(self.issues)}",
                "",
                "Proof Meaning: this report combines authority gaps, unresolved receipts, graph health, missing Anti-Pattern/Question coverage, quality/decay pressure, and safe next actions into one operator-facing cleanup view.",
            ]
        )
        return "\n".join(lines)


def readiness_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    include_retired: bool = False,
) -> ReadinessReport:
    reviewed_memories = [
        memory
        for memory in memories
        if include_retired or memory.status == MemoryStatus.ACTIVE
    ]
    memory_by_id = {memory.id: memory for memory in reviewed_memories}
    issues: list[ReadinessIssue] = []
    issues.extend(authority_gap_issues(reviewed_memories, receipts))
    issues.extend(receipt_issues(receipts, memory_by_id))
    issues.extend(graph_issues(reviewed_memories))
    issues.extend(quality_issues(reviewed_memories, receipts))
    issues.extend(missing_type_issues(reviewed_memories))
    issues = sorted(issues, key=lambda issue: (issue.severity, issue.category, issue.subject_id, issue.title))
    return ReadinessReport(
        memories_reviewed=len(reviewed_memories),
        receipts_reviewed=len(receipts),
        stable_memories=sum(1 for memory in reviewed_memories if memory.type in STABLE_TYPES),
        anti_patterns=sum(1 for memory in reviewed_memories if memory.type == MemoryType.ANTI_PATTERN),
        questions=sum(1 for memory in reviewed_memories if memory.type == MemoryType.QUESTION),
        issues=issues,
        include_retired=include_retired,
    )


def authority_gap_issues(memories: list[Memory], receipts: list[MemoryUseReceipt]) -> list[ReadinessIssue]:
    report = governance_report(memories, receipts)
    issues: list[ReadinessIssue] = []
    for card in report.cards:
        if not card.state.startswith("blocked:"):
            continue
        issues.append(
            ReadinessIssue(
                severity=0,
                category="authority",
                subject_id=card.memory_id,
                title=card.title,
                state=card.state,
                evidence=f"Authority: {card.authority}; review: {card.authority_review}; use evidence: {card.use_evidence}",
                next_action=card.next_action,
            )
        )
    return issues


def receipt_issues(receipts: list[MemoryUseReceipt], memory_by_id: dict[str, Memory]) -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    for receipt in receipts:
        memory = memory_by_id.get(receipt.memory_id)
        if memory is None:
            issues.append(
                ReadinessIssue(
                    severity=0,
                    category="receipt",
                    subject_id=receipt.id,
                    title=receipt.memory_title,
                    state="orphan receipt",
                    evidence=f"Receipt points to missing memory {receipt.memory_id}.",
                    next_action="repair receipt memory_id, restore the memory, or resolve the receipt as not-applicable",
                )
            )
        elif not receipt.commit_hash and not receipt.outcome_signal:
            issues.append(
                ReadinessIssue(
                    severity=1,
                    category="receipt",
                    subject_id=receipt.id,
                    title=receipt.memory_title or memory.title,
                    state="unresolved receipt",
                    evidence=f"Memory {receipt.memory_id}; source {receipt.source_command}; surfaced {receipt.surfaced_at}.",
                    next_action=f"run `cmu use-link {receipt.id} --commit <hash>` or `cmu use-resolve {receipt.id} --outcome <reason> --note <note>`",
                )
            )
    return issues


def graph_issues(memories: list[Memory]) -> list[ReadinessIssue]:
    report = graph_memory_view_report(memories)
    memory_by_id = {memory.id: memory for memory in memories}
    issues: list[ReadinessIssue] = []
    for edge in report.dangling_edges:
        source = memory_by_id[edge.source_id]
        issues.append(
            ReadinessIssue(
                severity=1,
                category="graph",
                subject_id=source.id,
                title=source.title,
                state="dangling relationship",
                evidence=f"{edge.relation_type.value} points to missing memory {edge.target_id}.",
                next_action="repair the relationship target or include retired history before trusting this path",
            )
        )
    for memory_id in report.isolated_ids:
        memory = memory_by_id[memory_id]
        if memory.type in {MemoryType.CANDIDATE, MemoryType.QUESTION}:
            severity = 3
        else:
            severity = 2
        issues.append(
            ReadinessIssue(
                severity=severity,
                category="graph",
                subject_id=memory.id,
                title=memory.title,
                state="isolated memory",
                evidence="No incoming or outgoing graph relationships are recorded.",
                next_action="relate this memory to supporting situations, practices, exceptions, questions, or anti-patterns where evidence supports it",
            )
        )
    return issues


def quality_issues(memories: list[Memory], receipts: list[MemoryUseReceipt]) -> list[ReadinessIssue]:
    report = quality_report(memories, receipts)
    issues: list[ReadinessIssue] = []
    for card in report.cards:
        if card.state not in {"review", "decay-ready", "watch"}:
            continue
        severity = 1 if card.state == "decay-ready" else 2 if card.state == "review" else 3
        issues.append(
            ReadinessIssue(
                severity=severity,
                category="quality",
                subject_id=card.memory_id,
                title=card.title,
                state=card.state,
                evidence=f"Quality {card.score:.2f}/10; signals: {format_list(card.signals)}.",
                next_action=card.recommended_action,
            )
        )
    return issues


def missing_type_issues(memories: list[Memory]) -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    if not any(memory.type == MemoryType.ANTI_PATTERN and memory.status == MemoryStatus.ACTIVE for memory in memories):
        issues.append(
            ReadinessIssue(
                severity=2,
                category="coverage",
                subject_id="anti-pattern",
                title="",
                state="missing active Anti-Pattern memory",
                evidence="No active anti-pattern records exist in the reviewed memory base.",
                next_action="create real Anti-Pattern memories from actual tempting mistakes with `cmu add --type anti-pattern`",
            )
        )
    if not any(memory.type == MemoryType.QUESTION and memory.status == MemoryStatus.ACTIVE for memory in memories):
        issues.append(
            ReadinessIssue(
                severity=2,
                category="coverage",
                subject_id="question",
                title="",
                state="missing active Question memory",
                evidence="No active question records exist in the reviewed memory base.",
                next_action="create real Question memories for unresolved costly uncertainty with `cmu add --type question`",
            )
        )
    return issues


def readiness_verdict(issues: list[ReadinessIssue]) -> str:
    if any(issue.severity == 0 for issue in issues):
        return "blocked: fix critical authority or orphan-receipt issues before treating the memory base as trusted"
    if any(issue.severity == 1 for issue in issues):
        return "needs cleanup: close high-priority receipt, graph, or decay issues before broadening trust"
    if issues:
        return "usable with follow-up: cleanup remains, but no critical blockers were found"
    return "ready: no cleanup issues detected by this first-pass workflow"


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
