from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory
from .retrieval import (
    DEFAULT_SEMANTIC_INDEX,
    Match,
    PreflightQuery,
    SemanticIndex,
    action_threshold,
    build_action_note,
    context_signal_score,
    memory_text,
    rank_memories,
    scope_conflicts_with_query,
    semantic_proposal_diagnostics,
    semantic_proposal_grounding,
    semantic_proposal_status,
    semantic_signal_score,
    tokenize,
)
from .usage import MemoryUseReceipt, apply_usage_adjustments


@dataclass
class PipelineLine:
    memory_id: str
    title: str
    memory_type: str
    phase: str
    status: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"- {self.memory_id} [{self.memory_type}] {self.title}",
            f"  Phase: {self.phase}",
            f"  Status: {self.status}",
            f"  Score: {self.score:.3f}",
            f"  Reasons: {format_list(self.reasons)}",
        ]
        return "\n".join(lines)


@dataclass
class HybridPipelineReport:
    query: PreflightQuery
    threshold: float
    lines: list[PipelineLine]
    selected: Match | None = None
    action_note_preview: str = ""

    def render(self) -> str:
        lines = [
            "CMU Hybrid Retrieval Pipeline",
            "Mode: read-only retrieval proof; no memories or receipts are mutated.",
            f"Prompt: {self.query.prompt}",
            f"Actor: {self.query.actor}",
            f"Area: {self.query.area or 'none'}",
            f"Files: {format_list(self.query.files or [])}",
            f"Workflow: {format_list(self.query.workflow or [])}",
            f"Environment: {format_list(self.query.environment or [])}",
            f"Risk: {self.query.risk}",
            f"Action Threshold: {self.threshold:.2f}",
            "",
            "Summary:",
            f"- Memories Considered: {len(self.lines)}",
            f"- Actionable: {sum(1 for line in self.lines if line.status == 'actionable')}",
            f"- Below Threshold: {sum(1 for line in self.lines if line.status == 'below-threshold')}",
            f"- Rejected: {sum(1 for line in self.lines if line.status == 'rejected')}",
            f"- Graph Expanded: {sum(1 for line in self.lines if line.phase == 'graph expansion')}",
            f"- Semantic Admissible: {sum(1 for line in self.lines if 'semantic proposal admissible' in line.reasons)}",
            "",
            "Pipeline Lines:",
        ]
        if not self.lines:
            lines.append("- None")
        else:
            lines.extend(line.render() for line in self.lines)
        lines.extend(["", "Selected Action:"])
        if self.selected is None:
            lines.append("- quiet: no memory crossed the action threshold")
        else:
            lines.append(f"- action-note: {self.selected.memory.id} {self.selected.memory.title} (score {self.selected.score:.3f})")
            if self.action_note_preview:
                lines.extend(["", "Action Note Preview:", self.action_note_preview])
        lines.extend(
            [
                "",
                "Proof Meaning: this report connects candidate search, hard grounding, graph expansion, semantic support, evidence/liability ranking, and Action Note selection/rejection in one inspectable retrieval path.",
            ]
        )
        return "\n".join(lines)


def hybrid_pipeline_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    query: PreflightQuery,
    *,
    semantic_index: SemanticIndex | None = None,
) -> HybridPipelineReport:
    semantic_index = semantic_index or DEFAULT_SEMANTIC_INDEX
    threshold = action_threshold(query.risk)
    raw_matches = rank_memories(memories, query, semantic_index=semantic_index)
    ranked = apply_usage_adjustments(raw_matches, receipts)
    ranked_by_id = {match.memory.id: match for match in ranked}
    raw_by_id = {match.memory.id: match for match in raw_matches}
    semantic_by_id = {
        diagnostic.memory_id: diagnostic
        for diagnostic in semantic_proposal_diagnostics(memories, query, semantic_index, limit=max(5, len(memories)))
    }
    lines: list[PipelineLine] = []
    for memory in memories:
        match = ranked_by_id.get(memory.id)
        raw_match = raw_by_id.get(memory.id)
        if match is not None:
            phase = "graph expansion" if match.is_graph_expanded() else "direct ranking"
            status = "actionable" if match.score >= threshold else "below-threshold"
            reasons = pipeline_match_reasons(match, raw_match)
            semantic = semantic_by_id.get(memory.id)
            if semantic is not None and semantic.status == "admissible":
                reasons.append("semantic proposal admissible")
            lines.append(
                PipelineLine(
                    memory_id=memory.id,
                    title=memory.title,
                    memory_type=memory.type.value,
                    phase=phase,
                    status=status,
                    score=match.score,
                    reasons=reasons,
                )
            )
            continue
        lines.append(rejected_pipeline_line(memory, query, semantic_index, semantic_by_id.get(memory.id)))
    lines = sorted(lines, key=lambda line: (status_sort(line.status), -line.score, line.title))
    selected = next((match for match in ranked if match.score >= threshold), None)
    note_preview = build_action_note(selected).render() if selected is not None else ""
    return HybridPipelineReport(query=query, threshold=threshold, lines=lines, selected=selected, action_note_preview=note_preview)


def pipeline_match_reasons(match: Match, raw_match: Match | None) -> list[str]:
    reasons = []
    if match.is_graph_expanded():
        reasons.append(f"graph expansion via {match.graph_source_id} {match.graph_relation_type}")
    elif match.semantic_proposal_status == "admissible":
        reasons.append("semantic proposal admitted by grounding")
    else:
        reasons.append("direct candidate survived hard grounding")
    if match.matched_terms:
        reasons.append(f"matched {format_list(match.matched_terms[:5])}")
    if match.semantic_score:
        reasons.append(f"semantic support {match.semantic_label} {match.semantic_score:.3f}")
    if raw_match is not None and match.score != raw_match.score:
        reasons.append(f"use evidence adjusted score {raw_match.score:.3f}->{match.score:.3f}")
    return reasons


def rejected_pipeline_line(
    memory: Memory,
    query: PreflightQuery,
    semantic_index: SemanticIndex,
    semantic_diagnostic,
) -> PipelineLine:
    reasons = []
    if scope_conflicts_with_query(memory, query):
        reasons.append("hard grounding rejected: scope conflicts with query")
        phase = "hard grounding"
    else:
        query_terms = tokenize(query.text())
        overlap = sorted(query_terms & tokenize(memory_text(memory)))
        context_bonus = context_signal_score(memory, query)
        semantic_signal = semantic_signal_score(memory, query, semantic_index)
        grounding = semantic_proposal_grounding(memory, query, semantic_signal)
        status, reason = semantic_proposal_status(memory, query, semantic_signal, overlap, context_bonus, grounding)
        phase = "candidate search"
        if not overlap and context_bonus <= 0:
            reasons.append("candidate search found no text or hard-scope grounding")
        if semantic_diagnostic is not None:
            reasons.append(f"semantic {status}: {reason}")
        else:
            reasons.append(f"semantic {status}: {reason}")
    return PipelineLine(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        phase=phase,
        status="rejected",
        score=0.0,
        reasons=reasons,
    )


def status_sort(status: str) -> int:
    order = {"actionable": 0, "below-threshold": 1, "rejected": 2}
    return order.get(status, 9)


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
