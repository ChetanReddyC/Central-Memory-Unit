from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .challenges import ChallengeRequest, challenge_stable_memory
from .json_store import read_json, update_json
from .models import Memory, MemoryScope, MemoryType, utc_now
from .retrieval import Match, PreflightQuery


DEFAULT_USE_FILE = "uses.json"
WIP_TERMS = {"wip", "tmp", "temp", "checkpoint", "draft"}
DEFAULT_AUTO_LINK_MIN_SCORE = 0.55
AUTO_LINK_AMBIGUITY_MARGIN = 0.12
STRONG_COMMIT_CONFIDENCE = 0.75
DRAG_REVIEW_MIN_SIGNALS = 2
DRAG_REVIEW_RATIO_MIN_USES = 3
DRAG_REVIEW_RATIO = 0.5
STRENGTHEN_MIN_STRONG_COMMITS = 2
USAGE_ADJUSTMENT_CAP = 0.8
USAGE_STRONG_COMMIT_WEIGHT = 0.25
USAGE_CHECKPOINT_WEIGHT = 0.08
USAGE_REVERTED_WEIGHT = -0.35
USAGE_LOW_CONFIDENCE_WEIGHT = -0.15
USAGE_MIXED_COMMIT_WEIGHT = -0.05
USAGE_NO_FILE_OVERLAP_WEIGHT = -0.15
RESOLVED_WITHOUT_COMMIT_OUTCOMES = {"no_checkpoint", "not_applicable", "superseded"}
RESOLVED_WITHOUT_COMMIT_FLAG = "resolved_without_commit"


@dataclass
class MemoryUseReceipt:
    id: str
    memory_id: str
    memory_title: str
    prompt: str
    actor: str
    area: str
    files: list[str]
    risk: str
    match_score: float
    source_command: str = "preflight"
    semantic_mode: str = "off"
    semantic_label: str = "unavailable"
    semantic_score: float = 0.0
    semantic_proposal_status: str = ""
    workflow: list[str] = field(default_factory=list)
    environment: list[str] = field(default_factory=list)
    surfaced_at: str = field(default_factory=utc_now)
    commit_hash: str = ""
    commit_message: str = ""
    commit_files: list[str] = field(default_factory=list)
    commit_time: str = ""
    metadata_source: str = ""
    linked_at: str = ""
    outcome_signal: str = ""
    link_confidence: float = 0.0
    flags: list[str] = field(default_factory=list)
    note: str = ""

    @classmethod
    def create(
        cls,
        memory: Memory,
        query: PreflightQuery,
        match: Match,
        *,
        source_command: str = "preflight",
        semantic_mode: str = "off",
    ) -> "MemoryUseReceipt":
        return cls(
            id=f"use_{uuid4().hex[:12]}",
            memory_id=memory.id,
            memory_title=memory.title,
            prompt=query.prompt.strip(),
            actor=query.actor.strip(),
            area=query.area.strip(),
            files=[item.strip() for item in query.files or [] if item.strip()],
            workflow=[item.strip() for item in query.workflow or [] if item.strip()],
            environment=[item.strip() for item in query.environment or [] if item.strip()],
            risk=query.risk.strip(),
            match_score=match.score,
            source_command=source_command.strip() or "preflight",
            semantic_mode=semantic_mode.strip() or "off",
            semantic_label=getattr(match, "semantic_label", "unavailable"),
            semantic_score=getattr(match, "semantic_score", 0.0),
            semantic_proposal_status=getattr(match, "semantic_proposal_status", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryUseReceipt":
        return cls(
            id=data["id"],
            memory_id=data["memory_id"],
            memory_title=data.get("memory_title", ""),
            prompt=data.get("prompt", ""),
            actor=data.get("actor", ""),
            area=data.get("area", ""),
            files=list(data.get("files", [])),
            workflow=list(data.get("workflow", [])),
            environment=list(data.get("environment", [])),
            risk=data.get("risk", ""),
            match_score=float(data.get("match_score", 0.0)),
            source_command=data.get("source_command", "preflight"),
            semantic_mode=data.get("semantic_mode", "off"),
            semantic_label=data.get("semantic_label", "unavailable"),
            semantic_score=float(data.get("semantic_score", 0.0)),
            semantic_proposal_status=data.get("semantic_proposal_status", ""),
            surfaced_at=data.get("surfaced_at", utc_now()),
            commit_hash=data.get("commit_hash", ""),
            commit_message=data.get("commit_message", ""),
            commit_files=list(data.get("commit_files", [])),
            commit_time=data.get("commit_time", ""),
            metadata_source=data.get("metadata_source", ""),
            linked_at=data.get("linked_at", ""),
            outcome_signal=data.get("outcome_signal", ""),
            link_confidence=float(data.get("link_confidence", 0.0)),
            flags=list(data.get("flags", [])),
            note=data.get("note", ""),
        )


@dataclass
class CommitLinkRequest:
    use_id: str
    commit_hash: str
    message: str = ""
    files: list[str] | None = None
    commit_time: str = ""
    metadata_source: str = "manual"
    note: str = ""


@dataclass
class GitCommitMetadata:
    commit_hash: str
    message: str
    files: list[str]
    commit_time: str


@dataclass
class CommitLinkDecision:
    linked: bool
    reason: str
    receipt: MemoryUseReceipt | None = None
    missing: list[str] | None = None

    def render(self) -> str:
        if not self.linked or self.receipt is None:
            lines = ["CMU Use Link Not Applied", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {format_list(self.missing)}")
            return "\n".join(lines)
        return "\n".join(
            [
                "CMU Use Link Applied",
                f"Use Receipt: {self.receipt.id}",
                f"Memory: {self.receipt.memory_id} {self.receipt.memory_title}",
                f"Commit: {self.receipt.commit_hash}",
                f"Outcome: {self.receipt.outcome_signal}",
                f"Confidence: {self.receipt.link_confidence:.2f}",
                f"Flags: {format_list(self.receipt.flags)}",
                f"Reason: {self.reason}",
            ]
        )


@dataclass
class ReceiptResolutionDecision:
    resolved: bool
    reason: str
    receipt: MemoryUseReceipt | None = None
    missing: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.resolved or self.receipt is None:
            lines = ["CMU Use Receipt Resolution Not Applied", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {format_list(self.missing)}")
            lines.append(f"Allowed Outcomes: {format_list(sorted(RESOLVED_WITHOUT_COMMIT_OUTCOMES))}")
            return "\n".join(lines)
        return "\n".join(
            [
                "CMU Use Receipt Resolution Applied",
                f"Use Receipt: {self.receipt.id}",
                f"Memory: {self.receipt.memory_id} {self.receipt.memory_title}",
                f"Outcome: {self.receipt.outcome_signal}",
                f"Resolved By: {self.receipt.metadata_source}",
                f"Note: {self.receipt.note}",
                "No Commit Linked: this closes the evidence gap without counting as committed usefulness.",
            ]
        )


@dataclass
class MemoryUseSummary:
    memory_id: str
    total: int
    committed: int = 0
    checkpoints: int = 0
    reverted: int = 0
    low_confidence: int = 0
    mixed: int = 0
    resolved_without_commit: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    semantic_mode_counts: dict[str, int] = field(default_factory=dict)
    semantic_match_counts: dict[str, int] = field(default_factory=dict)
    average_confidence: float = 0.0
    retrieval_adjustment: float = 0.0

    def render(self) -> str:
        return "\n".join(
            [
                "CMU Memory Use Summary",
                f"Memory: {self.memory_id}",
                f"Total Uses: {self.total}",
                f"Committed: {self.committed}",
                f"Checkpoints: {self.checkpoints}",
                f"Reverted: {self.reverted}",
                f"Low Confidence: {self.low_confidence}",
                f"Mixed Commits: {self.mixed}",
                f"Resolved Without Commit: {self.resolved_without_commit}",
                f"Sources: {format_source_counts(self.source_counts)}",
                f"Semantic Modes: {format_source_counts(self.semantic_mode_counts)}",
                f"Semantic Matches: {format_source_counts(self.semantic_match_counts)}",
                f"Average Confidence: {self.average_confidence:.2f}",
                f"Retrieval Adjustment: {self.retrieval_adjustment:+.2f}",
            ]
        )


@dataclass
class AutoLinkCandidate:
    receipt: MemoryUseReceipt
    commit: GitCommitMetadata
    score: float
    reasons: list[str]


@dataclass
class AutoLinkDecision:
    receipt: MemoryUseReceipt
    matched: bool
    applied: bool
    reason: str
    score: float = 0.0
    commit_hash: str = ""
    reasons: list[str] = field(default_factory=list)
    ambiguous_commits: list[str] = field(default_factory=list)
    split_credit: bool = False


@dataclass
class AutoLinkReport:
    applied: bool
    decisions: list[AutoLinkDecision]
    review_prompts: list[str] = field(default_factory=list)
    error: str = ""

    def render(self) -> str:
        if self.error:
            return "\n".join(["CMU Use Link Auto Not Applied", f"Reason: {self.error}", "No receipts were changed."])
        header = "CMU Use Link Auto Applied" if self.applied else "CMU Use Link Auto Dry Run"
        lines = [header]
        if not self.decisions:
            lines.append("No unlinked Memory Use Receipts found.")
        for decision in self.decisions:
            if decision.matched:
                action = "linked" if decision.applied else "would link"
                split = " split-credit" if decision.split_credit else ""
                lines.append(
                    f"{decision.receipt.id} {action}{split} {short_hash(decision.commit_hash)} "
                    f"link score {decision.score:.2f} - {decision.reason}"
                )
                lines.append(f"  Reasons: {format_list(decision.reasons)}")
            else:
                lines.append(f"{decision.receipt.id} not linked - {decision.reason}")
                if decision.ambiguous_commits:
                    lines.append(f"  Ambiguous Commits: {format_list([short_hash(item) for item in decision.ambiguous_commits])}")
        if self.review_prompts:
            lines.append("")
            lines.append("Review Prompts")
            lines.extend(f"- {prompt}" for prompt in self.review_prompts)
        return "\n".join(lines)


@dataclass
class UseReviewCard:
    memory: Memory | None
    memory_id: str
    memory_title: str
    total_uses: int
    linked_uses: int
    committed: int
    strong_committed: int
    checkpoints: int
    reverted: int
    low_confidence: int
    mixed: int
    resolved_without_commit: int
    drag_signals: int
    status: str
    why: str
    suggested_action: str
    source_counts: dict[str, int] = field(default_factory=dict)
    semantic_mode_counts: dict[str, int] = field(default_factory=dict)
    semantic_match_counts: dict[str, int] = field(default_factory=dict)
    semantic_strong_committed: int = 0
    semantic_drag_signals: int = 0

    def render(self) -> str:
        memory_label = f"{self.memory_id} {self.memory_title}".strip()
        memory_type = self.memory.type.value if self.memory is not None else "unknown"
        lines = [
            "CMU Use Review",
            f"Memory: {memory_label}",
            f"Type: {memory_type}",
            f"Status: {self.status}",
            f"Why: {self.why}",
            f"Signals: {self.signal_summary()}",
            f"Sources: {format_source_counts(self.source_counts)}",
            f"Semantic Modes: {format_source_counts(self.semantic_mode_counts)}",
            f"Semantic Matches: {format_source_counts(self.semantic_match_counts)}",
        ]
        interpretation = self.signal_interpretation()
        if interpretation:
            lines.append(f"Interpretation: {interpretation}")
        lines.extend(
            [
                f"Suggested Action: {self.suggested_action}",
                "Do Not Auto-Mutate: Use evidence should guide review, not silently rewrite memory.",
            ]
        )
        return "\n".join(lines)

    def signal_summary(self) -> str:
        return (
            f"{self.linked_uses}/{self.total_uses} linked uses; "
            f"{self.committed} committed ({self.strong_committed} strong), "
            f"{self.checkpoints} checkpoints, {self.reverted} reverted, "
            f"{self.low_confidence} low-confidence, {self.mixed} mixed, "
            f"{self.resolved_without_commit} resolved-without-commit, "
            f"{self.drag_signals} drag signals"
        )

    def signal_interpretation(self) -> str:
        semantic_note = ""
        if self.semantic_drag_signals:
            semantic_note = f"{self.semantic_drag_signals} drag signals came from semantic-assisted receipts; inspect semantic grounding before changing trust. "
        elif self.semantic_strong_committed:
            semantic_note = f"{self.semantic_strong_committed} strong committed uses came from semantic-assisted receipts; semantic retrieval has positive linked evidence. "
        if self.mixed:
            return semantic_note + (
                "Mixed commits are weak evidence because the checkpoint changed much more than the receipt files; "
                "inspect scope before tuning thresholds."
            )
        if self.drag_signals:
            return semantic_note + "Drag signals mean the linked evidence is weak, reverted, or unrelated; review the memory before tuning thresholds."
        return semantic_note.strip()


@dataclass
class UseReviewReport:
    cards: list[UseReviewCard]
    memory_id: str = ""

    def render(self) -> str:
        if not self.cards:
            if self.memory_id:
                return "\n".join(["CMU Use Review", f"Memory: {self.memory_id}", "Status: No use evidence found."])
            return "\n".join(["CMU Use Review", "Status: No memory use review cards found."])
        return "\n\n".join(card.render() for card in self.cards)


@dataclass
class UseThresholdMemoryDiagnostic:
    memory_id: str
    memory_title: str
    memory_type: str
    total_uses: int
    linked_uses: int
    strong_committed: int
    drag_signals: int
    resolved_without_commit: int
    source_counts: dict[str, int]
    semantic_mode_counts: dict[str, int]
    semantic_match_counts: dict[str, int]
    retrieval_adjustment: float
    status: str
    suggested_action: str

    def render(self) -> str:
        return (
            f"- {self.memory_id} [{self.memory_type}] {self.memory_title}: {self.status}; "
            f"{self.linked_uses}/{self.total_uses} linked, {self.strong_committed} strong, "
            f"{self.drag_signals} drag, {self.resolved_without_commit} resolved, adjustment {self.retrieval_adjustment:+.2f}; "
            f"sources {format_source_counts(self.source_counts)}; "
            f"semantic {format_source_counts(self.semantic_mode_counts)} / {format_source_counts(self.semantic_match_counts)}; "
            f"{self.suggested_action}"
        )


@dataclass
class UseThresholdReport:
    total_receipts: int
    linked_receipts: int
    unlinked_receipts: int
    source_counts: dict[str, int]
    semantic_mode_counts: dict[str, int]
    semantic_match_counts: dict[str, int]
    diagnostics: list[UseThresholdMemoryDiagnostic]

    def render(self) -> str:
        linked_ratio = self.linked_receipts / self.total_receipts if self.total_receipts else 0.0
        lines = [
            "CMU Use Threshold Report",
            "Mode: diagnostic only; no memory or receipt mutation.",
            "",
            "Current Thresholds",
            f"- Auto-Link Apply Candidate: score >= {DEFAULT_AUTO_LINK_MIN_SCORE:.2f}",
            f"- Auto-Link Ambiguity: best score within {AUTO_LINK_AMBIGUITY_MARGIN:.2f} of another candidate stays unlinked",
            f"- Strong Committed Use: committed with confidence >= {STRONG_COMMIT_CONFIDENCE:.2f}",
            f"- Strengthen Review: {STRENGTHEN_MIN_STRONG_COMMITS}+ strong committed uses and 0 drag signals",
            f"- Drag Review: {DRAG_REVIEW_MIN_SIGNALS}+ drag signals, or {DRAG_REVIEW_RATIO:.0%}+ drag across {DRAG_REVIEW_RATIO_MIN_USES}+ linked uses",
            f"- Retrieval Adjustment Cap: +/-{USAGE_ADJUSTMENT_CAP:.2f}",
            (
                "- Retrieval Weights: "
                f"strong {USAGE_STRONG_COMMIT_WEIGHT:+.2f}, checkpoint {USAGE_CHECKPOINT_WEIGHT:+.2f}, "
                f"revert {USAGE_REVERTED_WEIGHT:+.2f}, low-confidence {USAGE_LOW_CONFIDENCE_WEIGHT:+.2f}, "
                f"mixed {USAGE_MIXED_COMMIT_WEIGHT:+.2f}, no-file-overlap {USAGE_NO_FILE_OVERLAP_WEIGHT:+.2f}"
            ),
            "",
            "Observed Receipts",
            f"- Total: {self.total_receipts}",
            f"- Linked: {self.linked_receipts}",
            f"- Unlinked: {self.unlinked_receipts}",
            f"- Sources: {format_source_counts(self.source_counts)}",
            f"- Semantic Modes: {format_source_counts(self.semantic_mode_counts)}",
            f"- Semantic Matches: {format_source_counts(self.semantic_match_counts)}",
            "",
            "Evidence Readiness",
            f"- Functionality: {threshold_functionality_status(self.total_receipts, self.linked_receipts)}",
            f"- Accuracy: {threshold_accuracy_status(self.total_receipts, self.linked_receipts, linked_ratio)}",
            "",
            "Memory Diagnostics",
        ]
        if not self.diagnostics:
            lines.append("- No Memory Use Receipts found. Run preflight and link receipts before tuning thresholds.")
        else:
            lines.extend(diagnostic.render() for diagnostic in self.diagnostics)
        lines.extend(
            [
                "",
                "Tuning Guidance",
                "- If many correct receipts stay unlinked, inspect auto-link score components before lowering the min score.",
                "- If many wrong receipts link, raise the min score or widen ambiguity refusal.",
                "- If useful memories rarely strengthen, inspect the strong committed threshold before changing promotion rules.",
                "- If noisy memories avoid review, inspect drag thresholds before adding automation.",
                "Do Not Auto-Mutate: threshold evidence should guide review and tuning, not silently rewrite memory.",
            ]
        )
        return "\n".join(lines)


@dataclass
class SemanticAuditMemoryLine:
    memory_id: str
    memory_title: str
    strong_committed: int = 0
    drag_signals: int = 0

    def render_strong(self) -> str:
        return f"- {self.memory_id} {self.memory_title}: {self.strong_committed} semantic-assisted strong committed uses"

    def render_drag(self) -> str:
        return f"- {self.memory_id} {self.memory_title}: {self.drag_signals} semantic-assisted drag signals"


@dataclass
class SemanticAuditReport:
    total_receipts: int
    semantic_receipts: int
    semantic_linked: int
    semantic_resolved_without_commit: int
    semantic_unresolved: int
    semantic_strong_committed: int
    semantic_drag_signals: int
    semantic_mode_counts: dict[str, int]
    semantic_match_counts: dict[str, int]
    memory_id: str = ""
    memory_title: str = ""
    strong_memories: list[SemanticAuditMemoryLine] = field(default_factory=list)
    drag_memories: list[SemanticAuditMemoryLine] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Semantic Audit",
            "Mode: read-only; no memory or receipt mutation.",
        ]
        if self.memory_id:
            memory_label = f"{self.memory_id} {self.memory_title}".strip()
            lines.append(f"Memory: {memory_label}")
        lines.extend(
            [
                f"Total Receipts: {self.total_receipts}",
                f"Semantic-Assisted Receipts: {self.semantic_receipts}",
                f"Semantic-Assisted Linked: {self.semantic_linked}",
                f"Semantic-Assisted Resolved Without Commit: {self.semantic_resolved_without_commit}",
                f"Semantic-Assisted Unresolved: {self.semantic_unresolved}",
                f"Semantic-Assisted Strong Committed: {self.semantic_strong_committed}",
                f"Semantic-Assisted Drag Signals: {self.semantic_drag_signals}",
                f"Semantic Modes: {format_source_counts(self.semantic_mode_counts)}",
                f"Semantic Matches: {format_source_counts(self.semantic_match_counts)}",
                "",
                "Semantic Strong Evidence",
            ]
        )
        if self.strong_memories:
            lines.extend(line.render_strong() for line in self.strong_memories)
        else:
            lines.append("- None")
        lines.append("")
        lines.append("Semantic Drag")
        if self.drag_memories:
            lines.extend(line.render_drag() for line in self.drag_memories)
        else:
            lines.append("- None")
        lines.extend(["", f"Recommended Action: {self.recommended_action()}"])
        return "\n".join(lines)

    def recommended_action(self) -> str:
        if self.semantic_receipts == 0:
            if self.memory_id:
                return "No semantic-assisted receipts for this memory yet; keep collecting evidence before judging semantic fit."
            return "No semantic-assisted receipts yet; keep semantic retrieval opt-in and collect evidence before tuning."
        if self.semantic_linked == 0:
            return "Link semantic-assisted receipts to commits before judging semantic usefulness or drag."
        if self.semantic_drag_signals and self.semantic_drag_signals >= self.semantic_strong_committed:
            return "Inspect semantic-assisted drag before broadening semantic retrieval or changing thresholds."
        if self.semantic_unresolved:
            return "Resolve remaining semantic-assisted receipts before treating semantic usefulness as settled."
        if self.semantic_strong_committed:
            return "Semantic retrieval has positive linked evidence; keep collecting focused receipts before tuning thresholds."
        if self.semantic_resolved_without_commit:
            return "Semantic-assisted receipts were resolved without commit evidence; keep observing before tuning thresholds."
        return "Semantic retrieval has linked evidence but no strong or drag pattern yet; keep observing."


@dataclass
class SemanticAuditRecommendationLine:
    memory_id: str
    memory_title: str
    semantic_receipts: int
    semantic_linked: int
    semantic_resolved_without_commit: int
    semantic_unresolved: int
    semantic_strong_committed: int
    semantic_drag_signals: int
    action: str
    details: list["SemanticAuditReceiptDetail"] = field(default_factory=list)

    def render(self) -> str:
        memory_label = f"{self.memory_id} {self.memory_title}".strip()
        line = (
            f"- {memory_label}: {self.action} "
            f"({self.semantic_receipts} semantic receipts, {self.semantic_linked} linked, "
            f"{self.semantic_resolved_without_commit} resolved, "
            f"{self.semantic_unresolved} unresolved, "
            f"{self.semantic_strong_committed} strong, {self.semantic_drag_signals} drag)"
        )
        if not self.details:
            return line
        detail_lines: list[str] = []
        for detail in self.details:
            detail_lines.extend(detail.render())
        return "\n".join([line, *detail_lines])


@dataclass
class SemanticAuditCommitCandidateDetail:
    commit_hash: str
    message: str
    commit_time: str
    files: list[str]
    overlap: list[str]
    score: float
    reasons: list[str]

    def render(self) -> str:
        return (
            f"    - {short_hash(self.commit_hash)} score {self.score:.2f}; "
            f"message: {self.message or 'None'}; time: {self.commit_time or 'unknown'}; "
            f"overlap: {format_list(self.overlap)}; files: {format_list(self.files[:5])}; "
            f"reasons: {format_list(self.reasons)}"
        )


@dataclass
class SemanticAuditReceiptDetail:
    receipt_id: str
    source_command: str
    semantic_mode: str
    semantic_status: str
    semantic_score: float
    linked: bool
    resolved_without_commit: bool = False
    commit_hash: str = ""
    outcome_signal: str = ""
    link_confidence: float = 0.0
    auto_link_reason: str = ""
    candidate_commits: list[SemanticAuditCommitCandidateDetail] = field(default_factory=list)

    def command_lines(self) -> list[str]:
        if self.linked or self.resolved_without_commit:
            return []
        lines = [f"# {self.receipt_id} {self.source_command} semantic={self.semantic_mode}/{self.semantic_status or 'unknown'}"]
        for candidate in self.candidate_commits:
            lines.append(f"cmu use-link {self.receipt_id} --commit {candidate.commit_hash}")
        for outcome in sorted(RESOLVED_WITHOUT_COMMIT_OUTCOMES):
            cli_outcome = outcome.replace("_", "-")
            lines.append(
                f'cmu use-resolve {self.receipt_id} --outcome {cli_outcome} '
                '--note "<why no Git checkpoint should be linked>"'
            )
        return lines

    def render(self) -> list[str]:
        state = "resolved-without-commit" if self.resolved_without_commit else ("linked" if self.linked else "unlinked")
        lines = [
            (
                f"  - {self.receipt_id} {self.source_command} {state}; "
                f"semantic={self.semantic_mode}/{self.semantic_status or 'unknown'} "
                f"score={self.semantic_score:.2f}"
            )
        ]
        if self.resolved_without_commit:
            lines.append(
                f"    Resolution: {self.outcome_signal or 'unknown'}; "
                f"resolved-by={self.auto_link_reason or 'unknown'}"
            )
            return lines
        if self.linked:
            lines.append(
                f"    Linked Commit: {short_hash(self.commit_hash)}; "
                f"outcome={self.outcome_signal or 'unknown'}; confidence={self.link_confidence:.2f}"
            )
            return lines
        lines.append(f"    Auto-Link: {self.auto_link_reason or 'not inspected'}")
        if self.candidate_commits:
            lines.append(f"    Manual Link: cmu use-link {self.receipt_id} --commit <hash>")
            lines.append("    Candidate Commits:")
            for candidate in self.candidate_commits:
                lines.append(candidate.render())
                lines.append(f"      command: cmu use-link {self.receipt_id} --commit {candidate.commit_hash}")
        lines.append("    No-Commit Resolution Options:")
        for outcome in sorted(RESOLVED_WITHOUT_COMMIT_OUTCOMES):
            cli_outcome = outcome.replace("_", "-")
            lines.append(
                f"      command: cmu use-resolve {self.receipt_id} --outcome {cli_outcome} "
                '--note "<why no Git checkpoint should be linked>"'
            )
        return lines

@dataclass
class SemanticAuditRecommendationsReport:
    no_link: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    partial: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    strong: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    drag: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    neutral: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    no_semantic: list[SemanticAuditRecommendationLine] = field(default_factory=list)
    open_only: bool = False
    commands_only: bool = False
    receipt_id: str = ""
    limit: int = 20
    hours: int = 72
    min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE

    def render(self) -> str:
        if self.commands_only:
            return self.render_commands_only()
        lines = [
            "CMU Semantic Audit Recommendations",
            "Mode: read-only; no memory or receipt mutation.",
        ]
        if self.open_only:
            lines.append("Detail Filter: open semantic receipts only.")
        if self.receipt_id:
            lines.append(f"Receipt Filter: {self.receipt_id}")
        if self.details_are_tuned():
            lines.append(f"Candidate Window: limit={self.limit}, hours={self.hours}, min-score={self.min_score:.2f}")
        lines.extend(
            [
                "",
                "Link Receipts First",
                *render_recommendation_group(self.no_link),
                "",
                "Resolve Remaining Semantic Evidence",
                *render_recommendation_group(self.partial),
                "",
                "Inspect Semantic Drag",
                *render_recommendation_group(self.drag),
                "",
                "Positive Semantic Signal",
                *render_recommendation_group(self.strong),
                "",
                "Neutral Linked Semantic Evidence",
                *render_recommendation_group(self.neutral),
                "",
                "No Semantic Evidence",
                *render_recommendation_group(self.no_semantic),
                "",
                "Tuning Guidance: Do not tune thresholds or broaden semantic proposal behavior until linked per-memory evidence shows a repeated pattern.",
            ]
        )
        return "\n".join(lines)

    def render_commands_only(self) -> str:
        lines = [
            "CMU Semantic Audit Closure Commands",
            "Mode: read-only; no memory or receipt mutation.",
            "Detail Filter: open semantic receipts only.",
        ]
        if self.receipt_id:
            lines.append(f"Receipt Filter: {self.receipt_id}")
        if self.details_are_tuned():
            lines.append(f"Candidate Window: limit={self.limit}, hours={self.hours}, min-score={self.min_score:.2f}")
        command_lines: list[str] = []
        for group in [self.no_link, self.partial, self.drag, self.strong, self.neutral]:
            for item in group:
                for detail in item.details:
                    command_lines.extend(detail.command_lines())
        if not command_lines:
            lines.append("No unresolved semantic receipt commands found.")
        else:
            lines.extend(command_lines)
        lines.append("Review before running: choose either one use-link command or one use-resolve command per receipt.")
        return "\n".join(lines)

    def details_are_tuned(self) -> bool:
        return self.limit != 20 or self.hours != 72 or self.min_score != DEFAULT_AUTO_LINK_MIN_SCORE


@dataclass
class UseReviewFollowUp:
    action: str
    applied: bool
    reason: str
    card: UseReviewCard | None = None
    memory: Memory | None = None
    challenge_memory: Memory | None = None
    evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.card is None or self.memory is None:
            lines = ["CMU Use Review Follow-Up Not Prepared", f"Action: {self.action}", f"Reason: {self.reason}"]
            if self.missing:
                lines.append(f"Missing: {format_list(self.missing)}")
            return "\n".join(lines)
        header = "CMU Use Review Follow-Up Applied" if self.applied else "CMU Use Review Follow-Up Proposal"
        lines = [
            header,
            f"Action: {self.action}",
            f"Memory: {self.memory.id} [{self.memory.type.value}] {self.memory.title}",
            f"Review Status: {self.card.status}",
            f"Why: {self.card.why}",
            f"Reason: {self.reason}",
        ]
        if self.evidence:
            lines.append(f"Prepared Evidence: {format_list(self.evidence)}")
        if self.challenge_memory is not None:
            lines.extend(
                [
                    f"Challenge Candidate: {self.challenge_memory.id} {self.challenge_memory.title}",
                    f"Mismatch: {self.challenge_memory.summary}",
                    f"Expected Benefit: {self.challenge_memory.use_this_path}",
                    f"Risk: {self.challenge_memory.avoid_this}",
                    f"Rollback Path: {self.challenge_memory.challenge_only_if}",
                ]
            )
        if self.missing:
            lines.append(f"Missing: {format_list(self.missing)}")
        lines.append("Do Not Auto-Mutate: Follow-up actions require explicit apply/approval or the challenge path.")
        return "\n".join(lines)


class MemoryUseStore:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.store_dir = self.root / ".cmu"
        self.store_file = self.store_dir / DEFAULT_USE_FILE

    def init(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        read_json(self.store_file, {"version": 1, "uses": []})
        return self.store_file

    def add(self, receipt: MemoryUseReceipt) -> MemoryUseReceipt:
        return update_json(
            self.store_file,
            {"version": 1, "uses": []},
            lambda data: append_receipt(data, receipt),
        )

    def list(self) -> list[MemoryUseReceipt]:
        return sorted(
            [MemoryUseReceipt.from_dict(item) for item in self._read()["uses"]],
            key=lambda item: item.surfaced_at,
            reverse=True,
        )

    def get(self, use_id: str) -> MemoryUseReceipt:
        for receipt in self.list():
            if receipt.id == use_id:
                return receipt
        raise KeyError(f"Use receipt not found: {use_id}")

    def update(self, receipt: MemoryUseReceipt) -> MemoryUseReceipt:
        return update_json(
            self.store_file,
            {"version": 1, "uses": []},
            lambda data: replace_receipt(data, receipt),
        )

    def list_for_memory(self, memory_id: str) -> list[MemoryUseReceipt]:
        return [receipt for receipt in self.list() if receipt.memory_id == memory_id]

    def _read(self) -> dict:
        return read_json(self.store_file, {"version": 1, "uses": []})


def append_receipt(data: dict, receipt: MemoryUseReceipt) -> MemoryUseReceipt:
    data["uses"].append(receipt.to_dict())
    return receipt


def replace_receipt(data: dict, receipt: MemoryUseReceipt) -> MemoryUseReceipt:
    uses = data["uses"]
    for index, current in enumerate(uses):
        if current["id"] == receipt.id:
            uses[index] = receipt.to_dict()
            return receipt
    raise KeyError(f"Use receipt not found: {receipt.id}")


def link_commit(receipt: MemoryUseReceipt, request: CommitLinkRequest) -> CommitLinkDecision:
    missing = missing_link_fields(request)
    if missing:
        return CommitLinkDecision(linked=False, reason="commit link requires a use id and commit hash", missing=missing)
    receipt.commit_hash = request.commit_hash.strip()
    receipt.commit_message = request.message.strip()
    receipt.commit_files = clean_list(request.files or [])
    receipt.commit_time = request.commit_time.strip()
    receipt.metadata_source = request.metadata_source.strip() or "manual"
    receipt.note = request.note.strip()
    receipt.linked_at = utc_now()
    receipt.flags = link_flags(receipt)
    receipt.outcome_signal = outcome_signal(receipt.flags)
    receipt.link_confidence = link_confidence(receipt)
    return CommitLinkDecision(
        linked=True,
        reason="Linked memory use receipt to Git checkpoint signal.",
        receipt=receipt,
    )


def resolve_receipt_without_commit(
    receipt: MemoryUseReceipt,
    *,
    outcome: str,
    note: str,
    resolved_by: str = "",
) -> ReceiptResolutionDecision:
    normalized_outcome = outcome.strip().replace("-", "_").lower()
    normalized_note = note.strip()
    missing: list[str] = []
    if not receipt.id.strip():
        missing.append("use_id")
    if normalized_outcome not in RESOLVED_WITHOUT_COMMIT_OUTCOMES:
        missing.append("valid_outcome")
    if not normalized_note:
        missing.append("note")
    if missing:
        return ReceiptResolutionDecision(
            resolved=False,
            reason="receipt resolution requires a valid outcome and a note explaining why no Git commit should be linked",
            receipt=receipt,
            missing=missing,
        )
    if receipt.commit_hash:
        return ReceiptResolutionDecision(
            resolved=False,
            reason="receipt already has a linked commit; use-review should inspect the existing commit evidence",
            receipt=receipt,
        )
    receipt.outcome_signal = normalized_outcome
    receipt.commit_message = ""
    receipt.commit_files = []
    receipt.commit_time = ""
    receipt.metadata_source = resolved_by.strip() or "cmu use-resolve"
    receipt.note = normalized_note
    receipt.linked_at = utc_now()
    receipt.link_confidence = 0.0
    append_flag(receipt, RESOLVED_WITHOUT_COMMIT_FLAG)
    return ReceiptResolutionDecision(resolved=True, reason="Resolved receipt without Git commit evidence.", receipt=receipt)


def link_git_commit(
    receipt: MemoryUseReceipt,
    *,
    root: Path | str,
    ref: str = "HEAD",
    note: str = "",
    message_override: str = "",
    files_override: list[str] | None = None,
) -> CommitLinkDecision:
    try:
        metadata = inspect_git_commit(root, ref)
    except RuntimeError as error:
        return CommitLinkDecision(linked=False, reason=str(error), receipt=receipt)
    message = message_override.strip() or metadata.message
    files = files_override if files_override else metadata.files
    return link_commit(
        receipt,
        CommitLinkRequest(
            use_id=receipt.id,
            commit_hash=metadata.commit_hash,
            message=message,
            files=files,
            commit_time=metadata.commit_time,
            metadata_source="git",
            note=note,
        ),
    )


def auto_link_receipts(
    receipts: list[MemoryUseReceipt],
    memories: list[Memory],
    *,
    root: Path | str,
    limit: int = 20,
    hours: int = 72,
    min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
    apply: bool = False,
) -> AutoLinkReport:
    unlinked = [receipt for receipt in receipts if not receipt.commit_hash]
    if not unlinked:
        return AutoLinkReport(applied=apply, decisions=[], review_prompts=drag_review_prompts(receipts, memories))
    try:
        commits = inspect_recent_git_commits(root, limit=limit)
    except RuntimeError as error:
        return AutoLinkReport(applied=apply, decisions=[], error=str(error))
    memory_by_id = {memory.id: memory for memory in memories}
    selected: list[tuple[MemoryUseReceipt, AutoLinkCandidate]] = []
    decisions: list[AutoLinkDecision] = []
    for receipt in unlinked:
        memory = memory_by_id.get(receipt.memory_id)
        candidates = sorted(
            [
                candidate
                for candidate in (score_auto_link_candidate(receipt, memory, commit, hours=hours) for commit in commits)
                if candidate.score >= min_score
            ],
            key=lambda item: item.score,
            reverse=True,
        )
        if not candidates:
            decisions.append(AutoLinkDecision(receipt=receipt, matched=False, applied=False, reason="no recent commit crossed the auto-link threshold"))
            continue
        best = candidates[0]
        close = [candidate for candidate in candidates[1:] if best.score - candidate.score <= AUTO_LINK_AMBIGUITY_MARGIN]
        if close:
            decisions.append(
                AutoLinkDecision(
                    receipt=receipt,
                    matched=False,
                    applied=False,
                    reason="multiple commits were plausible; leaving receipt unlinked",
                    score=best.score,
                    ambiguous_commits=[best.commit.commit_hash, *[candidate.commit.commit_hash for candidate in close]],
                )
            )
            continue
        selected.append((receipt, best))

    commit_counts: dict[str, int] = {}
    for _, candidate in selected:
        commit_counts[candidate.commit.commit_hash] = commit_counts.get(candidate.commit.commit_hash, 0) + 1

    for receipt, candidate in selected:
        split_credit = commit_counts[candidate.commit.commit_hash] > 1
        linked_receipt = receipt
        if apply:
            decision = link_commit(
                linked_receipt,
                CommitLinkRequest(
                    use_id=receipt.id,
                    commit_hash=candidate.commit.commit_hash,
                    message=candidate.commit.message,
                    files=candidate.commit.files,
                    commit_time=candidate.commit.commit_time,
                    metadata_source="git-auto",
                    note=f"Auto-linked by CMU with score {candidate.score:.2f}.",
                ),
            )
            if decision.linked and decision.receipt is not None:
                linked_receipt = decision.receipt
                append_flag(linked_receipt, "auto_linked")
                if split_credit:
                    append_flag(linked_receipt, "split_credit")
                    linked_receipt.link_confidence = round(max(0.05, linked_receipt.link_confidence - 0.15), 2)
        decisions.append(
            AutoLinkDecision(
                receipt=linked_receipt,
                matched=True,
                applied=apply,
                reason="matched by recent commit metadata",
                score=candidate.score,
                commit_hash=candidate.commit.commit_hash,
                reasons=candidate.reasons,
                split_credit=split_credit,
            )
        )
    return AutoLinkReport(applied=apply, decisions=decisions, review_prompts=drag_review_prompts(receipts, memories))


def use_summary(receipts: list[MemoryUseReceipt], memory_id: str) -> MemoryUseSummary:
    relevant = [receipt for receipt in receipts if receipt.memory_id == memory_id]
    confidences = [receipt.link_confidence for receipt in relevant if receipt.link_confidence > 0]
    return MemoryUseSummary(
        memory_id=memory_id,
        total=len(relevant),
        committed=sum(1 for receipt in relevant if receipt.outcome_signal == "committed"),
        checkpoints=sum(1 for receipt in relevant if receipt.outcome_signal == "checkpoint"),
        reverted=sum(1 for receipt in relevant if receipt.outcome_signal == "reverted"),
        low_confidence=sum(1 for receipt in relevant if receipt.outcome_signal == "committed_low_confidence"),
        mixed=sum(1 for receipt in relevant if "mixed_commit" in receipt.flags),
        resolved_without_commit=sum(1 for receipt in relevant if is_resolved_without_commit(receipt)),
        source_counts=source_counts(relevant),
        semantic_mode_counts=semantic_mode_counts(relevant),
        semantic_match_counts=semantic_match_counts(relevant),
        average_confidence=round(sum(confidences) / len(confidences), 2) if confidences else 0.0,
        retrieval_adjustment=usage_adjustment(relevant),
    )


def use_review(receipts: list[MemoryUseReceipt], memories: list[Memory], memory_id: str = "") -> UseReviewReport:
    normalized_id = memory_id.strip()
    memory_by_id = {memory.id: memory for memory in memories}
    ids = [normalized_id] if normalized_id else sorted({receipt.memory_id for receipt in receipts})
    cards: list[UseReviewCard] = []
    for current_id in ids:
        relevant = [receipt for receipt in receipts if receipt.memory_id == current_id]
        if not relevant and normalized_id:
            memory = memory_by_id.get(current_id)
            title = memory.title if memory is not None else ""
            cards.append(empty_use_review_card(memory, current_id, title))
            continue
        if not relevant:
            continue
        memory = memory_by_id.get(current_id)
        card = build_use_review_card(memory, relevant)
        if normalized_id or should_surface_review_card(card):
            cards.append(card)
    return UseReviewReport(cards=cards, memory_id=normalized_id)


def use_threshold_report(receipts: list[MemoryUseReceipt], memories: list[Memory]) -> UseThresholdReport:
    memory_by_id = {memory.id: memory for memory in memories}
    diagnostics: list[UseThresholdMemoryDiagnostic] = []
    for memory_id in sorted({receipt.memory_id for receipt in receipts}):
        relevant = [receipt for receipt in receipts if receipt.memory_id == memory_id]
        memory = memory_by_id.get(memory_id)
        card = build_use_review_card(memory, relevant)
        diagnostics.append(
            UseThresholdMemoryDiagnostic(
                memory_id=memory_id,
                memory_title=card.memory_title,
                memory_type=memory.type.value if memory is not None else "unknown",
                total_uses=card.total_uses,
                linked_uses=card.linked_uses,
                strong_committed=card.strong_committed,
                drag_signals=card.drag_signals,
                resolved_without_commit=card.resolved_without_commit,
                source_counts=source_counts(relevant),
                semantic_mode_counts=semantic_mode_counts(relevant),
                semantic_match_counts=semantic_match_counts(relevant),
                retrieval_adjustment=usage_adjustment(relevant),
                status=card.status,
                suggested_action=card.suggested_action,
            )
        )
    diagnostics.sort(key=lambda item: (item.status != "Review suggested", item.status != "Strengthen evidence suggested", item.memory_title))
    linked = [receipt for receipt in receipts if receipt.commit_hash or receipt.outcome_signal]
    return UseThresholdReport(
        total_receipts=len(receipts),
        linked_receipts=len(linked),
        unlinked_receipts=len(receipts) - len(linked),
        source_counts=source_counts(receipts),
        semantic_mode_counts=semantic_mode_counts(receipts),
        semantic_match_counts=semantic_match_counts(receipts),
        diagnostics=diagnostics,
    )


def semantic_audit(receipts: list[MemoryUseReceipt], memories: list[Memory], memory_id: str = "") -> SemanticAuditReport:
    normalized_id = memory_id.strip()
    memory_by_id = {memory.id: memory for memory in memories}
    scoped_receipts = [receipt for receipt in receipts if receipt.memory_id == normalized_id] if normalized_id else receipts
    memory = memory_by_id.get(normalized_id) if normalized_id else None
    title = memory.title if memory is not None else ""
    if normalized_id and not title:
        for receipt in scoped_receipts:
            if receipt.memory_title:
                title = receipt.memory_title
                break
    semantic_receipts = [receipt for receipt in scoped_receipts if is_semantic_assisted(receipt)]
    linked = [receipt for receipt in semantic_receipts if receipt.commit_hash or receipt.outcome_signal]
    resolved = [receipt for receipt in semantic_receipts if is_resolved_without_commit(receipt)]
    unresolved = [receipt for receipt in semantic_receipts if not receipt.commit_hash and not receipt.outcome_signal]
    strong = [
        receipt
        for receipt in linked
        if receipt.outcome_signal == "committed" and receipt.link_confidence >= STRONG_COMMIT_CONFIDENCE
    ]
    drag = [receipt for receipt in linked if is_drag_signal(receipt)]
    return SemanticAuditReport(
        total_receipts=len(scoped_receipts),
        semantic_receipts=len(semantic_receipts),
        semantic_linked=len(linked),
        semantic_resolved_without_commit=len(resolved),
        semantic_unresolved=len(unresolved),
        semantic_strong_committed=len(strong),
        semantic_drag_signals=len(drag),
        semantic_mode_counts=semantic_mode_counts(semantic_receipts),
        semantic_match_counts=semantic_match_counts(semantic_receipts),
        memory_id=normalized_id,
        memory_title=title,
        strong_memories=semantic_audit_memory_lines(strong, memory_by_id, signal="strong"),
        drag_memories=semantic_audit_memory_lines(drag, memory_by_id, signal="drag"),
    )


def semantic_audit_recommendations(
    receipts: list[MemoryUseReceipt],
    memories: list[Memory],
    *,
    root: Path | str = ".",
    details: bool = False,
    limit: int = 20,
    hours: int = 72,
    min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
    open_only: bool = False,
    commands_only: bool = False,
    receipt_id: str = "",
) -> SemanticAuditRecommendationsReport:
    memory_by_id = {memory.id: memory for memory in memories}
    receipt_ids = {receipt.memory_id for receipt in receipts}
    memory_ids = sorted(set(memory_by_id) | receipt_ids)
    report = SemanticAuditRecommendationsReport(
        open_only=open_only,
        commands_only=commands_only,
        receipt_id=receipt_id,
        limit=limit,
        hours=hours,
        min_score=min_score,
    )
    commits: list[GitCommitMetadata] = []
    detail_error = ""
    if details:
        try:
            commits = inspect_recent_git_commits(root, limit=limit)
        except RuntimeError as error:
            detail_error = str(error)
    for memory_id in memory_ids:
        relevant = [receipt for receipt in receipts if receipt.memory_id == memory_id]
        semantic_receipts = [receipt for receipt in relevant if is_semantic_assisted(receipt)]
        linked = [receipt for receipt in semantic_receipts if receipt.commit_hash or receipt.outcome_signal]
        resolved = [receipt for receipt in semantic_receipts if is_resolved_without_commit(receipt)]
        unresolved = [receipt for receipt in semantic_receipts if not receipt.commit_hash and not receipt.outcome_signal]
        strong = [
            receipt
            for receipt in linked
            if receipt.outcome_signal == "committed" and receipt.link_confidence >= STRONG_COMMIT_CONFIDENCE
        ]
        drag = [receipt for receipt in linked if is_drag_signal(receipt)]
        memory = memory_by_id.get(memory_id)
        title = memory.title if memory is not None else next((receipt.memory_title for receipt in relevant if receipt.memory_title), "")
        line = SemanticAuditRecommendationLine(
            memory_id=memory_id,
            memory_title=title,
            semantic_receipts=len(semantic_receipts),
            semantic_linked=len(linked),
            semantic_resolved_without_commit=len(resolved),
            semantic_unresolved=len(unresolved),
            semantic_strong_committed=len(strong),
            semantic_drag_signals=len(drag),
            action=semantic_recommendation_action(len(semantic_receipts), len(linked), len(strong), len(drag), len(resolved)),
            details=semantic_receipt_details(
                semantic_receipts,
                memory,
                commits=commits,
                detail_error=detail_error,
                hours=hours,
                min_score=min_score,
                open_only=open_only,
                receipt_id=receipt_id,
            )
            if details and semantic_receipts
            else [],
        )
        if not semantic_receipts:
            report.no_semantic.append(line)
        elif not linked:
            report.no_link.append(line)
        elif drag:
            report.drag.append(line)
        elif unresolved:
            report.partial.append(line)
        elif strong:
            report.strong.append(line)
        else:
            report.neutral.append(line)
    sort_recommendations(report.no_link)
    sort_recommendations(report.partial)
    sort_recommendations(report.drag)
    sort_recommendations(report.strong)
    sort_recommendations(report.neutral)
    sort_recommendations(report.no_semantic)
    return report


def semantic_receipt_details(
    receipts: list[MemoryUseReceipt],
    memory: Memory | None,
    *,
    commits: list[GitCommitMetadata],
    detail_error: str,
    hours: int,
    min_score: float,
    open_only: bool = False,
    receipt_id: str = "",
) -> list[SemanticAuditReceiptDetail]:
    relevant = [receipt for receipt in receipts if not receipt.commit_hash and not receipt.outcome_signal] if open_only else receipts
    if receipt_id:
        relevant = [receipt for receipt in relevant if receipt.id == receipt_id]
    return [
        semantic_receipt_detail(
            receipt,
            memory,
            commits=commits,
            detail_error=detail_error,
            hours=hours,
            min_score=min_score,
        )
        for receipt in sorted(relevant, key=lambda item: item.surfaced_at)
    ]


def semantic_receipt_detail(
    receipt: MemoryUseReceipt,
    memory: Memory | None,
    *,
    commits: list[GitCommitMetadata],
    detail_error: str,
    hours: int,
    min_score: float,
) -> SemanticAuditReceiptDetail:
    if receipt.commit_hash or receipt.outcome_signal:
        resolved = is_resolved_without_commit(receipt)
        return SemanticAuditReceiptDetail(
            receipt_id=receipt.id,
            source_command=receipt.source_command,
            semantic_mode=receipt.semantic_mode,
            semantic_status=receipt.semantic_proposal_status,
            semantic_score=receipt.semantic_score,
            linked=True,
            resolved_without_commit=resolved,
            commit_hash=receipt.commit_hash,
            outcome_signal=receipt.outcome_signal,
            link_confidence=receipt.link_confidence,
            auto_link_reason=receipt.metadata_source if resolved else "",
        )
    if detail_error:
        return SemanticAuditReceiptDetail(
            receipt_id=receipt.id,
            source_command=receipt.source_command,
            semantic_mode=receipt.semantic_mode,
            semantic_status=receipt.semantic_proposal_status,
            semantic_score=receipt.semantic_score,
            linked=False,
            auto_link_reason=detail_error,
        )
    candidates = sorted(
        [
            candidate
            for candidate in (score_auto_link_candidate(receipt, memory, commit, hours=hours) for commit in commits)
            if candidate.score >= min_score
        ],
        key=lambda item: item.score,
        reverse=True,
    )
    if not candidates:
        return SemanticAuditReceiptDetail(
            receipt_id=receipt.id,
            source_command=receipt.source_command,
            semantic_mode=receipt.semantic_mode,
            semantic_status=receipt.semantic_proposal_status,
            semantic_score=receipt.semantic_score,
            linked=False,
            auto_link_reason="no recent commit crossed the auto-link threshold",
        )
    best = candidates[0]
    close = [candidate for candidate in candidates[1:] if best.score - candidate.score <= AUTO_LINK_AMBIGUITY_MARGIN]
    shown = [best, *close] if close else [best]
    reason = (
        "multiple commits were plausible; leaving receipt unlinked"
        if close
        else "one commit currently crosses the auto-link threshold"
    )
    return SemanticAuditReceiptDetail(
        receipt_id=receipt.id,
        source_command=receipt.source_command,
        semantic_mode=receipt.semantic_mode,
        semantic_status=receipt.semantic_proposal_status,
        semantic_score=receipt.semantic_score,
        linked=False,
        auto_link_reason=reason,
        candidate_commits=[
            SemanticAuditCommitCandidateDetail(
                commit_hash=candidate.commit.commit_hash,
                message=candidate.commit.message,
                commit_time=candidate.commit.commit_time,
                files=candidate.commit.files,
                overlap=file_overlap(receipt.files, candidate.commit.files),
                score=candidate.score,
                reasons=candidate.reasons,
            )
            for candidate in shown
        ],
    )


def semantic_recommendation_action(semantic_receipts: int, linked: int, strong: int, drag: int, resolved: int = 0) -> str:
    if semantic_receipts == 0:
        return "stay quiet until semantic-assisted receipts exist"
    if linked == 0:
        return "link receipts first before judging semantic fit"
    if drag:
        return "inspect semantic grounding before broadening semantic behavior"
    if semantic_receipts > linked:
        return "resolve remaining semantic receipts before judging semantic fit"
    if strong:
        return "keep collecting focused evidence; possible positive semantic signal"
    if resolved:
        return "keep observing; semantic receipts were resolved without commit evidence"
    return "keep observing; linked semantic evidence is not strong or drag yet"


def sort_recommendations(lines: list[SemanticAuditRecommendationLine]) -> None:
    lines.sort(
        key=lambda item: (
            -item.semantic_drag_signals,
            -item.semantic_strong_committed,
            -item.semantic_linked,
            item.memory_title,
        )
    )


def render_recommendation_group(lines: list[SemanticAuditRecommendationLine]) -> list[str]:
    if not lines:
        return ["- None"]
    return [line.render() for line in lines]


def semantic_audit_memory_lines(
    receipts: list[MemoryUseReceipt],
    memory_by_id: dict[str, Memory],
    *,
    signal: str,
) -> list[SemanticAuditMemoryLine]:
    counts: dict[str, int] = {}
    titles: dict[str, str] = {}
    for receipt in receipts:
        counts[receipt.memory_id] = counts.get(receipt.memory_id, 0) + 1
        memory = memory_by_id.get(receipt.memory_id)
        titles[receipt.memory_id] = memory.title if memory is not None else receipt.memory_title
    lines = [
        SemanticAuditMemoryLine(
            memory_id=memory_id,
            memory_title=titles.get(memory_id, ""),
            strong_committed=count if signal == "strong" else 0,
            drag_signals=count if signal == "drag" else 0,
        )
        for memory_id, count in counts.items()
    ]
    if signal == "strong":
        return sorted(lines, key=lambda item: (-item.strong_committed, item.memory_title))
    return sorted(lines, key=lambda item: (-item.drag_signals, item.memory_title))


def source_counts(receipts: list[MemoryUseReceipt]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for receipt in receipts:
        source = receipt.source_command.strip() or "preflight"
        counts[source] = counts.get(source, 0) + 1
    return counts


def format_source_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{source}={counts[source]}" for source in sorted(counts))


def semantic_mode_counts(receipts: list[MemoryUseReceipt]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for receipt in receipts:
        mode = receipt.semantic_mode.strip() or "off"
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def semantic_match_counts(receipts: list[MemoryUseReceipt]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for receipt in receipts:
        if receipt.semantic_mode.strip() and receipt.semantic_mode.strip() != "off":
            status = receipt.semantic_proposal_status.strip() or "unknown"
        else:
            status = "off"
        counts[status] = counts.get(status, 0) + 1
    return counts


def is_semantic_assisted(receipt: MemoryUseReceipt) -> bool:
    return bool(receipt.semantic_mode.strip() and receipt.semantic_mode.strip() != "off")


def threshold_functionality_status(total_receipts: int, linked_receipts: int) -> str:
    if total_receipts == 0:
        return "No use receipts yet; run preflight before checking receipt behavior."
    if linked_receipts == 0:
        return "Use receipts exist, but no commit/checkpoint links exist yet."
    return "Use receipts and linked commit/checkpoint signals exist."


def threshold_accuracy_status(total_receipts: int, linked_receipts: int, linked_ratio: float) -> str:
    if total_receipts == 0:
        return "Not enough evidence to judge matching or review accuracy."
    if linked_receipts == 0:
        return "Indeterminate; link receipts before tuning thresholds."
    if linked_receipts < max(3, STRENGTHEN_MIN_STRONG_COMMITS):
        return "Early signal only; inspect individual links before tuning thresholds."
    if linked_ratio < 0.5:
        return "Partial signal; many receipts remain unlinked, so accuracy is still uncertain."
    return "Enough linked evidence for a first-pass threshold review, but inspect false positives and false negatives before tuning."


def prepare_use_review_followup(
    receipts: list[MemoryUseReceipt],
    memories: list[Memory],
    memory_id: str,
    action: str,
    *,
    apply: bool = False,
    approved_by: str = "",
    mismatch: str = "",
    benefit: str = "",
    risk: str = "",
    rollback: str = "",
    challenged_by: str = "",
    evidence: list[str] | None = None,
    proposed_scope: MemoryScope | None = None,
) -> UseReviewFollowUp:
    normalized_action = action.strip().lower()
    memory = find_memory(memories, memory_id)
    report = use_review(receipts, memories, memory_id)
    card = report.cards[0] if report.cards else None
    if card is None:
        return UseReviewFollowUp(action=normalized_action, applied=False, reason="no use review card could be prepared", memory=memory)
    if normalized_action == "strengthen":
        return prepare_strengthen_followup(card, memory, apply=apply, approved_by=approved_by)
    if normalized_action == "challenge":
        return prepare_challenge_followup(
            card,
            memory,
            memories,
            apply=apply,
            mismatch=mismatch,
            benefit=benefit,
            risk=risk,
            rollback=rollback,
            challenged_by=challenged_by,
            evidence=evidence or [],
        )
    if normalized_action == "scope-review":
        return prepare_scope_review_followup(
            card,
            memory,
            apply=apply,
            approved_by=approved_by,
            proposed_scope=proposed_scope or MemoryScope(),
        )
    return UseReviewFollowUp(
        action=normalized_action,
        applied=False,
        reason=f"unsupported use-review follow-up action: {action}",
        card=card,
        memory=memory,
    )


def prepare_strengthen_followup(
    card: UseReviewCard,
    memory: Memory,
    *,
    apply: bool,
    approved_by: str,
) -> UseReviewFollowUp:
    if card.strong_committed < STRENGTHEN_MIN_STRONG_COMMITS or card.drag_signals:
        return UseReviewFollowUp(
            action="strengthen",
            applied=False,
            reason="strengthen requires repeated high-confidence committed uses with no drag signals",
            card=card,
            memory=memory,
        )
    evidence = strengthen_evidence(card, approved_by)
    missing: list[str] = []
    if apply and not approved_by.strip():
        missing.append("approved_by")
        return UseReviewFollowUp(
            action="strengthen",
            applied=False,
            reason="approved follow-up requires explicit owner/team approval",
            card=card,
            memory=memory,
            evidence=evidence,
            missing=missing,
        )
    if apply:
        for item in evidence:
            if item not in memory.evidence:
                memory.evidence.append(item)
        memory.confidence = round(min(1.0, max(memory.confidence, memory.confidence + 0.05)), 2)
        return UseReviewFollowUp(
            action="strengthen",
            applied=True,
            reason="Added approved use-review evidence to memory.",
            card=card,
            memory=memory,
            evidence=evidence,
        )
    return UseReviewFollowUp(
        action="strengthen",
        applied=False,
        reason="Prepared evidence that can strengthen this memory after approval.",
        card=card,
        memory=memory,
        evidence=evidence,
    )


def prepare_challenge_followup(
    card: UseReviewCard,
    memory: Memory,
    memories: list[Memory],
    *,
    apply: bool,
    mismatch: str,
    benefit: str,
    risk: str,
    rollback: str,
    challenged_by: str,
    evidence: list[str],
) -> UseReviewFollowUp:
    if memory.type not in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
        return UseReviewFollowUp(
            action="challenge",
            applied=False,
            reason="challenge follow-up only applies to Practice or Anchor memory",
            card=card,
            memory=memory,
        )
    if card.drag_signals < DRAG_REVIEW_MIN_SIGNALS and not mismatch.strip():
        return UseReviewFollowUp(
            action="challenge",
            applied=False,
            reason="challenge follow-up requires repeated drag signals or an explicit mismatch",
            card=card,
            memory=memory,
        )
    request = ChallengeRequest(
        memory_id=memory.id,
        mismatch=mismatch.strip() or default_challenge_mismatch(card),
        benefit=benefit.strip() or default_challenge_benefit(card),
        risk=risk.strip() or default_challenge_risk(card),
        rollback=rollback.strip() or default_challenge_rollback(card),
        challenged_by=challenged_by.strip() or "cmu use-review",
        evidence=dedupe_lists(evidence + challenge_followup_evidence(card)),
        confidence=0.65,
    )
    decision = challenge_stable_memory(memories, request)
    if not decision.saved:
        return UseReviewFollowUp(
            action="challenge",
            applied=False,
            reason=decision.reason,
            card=card,
            memory=memory,
            challenge_memory=decision.challenge_memory,
            missing=decision.missing or [],
        )
    return UseReviewFollowUp(
        action="challenge",
        applied=apply,
        reason="Challenge Candidate prepared from use-review drag evidence." if not apply else "Challenge Candidate saved from use-review drag evidence.",
        card=card,
        memory=memory,
        challenge_memory=decision.challenge_memory,
        evidence=request.evidence or [],
    )


def prepare_scope_review_followup(
    card: UseReviewCard,
    memory: Memory,
    *,
    apply: bool,
    approved_by: str,
    proposed_scope: MemoryScope,
) -> UseReviewFollowUp:
    current_scope = memory.scope
    new_scope = merged_scope(current_scope, proposed_scope)
    scope_changes = scope_change_summary(current_scope, new_scope)
    evidence = [
        f"Scope-review prompt from use-review: {card.drag_signals} drag signals across {card.linked_uses} linked uses.",
        f"Current scope: {format_list(current_scope.flattened())}",
        *semantic_followup_evidence(card),
    ]
    if scope_changes:
        evidence.append(f"Proposed scope: {format_list(new_scope.flattened())}")
        evidence.append(f"Scope changes: {format_list(scope_changes)}")
    else:
        evidence.append("Proposal only: provide --scope-* axes to apply an approved scope change.")
    if not apply:
        return UseReviewFollowUp(
            action="scope-review",
            applied=False,
            reason="Prepared scope review proposal; apply requires explicit scope axes and owner/team approval.",
            card=card,
            memory=memory,
            evidence=evidence,
        )

    missing: list[str] = []
    if not approved_by.strip():
        missing.append("approved_by")
    if not scope_changes:
        missing.append("scope_axes")
    if missing:
        return UseReviewFollowUp(
            action="scope-review",
            applied=False,
            reason="approved scope review requires explicit owner/team approval and at least one proposed scope axis",
            card=card,
            memory=memory,
            evidence=evidence,
            missing=missing,
        )
    if card.linked_uses == 0:
        return UseReviewFollowUp(
            action="scope-review",
            applied=False,
            reason="scope changes require linked Memory Use Receipt evidence",
            card=card,
            memory=memory,
            evidence=evidence,
            missing=["linked_use_evidence"],
        )
    if card.status != "Review suggested":
        return UseReviewFollowUp(
            action="scope-review",
            applied=False,
            reason="approved scope changes require a use-review scope signal such as repeated drag",
            card=card,
            memory=memory,
            evidence=evidence,
            missing=["review_suggested_signal"],
        )
    if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR} and not scope_change_is_safe_narrowing(current_scope, new_scope):
        return UseReviewFollowUp(
            action="scope-review",
            applied=False,
            reason="stable memory scope changes that broaden or shift scope require the challenge or split path",
            card=card,
            memory=memory,
            evidence=evidence,
            missing=["challenge_or_split_path"],
        )

    memory.scope = new_scope
    memory.updated_at = utc_now()
    approval_evidence = [
        f"Scope adjusted from use-review by: {approved_by.strip()}",
        f"Use-review scope signal: {card.signal_summary()}",
        f"Applied scope changes: {format_list(scope_changes)}",
    ]
    for item in approval_evidence:
        if item not in memory.evidence:
            memory.evidence.append(item)
    evidence.extend(approval_evidence)
    return UseReviewFollowUp(
        action="scope-review",
        applied=True,
        reason="Applied approved scope review to memory.",
        card=card,
        memory=memory,
        evidence=evidence,
    )


def merged_scope(current: MemoryScope, proposed: MemoryScope) -> MemoryScope:
    return MemoryScope(
        ownership=proposed.ownership or list(current.ownership),
        code=proposed.code or list(current.code),
        workflow=proposed.workflow or list(current.workflow),
        environment=proposed.environment or list(current.environment),
        actor=proposed.actor or list(current.actor),
        time=proposed.time or list(current.time),
    )


def scope_change_summary(current: MemoryScope, new_scope: MemoryScope) -> list[str]:
    changes: list[str] = []
    for axis in scope_axes():
        old_values = getattr(current, axis)
        new_values = getattr(new_scope, axis)
        if clean_scope_values(old_values) != clean_scope_values(new_values):
            changes.append(f"{axis}: {format_list(old_values)} -> {format_list(new_values)}")
    return changes


def scope_change_is_safe_narrowing(current: MemoryScope, new_scope: MemoryScope) -> bool:
    for axis in scope_axes():
        old_values = clean_scope_values(getattr(current, axis))
        new_values = clean_scope_values(getattr(new_scope, axis))
        if old_values == new_values:
            continue
        if not new_values:
            return False
        if not old_values:
            continue
        if not all(scope_value_is_within_existing_scope(value, old_values) for value in new_values):
            return False
    return True


def scope_value_is_within_existing_scope(value: str, old_values: list[str]) -> bool:
    normalized = normalize_scope_value(value)
    return any(normalize_scope_value(old) == normalized or normalize_scope_value(old) in normalized for old in old_values)


def clean_scope_values(values: list[str]) -> list[str]:
    return sorted({item.strip() for item in values if item.strip()})


def normalize_scope_value(value: str) -> str:
    return value.strip().replace("\\", "/").lower()


def scope_axes() -> list[str]:
    return ["ownership", "code", "workflow", "environment", "actor", "time"]


def build_use_review_card(memory: Memory | None, receipts: list[MemoryUseReceipt]) -> UseReviewCard:
    linked = [receipt for receipt in receipts if receipt.commit_hash or receipt.outcome_signal]
    committed = [receipt for receipt in linked if receipt.outcome_signal == "committed"]
    strong_committed = [receipt for receipt in committed if receipt.link_confidence >= STRONG_COMMIT_CONFIDENCE]
    checkpoints = [receipt for receipt in linked if receipt.outcome_signal == "checkpoint"]
    reverted = [receipt for receipt in linked if receipt.outcome_signal == "reverted"]
    low_confidence = [receipt for receipt in linked if receipt.outcome_signal == "committed_low_confidence"]
    mixed = [receipt for receipt in linked if "mixed_commit" in receipt.flags]
    resolved_without_commit = [receipt for receipt in linked if is_resolved_without_commit(receipt)]
    drag = [receipt for receipt in linked if is_drag_signal(receipt)]
    memory_id = receipts[0].memory_id
    title = memory.title if memory is not None else receipts[0].memory_title
    status, why, suggested_action = review_judgment(
        memory,
        len(receipts),
        len(linked),
        len(strong_committed),
        len(drag),
        len(reverted),
        len(mixed),
    )
    return UseReviewCard(
        memory=memory,
        memory_id=memory_id,
        memory_title=title,
        total_uses=len(receipts),
        linked_uses=len(linked),
        committed=len(committed),
        strong_committed=len(strong_committed),
        checkpoints=len(checkpoints),
        reverted=len(reverted),
        low_confidence=len(low_confidence),
        mixed=len(mixed),
        resolved_without_commit=len(resolved_without_commit),
        drag_signals=len(drag),
        status=status,
        why=why,
        suggested_action=suggested_action,
        source_counts=source_counts(receipts),
        semantic_mode_counts=semantic_mode_counts(receipts),
        semantic_match_counts=semantic_match_counts(receipts),
        semantic_strong_committed=sum(1 for receipt in strong_committed if is_semantic_assisted(receipt)),
        semantic_drag_signals=sum(1 for receipt in drag if is_semantic_assisted(receipt)),
    )


def empty_use_review_card(memory: Memory | None, memory_id: str, title: str) -> UseReviewCard:
    return UseReviewCard(
        memory=memory,
        memory_id=memory_id,
        memory_title=title,
        total_uses=0,
        linked_uses=0,
        committed=0,
        strong_committed=0,
        checkpoints=0,
        reverted=0,
        low_confidence=0,
        mixed=0,
        resolved_without_commit=0,
        drag_signals=0,
        status="No use evidence found",
        why="No Memory Use Receipts exist for this memory yet.",
        suggested_action="Let preflight create receipts and link them before reviewing usefulness.",
        source_counts={},
        semantic_mode_counts={},
        semantic_match_counts={},
    )


def review_judgment(
    memory: Memory | None,
    total_uses: int,
    linked_uses: int,
    strong_committed: int,
    drag_signals: int,
    reverted: int,
    mixed: int = 0,
) -> tuple[str, str, str]:
    if linked_uses == 0:
        return (
            "Needs linked evidence",
            f"{total_uses} uses exist, but none are linked to Git checkpoint signals.",
            "Run use-link-auto or use-link before judging usefulness.",
        )
    if drag_signals >= DRAG_REVIEW_MIN_SIGNALS or (
        linked_uses >= DRAG_REVIEW_RATIO_MIN_USES and drag_signals / linked_uses >= DRAG_REVIEW_RATIO
    ):
        action = "Review scope and wording; narrow this memory if it is surfacing too broadly."
        why = f"{drag_signals} drag signals across {linked_uses} linked uses."
        if mixed == drag_signals and strong_committed:
            action = "Inspect broad mixed commits before challenging this memory; keep collecting focused linked uses unless drag continues."
            why = (
                f"{drag_signals} drag signals across {linked_uses} linked uses, all from mixed commits, "
                f"with {strong_committed} strong focused uses."
            )
        if memory is not None and memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR}:
            action = "Use the challenge path or narrow scope before trusting this stable memory further."
            if mixed == drag_signals and strong_committed:
                action = "Inspect broad mixed commits before challenging this stable memory; keep collecting focused linked uses unless drag continues."
        if reverted:
            action = "Review for challenge or retirement; at least one linked use was reverted."
        return (
            "Review suggested",
            why,
            action,
        )
    if strong_committed >= STRENGTHEN_MIN_STRONG_COMMITS and drag_signals == 0:
        return (
            "Strengthen evidence suggested",
            f"{strong_committed} high-confidence committed uses with no drag signals.",
            "Consider strengthening evidence or promotion readiness if the scope is still accurate.",
        )
    return (
        "No review needed",
        f"{linked_uses} linked uses do not show repeated usefulness or repeated drag yet.",
        "Keep collecting use receipts before changing trust or scope.",
    )


def strengthen_evidence(card: UseReviewCard, approved_by: str) -> list[str]:
    evidence = [
        f"Use-review strengthened evidence: {card.strong_committed} high-confidence committed uses across {card.linked_uses} linked uses.",
        f"Use-review signal summary: {card.signal_summary()}",
        *semantic_followup_evidence(card),
    ]
    if approved_by.strip():
        evidence.append(f"Use-review evidence approved by: {approved_by.strip()}")
    return evidence


def default_challenge_mismatch(card: UseReviewCard) -> str:
    return f"Use-review found {card.drag_signals} drag signals across {card.linked_uses} linked uses for this stable memory."


def default_challenge_benefit(card: UseReviewCard) -> str:
    return "Review whether this stable memory should be narrowed, excepted, retired, split, or strengthened based on linked use evidence."


def default_challenge_risk(card: UseReviewCard) -> str:
    return "Leaving a noisy stable memory unchanged may keep guiding future work poorly; changing it too broadly may discard useful precedent."


def default_challenge_rollback(card: UseReviewCard) -> str:
    return "Keep the stable memory unchanged until an approved challenge resolution provides stronger evidence."


def challenge_followup_evidence(card: UseReviewCard) -> list[str]:
    return [
        f"Use-review challenge signal: {card.signal_summary()}",
        f"Use-review reason: {card.why}",
        *semantic_followup_evidence(card),
    ]


def semantic_followup_evidence(card: UseReviewCard) -> list[str]:
    semantic_total = sum(count for mode, count in card.semantic_mode_counts.items() if mode != "off")
    if semantic_total == 0:
        return []
    evidence = [
        f"Semantic provenance: modes {format_source_counts(card.semantic_mode_counts)}; matches {format_source_counts(card.semantic_match_counts)}."
    ]
    if card.semantic_strong_committed:
        evidence.append(f"Semantic-assisted strong committed uses: {card.semantic_strong_committed}.")
    if card.semantic_drag_signals:
        evidence.append(f"Semantic-assisted drag signals: {card.semantic_drag_signals}.")
    return evidence


def should_surface_review_card(card: UseReviewCard) -> bool:
    return card.status in {"Review suggested", "Strengthen evidence suggested"}


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def dedupe_lists(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def apply_usage_adjustments(matches: list[Match], receipts: list[MemoryUseReceipt]) -> list[Match]:
    adjusted: list[Match] = []
    for match in matches:
        adjustment = usage_adjustment([receipt for receipt in receipts if receipt.memory_id == match.memory.id])
        adjusted.append(
            Match(
                memory=match.memory,
                score=round(max(0.0, match.score + adjustment), 3),
                matched_terms=match.matched_terms,
                score_breakdown=match.score_breakdown + usage_adjustment_breakdown(adjustment),
                semantic_label=match.semantic_label,
                semantic_score=match.semantic_score,
                semantic_proposal_status=match.semantic_proposal_status,
                graph_source_id=match.graph_source_id,
                graph_source_title=match.graph_source_title,
                graph_relation_type=match.graph_relation_type,
                graph_relation_reason=match.graph_relation_reason,
            )
        )
    return sorted(adjusted, key=lambda item: item.score, reverse=True)


def usage_adjustment_breakdown(adjustment: float) -> list[str]:
    if adjustment == 0:
        return []
    sign = "+" if adjustment > 0 else ""
    return [f"use evidence adjustment: {sign}{adjustment:.2f}"]


def usage_adjustment(receipts: list[MemoryUseReceipt]) -> float:
    adjustment = 0.0
    for receipt in receipts:
        if receipt.outcome_signal == "committed" and receipt.link_confidence >= STRONG_COMMIT_CONFIDENCE:
            adjustment += USAGE_STRONG_COMMIT_WEIGHT
        elif receipt.outcome_signal == "checkpoint":
            adjustment += USAGE_CHECKPOINT_WEIGHT
        elif receipt.outcome_signal == "reverted":
            adjustment += USAGE_REVERTED_WEIGHT
        elif receipt.outcome_signal == "committed_low_confidence":
            adjustment += USAGE_LOW_CONFIDENCE_WEIGHT
        if "mixed_commit" in receipt.flags:
            adjustment += USAGE_MIXED_COMMIT_WEIGHT
        if "no_file_overlap" in receipt.flags:
            adjustment += USAGE_NO_FILE_OVERLAP_WEIGHT
    return round(max(-USAGE_ADJUSTMENT_CAP, min(adjustment, USAGE_ADJUSTMENT_CAP)), 2)


def inspect_git_commit(root: Path | str, ref: str = "HEAD") -> GitCommitMetadata:
    commit_hash = run_git(root, ["rev-parse", "--verify", ref]).strip()
    message = run_git(root, ["show", "-s", "--format=%B", commit_hash]).strip()
    commit_time = run_git(root, ["show", "-s", "--format=%cI", commit_hash]).strip()
    files_output = run_git(root, ["show", "--name-only", "--format=", commit_hash])
    return GitCommitMetadata(
        commit_hash=commit_hash,
        message=message,
        files=[line.strip() for line in files_output.splitlines() if line.strip()],
        commit_time=commit_time,
    )


def inspect_recent_git_commits(root: Path | str, limit: int = 20) -> list[GitCommitMetadata]:
    output = run_git(root, ["log", f"--max-count={max(1, limit)}", "--format=%H"])
    commits: list[GitCommitMetadata] = []
    for line in output.splitlines():
        ref = line.strip()
        if ref:
            commits.append(inspect_git_commit(root, ref))
    return commits


def run_git(root: Path | str, args: list[str]) -> str:
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError(f"git command unavailable: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(format_git_metadata_error(root, detail or "command failed"))
    return result.stdout


def format_git_metadata_error(root: Path | str, detail: str) -> str:
    normalized = " ".join(detail.split())
    hint = ""
    lower = normalized.lower()
    if "cannot change to" in lower:
        hint = (
            "Git discovered a repository root that this process cannot access from the CMU root. "
            "Run from a Git-accessible project root, make this folder a standalone repo, or use manual commit metadata."
        )
    elif "dubious ownership" in lower:
        hint = (
            "Git refused the repository ownership trust check. "
            "Use a trusted project root or configure safe.directory before relying on automatic Git linking."
        )
    elif "not a git repository" in lower:
        hint = "Automatic linking needs a Git repository; use manual metadata or initialize/use a repo root."
    location = f"root={Path(root)}"
    if hint:
        return f"git metadata unavailable ({location}): {normalized} Hint: {hint}"
    return f"git metadata unavailable ({location}): {normalized}"


def missing_link_fields(request: CommitLinkRequest) -> list[str]:
    missing: list[str] = []
    if not request.use_id.strip():
        missing.append("use_id")
    if not request.commit_hash.strip():
        missing.append("commit_hash")
    return missing


def link_flags(receipt: MemoryUseReceipt) -> list[str]:
    flags: list[str] = []
    message = receipt.commit_message.lower()
    message_terms = set(re.findall(r"[a-z0-9_-]+", message))
    if message.startswith("revert") or "reverts commit" in message or "this reverts" in message:
        flags.append("reverted_after_use")
    if message_terms & WIP_TERMS:
        flags.append("wip_commit")
    if not receipt.commit_files:
        flags.append("no_commit_file_context")
    else:
        overlap = file_overlap(receipt.files, receipt.commit_files)
        if not overlap:
            flags.append("no_file_overlap")
        elif len(receipt.commit_files) >= max(4, len(overlap) * 3):
            flags.append("mixed_commit")
    if is_delayed(receipt):
        flags.append("delayed_commit")
    return flags


def outcome_signal(flags: list[str]) -> str:
    if "reverted_after_use" in flags:
        return "reverted"
    if "wip_commit" in flags:
        return "checkpoint"
    if "no_file_overlap" in flags:
        return "committed_low_confidence"
    return "committed"


def link_confidence(receipt: MemoryUseReceipt) -> float:
    confidence = 0.45
    if receipt.commit_files:
        overlap = file_overlap(receipt.files, receipt.commit_files)
        if overlap:
            confidence += 0.35
        else:
            confidence -= 0.2
    else:
        confidence -= 0.1
    if receipt.risk == "high":
        confidence += 0.05
    if "mixed_commit" in receipt.flags:
        confidence -= 0.15
    if "delayed_commit" in receipt.flags:
        confidence -= 0.1
    if "wip_commit" in receipt.flags:
        confidence = min(confidence, 0.55)
    if "reverted_after_use" in receipt.flags:
        confidence = min(confidence, 0.2)
    return round(max(0.05, min(confidence, 0.95)), 2)


def score_auto_link_candidate(
    receipt: MemoryUseReceipt,
    memory: Memory | None,
    commit: GitCommitMetadata,
    *,
    hours: int,
) -> AutoLinkCandidate:
    score = 0.0
    reasons: list[str] = []
    time_score = auto_time_score(receipt, commit, hours)
    if time_score <= 0:
        return AutoLinkCandidate(receipt=receipt, commit=commit, score=0.0, reasons=["outside time window"])
    score += time_score
    reasons.append("time window")

    overlap = file_overlap(receipt.files, commit.files)
    if overlap:
        score += 0.4
        reasons.append(f"file overlap: {format_list(overlap[:3])}")
    elif receipt.files and commit.files:
        score -= 0.15
        reasons.append("no file overlap")

    commit_terms = tokenize_for_auto_link(" ".join([commit.message, " ".join(commit.files)]))
    task_terms = tokenize_for_auto_link(" ".join([receipt.prompt, receipt.area, " ".join(receipt.files)]))
    task_overlap = sorted(task_terms & commit_terms)
    if task_overlap:
        score += min(0.25, 0.07 * len(task_overlap))
        reasons.append(f"task terms: {format_list(task_overlap[:4])}")

    scope_terms = memory_scope_terms(memory)
    scope_overlap = sorted(scope_terms & commit_terms)
    if scope_overlap:
        score += min(0.2, 0.06 * len(scope_overlap))
        reasons.append(f"memory scope: {format_list(scope_overlap[:4])}")

    return AutoLinkCandidate(receipt=receipt, commit=commit, score=round(max(0.0, min(score, 1.0)), 2), reasons=reasons)


def auto_time_score(receipt: MemoryUseReceipt, commit: GitCommitMetadata, hours: int) -> float:
    surfaced = parse_iso(receipt.surfaced_at)
    committed = parse_iso(commit.commit_time)
    if surfaced is None or committed is None:
        return 0.1
    delta_seconds = (committed - surfaced).total_seconds()
    if delta_seconds < -5 * 60 or delta_seconds > max(1, hours) * 60 * 60:
        return 0.0
    if delta_seconds <= 6 * 60 * 60:
        return 0.3
    if delta_seconds <= 24 * 60 * 60:
        return 0.22
    return 0.12


def memory_scope_terms(memory: Memory | None) -> set[str]:
    if memory is None:
        return set()
    return tokenize_for_auto_link(" ".join(memory.scope.flattened()))


def tokenize_for_auto_link(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_./-]+", text.lower()) if len(token) > 2}


def drag_review_prompts(receipts: list[MemoryUseReceipt], memories: list[Memory]) -> list[str]:
    memory_by_id = {memory.id: memory for memory in memories}
    prompts: list[str] = []
    for memory_id in sorted({receipt.memory_id for receipt in receipts}):
        relevant = [receipt for receipt in receipts if receipt.memory_id == memory_id]
        drag = [receipt for receipt in relevant if is_drag_signal(receipt)]
        if len(drag) < DRAG_REVIEW_MIN_SIGNALS:
            continue
        memory = memory_by_id.get(memory_id)
        title = memory.title if memory is not None else relevant[0].memory_title
        prompts.append(
            f"{memory_id} {title}: {len(drag)} drag signals across {len(relevant)} uses; review scope, clarity, or usefulness."
        )
    return prompts


def is_drag_signal(receipt: MemoryUseReceipt) -> bool:
    if is_resolved_without_commit(receipt):
        return False
    return (
        receipt.outcome_signal in {"reverted", "committed_low_confidence"}
        or "mixed_commit" in receipt.flags
        or "no_file_overlap" in receipt.flags
        or "no_commit_file_context" in receipt.flags
    )


def is_resolved_without_commit(receipt: MemoryUseReceipt) -> bool:
    return (
        receipt.outcome_signal in RESOLVED_WITHOUT_COMMIT_OUTCOMES
        or RESOLVED_WITHOUT_COMMIT_FLAG in receipt.flags
    ) and not receipt.commit_hash


def append_flag(receipt: MemoryUseReceipt, flag: str) -> None:
    if flag not in receipt.flags:
        receipt.flags.append(flag)


def is_delayed(receipt: MemoryUseReceipt) -> bool:
    if not receipt.commit_time or not receipt.surfaced_at:
        return False
    surfaced = parse_iso(receipt.surfaced_at)
    committed = parse_iso(receipt.commit_time)
    if surfaced is None or committed is None:
        return False
    return (committed - surfaced).total_seconds() > 24 * 60 * 60


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def file_overlap(receipt_files: list[str], commit_files: list[str]) -> list[str]:
    overlap: list[str] = []
    for receipt_file in clean_list(receipt_files):
        for commit_file in clean_list(commit_files):
            if receipt_file in commit_file or commit_file in receipt_file:
                overlap.append(commit_file)
    return sorted(set(overlap))


def clean_list(values: list[str]) -> list[str]:
    return [item.strip().replace("\\", "/").lower() for item in values if item.strip()]


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "None"


def short_hash(value: str) -> str:
    return value[:12] if value else "unknown"
