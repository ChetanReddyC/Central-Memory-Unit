from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory
from .onboarding import OnboardingSeed, build_onboarding_seed
from .retrieval import PreflightQuery, SemanticIndex, action_threshold, build_action_note, rank_memories
from .triggers import TriggerDecision, decide_trigger
from .usage import MemoryUseReceipt, apply_usage_adjustments


@dataclass
class ScenarioEvaluationRequest:
    prompt: str
    query: PreflightQuery
    repeated_error: bool = False
    uncertainty: bool = False
    shared_contract: bool = False
    irreversible: bool = False
    unfamiliar: bool = False
    expect_trigger: str = ""
    expect_action: str = ""
    expect_memory: str = ""
    expect_candidate: str = ""
    learning_signals: list[str] = field(default_factory=list)
    worked: str = ""
    failed: str = ""
    future_use: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class ScenarioCheck:
    name: str
    passed: bool
    expected: str
    actual: str

    def render(self) -> str:
        status = "pass" if self.passed else "fail"
        return f"- {self.name}: {status} (expected {self.expected}; actual {self.actual})"


@dataclass
class ScenarioCandidateSignal:
    status: str
    reason: str

    def render(self) -> str:
        return f"Candidate Memory: {self.status} - {self.reason}"


@dataclass
class ScenarioEvaluationReport:
    prompt: str
    trigger: TriggerDecision
    onboarding_seed: OnboardingSeed | None
    action: str
    matched_memory_id: str = ""
    matched_memory_title: str = ""
    matched_score: float = 0.0
    receipt_signal: str = ""
    candidate_signal: ScenarioCandidateSignal = field(
        default_factory=lambda: ScenarioCandidateSignal(status="not-evaluated", reason="no candidate expectation supplied")
    )
    checks: list[ScenarioCheck] = field(default_factory=list)
    verdict: str = "inconclusive"
    proof_points: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Scenario Evaluation",
            "Mode: read-only structural proof; no memories or receipts are mutated.",
            f"Scenario: {self.prompt}",
            "",
            self.trigger.render(),
        ]
        if self.onboarding_seed is None:
            lines.extend(["", "Onboarding Seed: skipped by silent-skip trigger"])
        else:
            lines.extend(["", self.onboarding_seed.render()])
        lines.extend(["", "Preflight Result:", f"Action: {self.action}"])
        if self.matched_memory_id:
            lines.append(f"Matched Memory: {self.matched_memory_id} {self.matched_memory_title} (score {self.matched_score:.3f})")
        lines.append(f"Receipt Signal: {self.receipt_signal}")
        lines.extend(["", self.candidate_signal.render()])
        lines.extend(["", "Expectation Checks:"])
        if self.checks:
            lines.extend(check.render() for check in self.checks)
        else:
            lines.append("- none supplied")
        lines.extend(["", f"Verdict: {self.verdict}"])
        if self.proof_points:
            lines.append("Proof Points:")
            lines.extend(f"- {point}" for point in self.proof_points)
        return "\n".join(lines)


def evaluate_scenario(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    request: ScenarioEvaluationRequest,
    *,
    semantic_index: SemanticIndex | None = None,
) -> ScenarioEvaluationReport:
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
    matched_memory_id = ""
    matched_memory_title = ""
    matched_score = 0.0
    if matches:
        note = build_action_note(matches[0])
        action = "action-note"
        matched_memory_id = matches[0].memory.id
        matched_memory_title = note.recognized_situation
        matched_score = matches[0].score
    candidate_signal = evaluate_candidate_signal(request)
    receipt_signal = "would-create-use-receipt" if action == "action-note" else "none"
    checks = scenario_checks(
        request,
        trigger_level=trigger.level,
        action=action,
        matched_memory_id=matched_memory_id,
        candidate_status=candidate_signal.status,
    )
    verdict, proof_points = scenario_verdict(
        trigger_level=trigger.level,
        action=action,
        checks=checks,
        matched_memory_id=matched_memory_id,
        candidate_signal=candidate_signal,
    )
    return ScenarioEvaluationReport(
        prompt=request.prompt,
        trigger=trigger,
        onboarding_seed=onboarding_seed,
        action=action,
        matched_memory_id=matched_memory_id,
        matched_memory_title=matched_memory_title,
        matched_score=matched_score,
        receipt_signal=receipt_signal,
        candidate_signal=candidate_signal,
        checks=checks,
        verdict=verdict,
        proof_points=proof_points,
    )


def evaluate_candidate_signal(request: ScenarioEvaluationRequest) -> ScenarioCandidateSignal:
    has_learning = bool(
        request.future_use.strip()
        and (request.worked.strip() or request.failed.strip())
        and (request.evidence or request.learning_signals)
    )
    if has_learning:
        return ScenarioCandidateSignal(
            status="draft-recommended",
            reason="scenario includes future-use, worked/failed lesson, and evidence or signals",
        )
    return ScenarioCandidateSignal(
        status="not-recommended",
        reason="scenario does not contain enough reusable learning to draft memory",
    )


def scenario_checks(
    request: ScenarioEvaluationRequest,
    *,
    trigger_level: str,
    action: str,
    matched_memory_id: str,
    candidate_status: str,
) -> list[ScenarioCheck]:
    checks: list[ScenarioCheck] = []
    if request.expect_trigger:
        checks.append(
            ScenarioCheck(
                name="trigger",
                passed=request.expect_trigger == trigger_level,
                expected=request.expect_trigger,
                actual=trigger_level,
            )
        )
    if request.expect_action:
        checks.append(
            ScenarioCheck(
                name="action",
                passed=request.expect_action == action,
                expected=request.expect_action,
                actual=action,
            )
        )
    if request.expect_memory:
        expected = "none" if request.expect_memory == "none" else request.expect_memory
        actual = matched_memory_id or "none"
        checks.append(
            ScenarioCheck(
                name="memory",
                passed=expected == actual,
                expected=expected,
                actual=actual,
            )
        )
    if request.expect_candidate:
        checks.append(
            ScenarioCheck(
                name="candidate",
                passed=request.expect_candidate == candidate_status,
                expected=request.expect_candidate,
                actual=candidate_status,
            )
        )
    return checks


def scenario_verdict(
    *,
    trigger_level: str,
    action: str,
    checks: list[ScenarioCheck],
    matched_memory_id: str,
    candidate_signal: ScenarioCandidateSignal,
) -> tuple[str, list[str]]:
    if checks and all(check.passed for check in checks):
        proof_points = ["all supplied expectations passed"]
        if action == "action-note":
            proof_points.append(f"CMU surfaced memory {matched_memory_id} and would create a use receipt")
        elif trigger_level == "silent-skip":
            proof_points.append("CMU stayed quiet through the trigger layer")
        if candidate_signal.status == "draft-recommended":
            proof_points.append("scenario contains enough reusable learning for candidate drafting")
        return "supports-cmu-assumption", proof_points
    if any(not check.passed for check in checks):
        failed = ", ".join(check.name for check in checks if not check.passed)
        return "cmu-gap-found", [f"failed expectation checks: {failed}"]
    if action == "action-note":
        return "needs-human-judgment", ["CMU surfaced memory, but no expectations were supplied to judge fit"]
    if trigger_level == "silent-skip":
        return "quiet-baseline", ["CMU skipped the work cycle because no trigger required memory"]
    return "inconclusive", ["scenario ran, but supplied evidence was not enough to judge the assumption"]
