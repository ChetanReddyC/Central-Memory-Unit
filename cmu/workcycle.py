from __future__ import annotations

from dataclasses import dataclass, field

from .analytics import analytics_card
from .governance import active_challenges_by_stable
from .models import Memory, MemoryScope, MemoryType
from .onboarding import OnboardingSeed, build_onboarding_seed
from .remembering import RememberDecision, RememberRequest, remember_candidate
from .retrieval import PreflightQuery, SemanticIndex, action_threshold, build_action_note, rank_memories
from .triggers import TriggerDecision, decide_trigger
from .usage import MemoryUseReceipt, apply_usage_adjustments


@dataclass
class WorkCycleRequest:
    prompt: str
    query: PreflightQuery
    repeated_error: bool = False
    uncertainty: bool = False
    shared_contract: bool = False
    irreversible: bool = False
    unfamiliar: bool = False
    learning_signals: list[str] = field(default_factory=list)
    outcome: str = ""
    worked: str = ""
    failed: str = ""
    future_use: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class WorkCycleCandidateDecision:
    status: str
    reason: str
    candidate_id: str = ""
    suggested_next_type: str = ""

    def render(self) -> str:
        candidate = self.candidate_id or "none"
        next_type = self.suggested_next_type or "none"
        return "\n".join(
            [
                f"Status: {self.status}",
                f"Reason: {self.reason}",
                f"Candidate: {candidate}",
                f"Suggested Next Type: {next_type}",
            ]
        )


@dataclass
class WorkCycleReport:
    prompt: str
    trigger: TriggerDecision
    onboarding_seed: OnboardingSeed | None
    action: str
    receipt_plan: str
    matched_memory_id: str = ""
    matched_memory_title: str = ""
    matched_score: float = 0.0
    after_work_decision: WorkCycleCandidateDecision = field(
        default_factory=lambda: WorkCycleCandidateDecision(status="not-evaluated", reason="no after-work learning supplied")
    )
    analytics_summary: str = "not available"
    next_action: str = ""

    def render(self) -> str:
        lines = [
            "CMU Full Work Cycle",
            "Mode: read-only integration proof; no memories or receipts are mutated.",
            f"Task: {self.prompt}",
            "",
            "Step 1 - Trigger:",
            self.trigger.render(),
            "",
            "Step 2 - Onboarding:",
        ]
        if self.onboarding_seed is None:
            lines.append("Skipped: trigger selected silent-skip.")
        else:
            lines.append(self.onboarding_seed.render())
        lines.extend(
            [
                "",
                "Step 3 - Preflight:",
                f"Action: {self.action}",
            ]
        )
        if self.matched_memory_id:
            lines.append(f"Matched Memory: {self.matched_memory_id} {self.matched_memory_title} (score {self.matched_score:.3f})")
        lines.extend(
            [
                "",
                "Step 4 - Receipt:",
                self.receipt_plan,
                "",
                "Step 5 - After-Work Memory Decision:",
                self.after_work_decision.render(),
                "",
                "Step 6 - Review Signal:",
                self.analytics_summary,
                "",
                f"Next: {self.next_action}",
                "",
                "Proof Meaning: this report connects the CMU task loop from trigger to onboarding, preflight, receipt planning, after-work candidate memory decision, and review/analytics feedback.",
            ]
        )
        return "\n".join(lines)


def work_cycle_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    request: WorkCycleRequest,
    *,
    semantic_index: SemanticIndex | None = None,
) -> WorkCycleReport:
    trigger = decide_trigger(
        request.query,
        repeated_error=request.repeated_error,
        uncertainty=request.uncertainty,
        shared_contract=request.shared_contract,
        irreversible=request.irreversible,
        unfamiliar=request.unfamiliar,
    )
    onboarding_seed = None
    matches = []
    if trigger.level != "silent-skip":
        onboarding_seed = build_onboarding_seed(memories, request.query, semantic_index=semantic_index)
        raw_matches = rank_memories(memories, request.query, semantic_index=semantic_index)
        actionable = [match for match in raw_matches if match.score >= action_threshold(request.query.risk)]
        matches = apply_usage_adjustments(actionable, receipts)
    action = "quiet"
    receipt_plan = "No receipt planned: no Action Note surfaced."
    matched_memory_id = ""
    matched_memory_title = ""
    matched_score = 0.0
    analytics_summary = "No matched memory analytics available."
    if matches:
        note = build_action_note(matches[0])
        action = "action-note"
        matched_memory_id = matches[0].memory.id
        matched_memory_title = note.recognized_situation
        matched_score = matches[0].score
        receipt_plan = f"Would create Memory Use Receipt for {matched_memory_id} from work-cycle."
        analytics_summary = matched_memory_analytics(matches[0].memory, memories, receipts)
    after_work_decision = after_work_candidate_decision(memories, request)
    return WorkCycleReport(
        prompt=request.prompt,
        trigger=trigger,
        onboarding_seed=onboarding_seed,
        action=action,
        matched_memory_id=matched_memory_id,
        matched_memory_title=matched_memory_title,
        matched_score=matched_score,
        receipt_plan=receipt_plan,
        after_work_decision=after_work_decision,
        analytics_summary=analytics_summary,
        next_action=next_work_cycle_action(trigger.level, action, after_work_decision.status, matched_memory_id),
    )


def after_work_candidate_decision(memories: list[Memory], request: WorkCycleRequest) -> WorkCycleCandidateDecision:
    if not has_after_work_learning(request):
        return WorkCycleCandidateDecision(status="not-evaluated", reason="no after-work learning supplied")
    decision = remember_candidate(
        memories,
        RememberRequest(
            situation=request.prompt,
            signals=request.learning_signals,
            outcome=request.outcome,
            worked=request.worked,
            failed=request.failed,
            future_use=request.future_use,
            evidence=request.evidence,
            liability_score=liability_from_query(request.query),
            suggested_next_type=MemoryType.SITUATION,
            scope=MemoryScope(
                code=[*request.query.files, *([request.query.area] if request.query.area else [])],
                workflow=request.query.workflow,
                environment=request.query.environment,
                actor=[request.query.actor] if request.query.actor else [],
            ),
            confidence=confidence_from_after_work(request),
        ),
    )
    return candidate_decision_from_remember(decision)


def matched_memory_analytics(memory: Memory, memories: list[Memory], receipts: list[MemoryUseReceipt]) -> str:
    challenges = active_challenges_by_stable(memories)
    card = analytics_card(
        memory,
        [receipt for receipt in receipts if receipt.memory_id == memory.id],
        challenges,
        memory.id,
    )
    return (
        f"{card.verdict}; {card.linked_uses}/{card.total_uses} linked, "
        f"{card.strong_committed} strong, {card.drag_signals} drag, "
        f"{card.unlinked_uses} unresolved; governance {card.governance_state}; "
        f"next {card.next_action}"
    )


def candidate_decision_from_remember(decision: RememberDecision) -> WorkCycleCandidateDecision:
    if decision.saved and decision.memory is not None:
        return WorkCycleCandidateDecision(
            status="candidate-ready",
            reason=decision.reason,
            candidate_id=decision.memory.id,
            suggested_next_type=decision.suggested_next_type.value,
        )
    return WorkCycleCandidateDecision(
        status="not-saved",
        reason=decision.reason,
        suggested_next_type=decision.suggested_next_type.value,
    )


def next_work_cycle_action(trigger_level: str, action: str, after_work_status: str, matched_memory_id: str) -> str:
    if trigger_level == "silent-skip" and after_work_status == "not-evaluated":
        return "proceed without CMU memory; no receipt or memory draft is indicated"
    if action == "action-note" and after_work_status == "candidate-ready":
        return "do the work, link the resulting receipt/checkpoint, then save/review the Candidate Memory if the lesson still holds"
    if action == "action-note":
        return f"do the work, then link or resolve the planned receipt for {matched_memory_id}"
    if after_work_status == "candidate-ready":
        return "save/review the Candidate Memory even though no prior memory guided the task"
    return "continue work and capture a trace only if reusable learning appears"


def has_after_work_learning(request: WorkCycleRequest) -> bool:
    return bool(
        request.learning_signals
        or request.outcome.strip()
        or request.worked.strip()
        or request.failed.strip()
        or request.future_use.strip()
        or request.evidence
    )


def liability_from_query(query: PreflightQuery) -> int:
    score = {"low": 1, "medium": 3, "high": 4}.get(query.risk, 3)
    if query.area.lower() in {"auth", "billing", "security", "privacy", "deployment"}:
        score += 1
    return max(1, min(score, 5))


def confidence_from_after_work(request: WorkCycleRequest) -> float:
    confidence = 0.45
    if request.future_use:
        confidence += 0.15
    if request.worked or request.failed:
        confidence += 0.15
    if request.evidence or request.outcome:
        confidence += 0.15
    if request.learning_signals:
        confidence += 0.1
    return min(confidence, 0.9)
