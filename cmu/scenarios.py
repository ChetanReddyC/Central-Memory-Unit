from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .json_store import read_json, update_json
from .models import Memory, utc_now
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
class ScenarioDefinition:
    id: str
    name: str
    prompt: str
    actor: str = "developer"
    area: str = ""
    files: list[str] = field(default_factory=list)
    workflow: list[str] = field(default_factory=list)
    environment: list[str] = field(default_factory=list)
    risk: str = "medium"
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
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @classmethod
    def create(
        cls,
        *,
        name: str,
        prompt: str,
        actor: str = "developer",
        area: str = "",
        files: list[str] | None = None,
        workflow: list[str] | None = None,
        environment: list[str] | None = None,
        risk: str = "medium",
        repeated_error: bool = False,
        uncertainty: bool = False,
        shared_contract: bool = False,
        irreversible: bool = False,
        unfamiliar: bool = False,
        expect_trigger: str = "",
        expect_action: str = "",
        expect_memory: str = "",
        expect_candidate: str = "",
        learning_signals: list[str] | None = None,
        worked: str = "",
        failed: str = "",
        future_use: str = "",
        evidence: list[str] | None = None,
        tags: list[str] | None = None,
        description: str = "",
    ) -> "ScenarioDefinition":
        return cls(
            id=f"scn_{uuid4().hex[:12]}",
            name=name.strip(),
            prompt=prompt.strip(),
            actor=actor.strip() or "developer",
            area=area.strip(),
            files=[item.strip() for item in files or [] if item.strip()],
            workflow=[item.strip() for item in workflow or [] if item.strip()],
            environment=[item.strip() for item in environment or [] if item.strip()],
            risk=risk.strip() or "medium",
            repeated_error=repeated_error,
            uncertainty=uncertainty,
            shared_contract=shared_contract,
            irreversible=irreversible,
            unfamiliar=unfamiliar,
            expect_trigger=expect_trigger.strip(),
            expect_action=expect_action.strip(),
            expect_memory=expect_memory.strip(),
            expect_candidate=expect_candidate.strip(),
            learning_signals=[item.strip() for item in learning_signals or [] if item.strip()],
            worked=worked.strip(),
            failed=failed.strip(),
            future_use=future_use.strip(),
            evidence=[item.strip() for item in evidence or [] if item.strip()],
            tags=[item.strip() for item in tags or [] if item.strip()],
            description=description.strip(),
        )

    def query(self) -> PreflightQuery:
        return PreflightQuery(
            prompt=self.prompt,
            actor=self.actor,
            area=self.area,
            files=self.files,
            workflow=self.workflow,
            environment=self.environment,
            risk=self.risk,
        )

    def request(self) -> ScenarioEvaluationRequest:
        return ScenarioEvaluationRequest(
            prompt=self.prompt,
            query=self.query(),
            repeated_error=self.repeated_error,
            uncertainty=self.uncertainty,
            shared_contract=self.shared_contract,
            irreversible=self.irreversible,
            unfamiliar=self.unfamiliar,
            expect_trigger=self.expect_trigger,
            expect_action=self.expect_action,
            expect_memory=self.expect_memory,
            expect_candidate=self.expect_candidate,
            learning_signals=list(self.learning_signals),
            worked=self.worked,
            failed=self.failed,
            future_use=self.future_use,
            evidence=list(self.evidence),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "actor": self.actor,
            "area": self.area,
            "files": self.files,
            "workflow": self.workflow,
            "environment": self.environment,
            "risk": self.risk,
            "repeated_error": self.repeated_error,
            "uncertainty": self.uncertainty,
            "shared_contract": self.shared_contract,
            "irreversible": self.irreversible,
            "unfamiliar": self.unfamiliar,
            "expect_trigger": self.expect_trigger,
            "expect_action": self.expect_action,
            "expect_memory": self.expect_memory,
            "expect_candidate": self.expect_candidate,
            "learning_signals": self.learning_signals,
            "worked": self.worked,
            "failed": self.failed,
            "future_use": self.future_use,
            "evidence": self.evidence,
            "tags": self.tags,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioDefinition":
        return cls(
            id=data["id"],
            name=data["name"],
            prompt=data["prompt"],
            actor=data.get("actor", "developer"),
            area=data.get("area", ""),
            files=list(data.get("files", [])),
            workflow=list(data.get("workflow", [])),
            environment=list(data.get("environment", [])),
            risk=data.get("risk", "medium"),
            repeated_error=bool(data.get("repeated_error", False)),
            uncertainty=bool(data.get("uncertainty", False)),
            shared_contract=bool(data.get("shared_contract", False)),
            irreversible=bool(data.get("irreversible", False)),
            unfamiliar=bool(data.get("unfamiliar", False)),
            expect_trigger=data.get("expect_trigger", ""),
            expect_action=data.get("expect_action", ""),
            expect_memory=data.get("expect_memory", ""),
            expect_candidate=data.get("expect_candidate", ""),
            learning_signals=list(data.get("learning_signals", [])),
            worked=data.get("worked", ""),
            failed=data.get("failed", ""),
            future_use=data.get("future_use", ""),
            evidence=list(data.get("evidence", [])),
            tags=list(data.get("tags", [])),
            description=data.get("description", ""),
        )

    def render_summary(self) -> str:
        tags = f" tags={','.join(self.tags)}" if self.tags else ""
        expectations = []
        if self.expect_trigger:
            expectations.append(f"trigger={self.expect_trigger}")
        if self.expect_action:
            expectations.append(f"action={self.expect_action}")
        if self.expect_memory:
            expectations.append(f"memory={self.expect_memory}")
        if self.expect_candidate:
            expectations.append(f"candidate={self.expect_candidate}")
        expected = f" expects {' '.join(expectations)}" if expectations else ""
        return f"{self.id} - {self.name} [{self.risk}]{tags}{expected}"


class ScenarioLibraryStore:
    def __init__(self, root) -> None:
        from pathlib import Path

        self.root = Path(root)
        self.store_file = self.root / ".cmu" / "scenarios.json"

    def add(self, scenario: ScenarioDefinition) -> ScenarioDefinition:
        return update_json(
            self.store_file,
            {"version": 1, "scenarios": []},
            lambda data: append_scenario(data, scenario),
        )

    def list(self, *, tag: str = "") -> list[ScenarioDefinition]:
        scenarios = [ScenarioDefinition.from_dict(item) for item in self._read()["scenarios"]]
        if tag:
            scenarios = [scenario for scenario in scenarios if tag in scenario.tags]
        return sorted(scenarios, key=lambda item: item.name.lower())

    def get(self, scenario_id: str) -> ScenarioDefinition:
        for scenario in self.list():
            if scenario.id == scenario_id or scenario.name == scenario_id:
                return scenario
        raise KeyError(f"Scenario not found: {scenario_id}")

    def _read(self) -> dict:
        return read_json(self.store_file, {"version": 1, "scenarios": []})


def append_scenario(data: dict, scenario: ScenarioDefinition) -> ScenarioDefinition:
    data["scenarios"].append(scenario.to_dict())
    return scenario


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


@dataclass
class ScenarioLibraryRunItem:
    scenario: ScenarioDefinition
    report: ScenarioEvaluationReport

    @property
    def passed(self) -> bool:
        return bool(self.report.checks) and all(check.passed for check in self.report.checks)

    def render(self) -> str:
        status = "pass" if self.passed else "review"
        failed = [check.name for check in self.report.checks if not check.passed]
        suffix = f" failed={','.join(failed)}" if failed else ""
        memory = f" memory={self.report.matched_memory_id or 'none'}"
        return f"- {status}: {self.scenario.id} {self.scenario.name} verdict={self.report.verdict}{memory}{suffix}"


@dataclass
class ScenarioLibraryRunReport:
    items: list[ScenarioLibraryRunItem]
    tag: str = ""

    def render(self) -> str:
        passed = sum(1 for item in self.items if item.passed)
        review = len(self.items) - passed
        lines = [
            "CMU Scenario Library Run",
            "Mode: read-only scenario regression; no memories or receipts are mutated.",
            f"Filter: tag={self.tag}" if self.tag else "Filter: all scenarios",
            f"Summary: total={len(self.items)} pass={passed} review={review}",
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios matched."])
        return "\n".join(lines)

    def has_review_items(self) -> bool:
        return any(not item.passed for item in self.items)


@dataclass
class ScenarioComparisonItem:
    scenario: ScenarioDefinition
    baseline: ScenarioEvaluationReport
    current: ScenarioEvaluationReport

    @property
    def baseline_passed(self) -> bool:
        return bool(self.baseline.checks) and all(check.passed for check in self.baseline.checks)

    @property
    def current_passed(self) -> bool:
        return bool(self.current.checks) and all(check.passed for check in self.current.checks)

    @property
    def classification(self) -> str:
        if self.baseline_passed and not self.current_passed:
            return "regressed"
        if not self.baseline_passed and self.current_passed:
            return "improved"
        if self.baseline_passed and self.current_passed:
            if self.baseline.action != self.current.action or self.baseline.matched_memory_id != self.current.matched_memory_id:
                return "changed-pass"
            return "unchanged-pass"
        if self.baseline.action != self.current.action or self.baseline.matched_memory_id != self.current.matched_memory_id:
            return "changed-review"
        return "unchanged-review"

    def render(self) -> str:
        return (
            f"- {self.classification}: {self.scenario.id} {self.scenario.name} "
            f"baseline={self.baseline.verdict}/{self.baseline.action}/{self.baseline.matched_memory_id or 'none'} "
            f"current={self.current.verdict}/{self.current.action}/{self.current.matched_memory_id or 'none'}"
        )


@dataclass
class ScenarioComparisonReport:
    items: list[ScenarioComparisonItem]
    baseline_root: str
    current_root: str
    tag: str = ""

    def render(self) -> str:
        counts = {name: 0 for name in ["regressed", "improved", "changed-pass", "changed-review", "unchanged-pass", "unchanged-review"]}
        for item in self.items:
            counts[item.classification] += 1
        lines = [
            "CMU Scenario Comparison",
            "Mode: read-only before/after scenario proof; no memories or receipts are mutated.",
            f"Baseline Root: {self.baseline_root}",
            f"Current Root: {self.current_root}",
            f"Filter: tag={self.tag}" if self.tag else "Filter: all scenarios",
            (
                "Summary: "
                f"total={len(self.items)} "
                f"regressed={counts['regressed']} "
                f"improved={counts['improved']} "
                f"changed={counts['changed-pass'] + counts['changed-review']} "
                f"unchanged={counts['unchanged-pass'] + counts['unchanged-review']}"
            ),
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios matched."])
        lines.extend(
            [
                "",
                "Proof Meaning: this comparison runs the same saved scenarios against two real CMU stores so "
                "retrieval, trigger, Candidate, and expectation behavior can be checked before trusting a memory-base or runtime change.",
            ]
        )
        return "\n".join(lines)

    def has_regressions(self) -> bool:
        return any(item.classification == "regressed" for item in self.items)


@dataclass
class ScenarioNoMemoryComparisonItem:
    scenario: ScenarioDefinition
    no_memory: ScenarioEvaluationReport
    with_cmu: ScenarioEvaluationReport

    @property
    def classification(self) -> str:
        if self.no_memory.action == "quiet" and self.with_cmu.action == "action-note":
            return "cmu-added-guidance"
        if self.no_memory.action == self.with_cmu.action and self.no_memory.matched_memory_id == self.with_cmu.matched_memory_id:
            return "no-difference"
        if self.no_memory.action == "action-note" and self.with_cmu.action == "quiet":
            return "cmu-suppressed-guidance"
        return "changed"

    def render(self) -> str:
        return (
            f"- {self.classification}: {self.scenario.id} {self.scenario.name} "
            f"without={self.no_memory.verdict}/{self.no_memory.action}/{self.no_memory.matched_memory_id or 'none'} "
            f"with={self.with_cmu.verdict}/{self.with_cmu.action}/{self.with_cmu.matched_memory_id or 'none'}"
        )


@dataclass
class ScenarioNoMemoryComparisonReport:
    items: list[ScenarioNoMemoryComparisonItem]
    root: str
    tag: str = ""

    def render(self) -> str:
        counts = {name: 0 for name in ["cmu-added-guidance", "cmu-suppressed-guidance", "changed", "no-difference"]}
        for item in self.items:
            counts[item.classification] += 1
        lines = [
            "CMU Scenario No-Memory Comparison",
            "Mode: read-only agent-behavior comparison; no-memory baseline vs current CMU store.",
            f"Root: {self.root}",
            f"Filter: tag={self.tag}" if self.tag else "Filter: all scenarios",
            (
                "Summary: "
                f"total={len(self.items)} "
                f"cmu_added_guidance={counts['cmu-added-guidance']} "
                f"cmu_suppressed_guidance={counts['cmu-suppressed-guidance']} "
                f"changed={counts['changed']} "
                f"no_difference={counts['no-difference']}"
            ),
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios matched."])
        lines.extend(
            [
                "",
                "Proof Meaning: saved scenarios can now compare agent behavior with the current CMU store against an explicit no-memory baseline.",
            ]
        )
        return "\n".join(lines)

    def has_no_difference(self) -> bool:
        return any(item.classification == "no-difference" for item in self.items)


SCENARIO_SUITE_VERSION = "cmu-scenario-suite/v1"


@dataclass(frozen=True)
class ScenarioSuiteRecord:
    id: str
    created_at: str
    tag: str
    total: int
    passed: int
    review: int
    cmu_added_guidance: int
    no_difference: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "tag": self.tag,
            "total": self.total,
            "passed": self.passed,
            "review": self.review,
            "cmu_added_guidance": self.cmu_added_guidance,
            "no_difference": self.no_difference,
        }


@dataclass(frozen=True)
class ScenarioSuiteReport:
    root: str
    record: ScenarioSuiteRecord
    run: ScenarioLibraryRunReport
    no_memory: ScenarioNoMemoryComparisonReport
    recorded: bool
    history_count: int

    @property
    def ok(self) -> bool:
        return self.record.review == 0 and self.record.no_difference == 0

    def render(self) -> str:
        lines = [
            "CMU Longitudinal Scenario Suite",
            f"Version: {SCENARIO_SUITE_VERSION}",
            "Mode: read-only scenario run plus no-memory comparison, with optional run-history recording.",
            f"Root: {self.root}",
            f"Suite Run: {self.record.id}",
            f"Filter: tag={self.record.tag}" if self.record.tag else "Filter: all scenarios",
            f"Recorded: {'yes' if self.recorded else 'no'}",
            f"History Runs: {self.history_count}",
            (
                "Summary: "
                f"total={self.record.total} pass={self.record.passed} review={self.record.review} "
                f"cmu_added_guidance={self.record.cmu_added_guidance} no_difference={self.record.no_difference}"
            ),
            "",
            "Scenario Run:",
            self.run.render(),
            "",
            "No-Memory Comparison:",
            self.no_memory.render(),
            "",
            f"Trend Judgment: {scenario_suite_judgment(self.record)}",
            "Proof Meaning: scenario evaluation can now be run as a longitudinal suite with persisted history and usefulness/drag-oriented no-memory comparison metrics.",
        ]
        return "\n".join(lines)


def run_longitudinal_scenario_suite(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    tag: str = "",
    semantic_index: SemanticIndex | None = None,
    record: bool = False,
) -> ScenarioSuiteReport:
    root_path = Path(root)
    scenarios = ScenarioLibraryStore(root_path).list(tag=tag or None)
    run_report = run_scenario_library(scenarios, memories, receipts, tag=tag, semantic_index=semantic_index)
    no_memory = compare_scenario_library_to_no_memory(
        scenarios,
        current_memories=memories,
        current_receipts=receipts,
        root=str(root_path),
        tag=tag,
        current_semantic_index=semantic_index,
    )
    passed = sum(1 for item in run_report.items if item.passed)
    cmu_added = sum(1 for item in no_memory.items if item.classification == "cmu-added-guidance")
    no_difference = sum(1 for item in no_memory.items if item.classification == "no-difference")
    record_item = ScenarioSuiteRecord(
        id=f"ssr_{uuid4().hex[:12]}",
        created_at=utc_now(),
        tag=tag,
        total=len(run_report.items),
        passed=passed,
        review=len(run_report.items) - passed,
        cmu_added_guidance=cmu_added,
        no_difference=no_difference,
    )
    history_count = existing_scenario_history_count(root_path)
    if record:
        update_json(
            root_path / ".cmu" / "scenario_suite_runs.json",
            {"version": 1, "runs": []},
            lambda data: append_scenario_suite_run(data, record_item),
        )
        history_count += 1
    return ScenarioSuiteReport(
        root=str(root_path),
        record=record_item,
        run=run_report,
        no_memory=no_memory,
        recorded=record,
        history_count=history_count,
    )


def append_scenario_suite_run(data: dict, record: ScenarioSuiteRecord) -> ScenarioSuiteRecord:
    data["runs"].append(record.to_dict())
    return record


def existing_scenario_history_count(root: Path) -> int:
    data = read_json(root / ".cmu" / "scenario_suite_runs.json", {"version": 1, "runs": []})
    return len(data.get("runs", []))


def scenario_suite_judgment(record: ScenarioSuiteRecord) -> str:
    if record.total == 0:
        return "no scenarios matched; add saved cases before judging memory behavior"
    if record.review:
        return "some scenarios need review; inspect failures before broadening retrieval or trust"
    if record.no_difference:
        return "CMU did not change behavior for every passing case; strengthen expectations or memory coverage"
    if record.cmu_added_guidance:
        return "CMU added guidance across the passing suite; keep recording runs to catch regressions over time"
    return "suite passed but usefulness signal is neutral; add cases that compare CMU against no-memory behavior"


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


def run_scenario_library(
    scenarios: list[ScenarioDefinition],
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    semantic_index: SemanticIndex | None = None,
    tag: str = "",
) -> ScenarioLibraryRunReport:
    items = [
        ScenarioLibraryRunItem(
            scenario=scenario,
            report=evaluate_scenario(memories, receipts, scenario.request(), semantic_index=semantic_index),
        )
        for scenario in scenarios
    ]
    return ScenarioLibraryRunReport(items=items, tag=tag)


def compare_scenario_library(
    scenarios: list[ScenarioDefinition],
    *,
    baseline_memories: list[Memory],
    baseline_receipts: list[MemoryUseReceipt],
    current_memories: list[Memory],
    current_receipts: list[MemoryUseReceipt],
    baseline_root: str,
    current_root: str,
    baseline_semantic_index: SemanticIndex | None = None,
    current_semantic_index: SemanticIndex | None = None,
    tag: str = "",
) -> ScenarioComparisonReport:
    items = [
        ScenarioComparisonItem(
            scenario=scenario,
            baseline=evaluate_scenario(
                baseline_memories,
                baseline_receipts,
                scenario.request(),
                semantic_index=baseline_semantic_index,
            ),
            current=evaluate_scenario(
                current_memories,
                current_receipts,
                scenario.request(),
                semantic_index=current_semantic_index,
            ),
        )
        for scenario in scenarios
    ]
    return ScenarioComparisonReport(items=items, baseline_root=baseline_root, current_root=current_root, tag=tag)


def compare_scenario_library_to_no_memory(
    scenarios: list[ScenarioDefinition],
    *,
    current_memories: list[Memory],
    current_receipts: list[MemoryUseReceipt],
    root: str,
    current_semantic_index: SemanticIndex | None = None,
    tag: str = "",
) -> ScenarioNoMemoryComparisonReport:
    items = [
        ScenarioNoMemoryComparisonItem(
            scenario=scenario,
            no_memory=evaluate_scenario([], [], scenario.request(), semantic_index=None),
            with_cmu=evaluate_scenario(
                current_memories,
                current_receipts,
                scenario.request(),
                semantic_index=current_semantic_index,
            ),
        )
        for scenario in scenarios
    ]
    return ScenarioNoMemoryComparisonReport(items=items, root=root, tag=tag)


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
