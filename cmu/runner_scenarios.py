from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from .models import MemoryStatus
from .runner_hooks import AutonomousRunnerHooks, RunnerHookResult
from .scenarios import ScenarioDefinition, ScenarioLibraryStore
from .store import MemoryStore
from .usage import MemoryUseReceipt, MemoryUseStore
from .json_store import read_json, update_json
from .models import utc_now


RUNNER_SCENARIO_VERSION = "cmu-runner-scenario/v1"
RUNNER_SCENARIO_SUITE_VERSION = "cmu-runner-scenario-suite/v1"


@dataclass(frozen=True)
class RunnerScenarioRequest:
    prompt: str
    actor: str = "agent"
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
    semantic: str = "off"
    run_after_task: bool = False
    reusable_learning: bool = False
    title: str = ""
    situation: str = ""
    signals: list[str] = field(default_factory=list)
    outcome: str = ""
    worked: str = ""
    failed: str = ""
    future_use: str = ""
    evidence: list[str] = field(default_factory=list)
    liability_score: int = 1
    suggested_next_type: str = "situation"
    confidence: float = 0.6
    scope: dict[str, list[str]] = field(default_factory=dict)
    checkpoint_hash: str = ""
    checkpoint_message: str = ""
    checkpoint_files: list[str] = field(default_factory=list)
    checkpoint_note: str = ""
    expect_start: str = ""
    expect_memory: str = ""
    expect_candidate: str = ""
    expect_checkpoint: str = ""


@dataclass(frozen=True)
class RunnerScenarioCheck:
    name: str
    passed: bool
    expected: str
    actual: str

    def render(self) -> str:
        return f"- {self.name}: {'pass' if self.passed else 'fail'} (expected {self.expected}; actual {self.actual})"


@dataclass(frozen=True)
class RunnerScenarioReport:
    source_root: str
    start: RunnerHookResult
    after_task: RunnerHookResult | None = None
    checkpoint: RunnerHookResult | None = None
    review: RunnerHookResult | None = None
    checks: list[RunnerScenarioCheck] = field(default_factory=list)
    source_memory_count: int = 0
    source_receipt_count: int = 0
    isolated_memory_count: int = 0
    isolated_receipt_count: int = 0

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def render(self) -> str:
        lines = [
            "CMU Runner Scenario",
            f"Version: {RUNNER_SCENARIO_VERSION}",
            "Mode: read-only source-store proof; runner hooks execute against an isolated temporary CMU store.",
            f"Source Root: {self.source_root}",
            "",
            "Source Snapshot:",
            f"- Memories: {self.source_memory_count}",
            f"- Receipts: {self.source_receipt_count}",
            "",
            "Runner Hook Results:",
            f"- before_task: {self.start.status}",
        ]
        matched = self.start.response.get("matched_memory", {})
        if matched:
            lines.append(f"  Matched Memory: {matched.get('id')} {matched.get('title')}")
        receipt = self.start.response.get("receipt")
        if isinstance(receipt, dict) and receipt.get("id"):
            lines.append(f"  Receipt: {receipt['id']}")
        if self.after_task is not None:
            lines.append(f"- after_task: {self.after_task.status}")
        if self.checkpoint is not None:
            lines.append(f"- after_checkpoint: {self.checkpoint.status}")
        if self.review is not None:
            lines.append(f"- review: {self.review.status}")
        lines.extend(
            [
                "",
                "Isolated Result:",
                f"- Memories: {self.isolated_memory_count}",
                f"- Receipts: {self.isolated_receipt_count}",
                "",
                "Expectation Checks:",
            ]
        )
        if self.checks:
            lines.extend(check.render() for check in self.checks)
        else:
            lines.append("- none supplied")
        lines.extend(
            [
                "",
                f"Verdict: {'pass' if self.passed else 'review'}",
                "Proof Meaning: this scenario executes the real autonomous-runner hooks and persistence gates "
                "without mutating the source CMU memory base. Use it to compare expected runner lifecycle behavior "
                "against actual trigger, retrieval, receipt, Candidate, checkpoint, and review outcomes.",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class RunnerScenarioSuiteRecord:
    id: str
    created_at: str
    tag: str
    total: int
    passed: int
    review: int
    action_notes: int
    candidates: int
    checkpoints: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "tag": self.tag,
            "total": self.total,
            "passed": self.passed,
            "review": self.review,
            "action_notes": self.action_notes,
            "candidates": self.candidates,
            "checkpoints": self.checkpoints,
        }


@dataclass(frozen=True)
class RunnerScenarioSuiteItem:
    scenario: ScenarioDefinition
    report: RunnerScenarioReport

    @property
    def passed(self) -> bool:
        return self.report.passed

    def render(self) -> str:
        status = "pass" if self.passed else "review"
        matched = matched_memory_id(self.report.start) or "none"
        return f"- {status}: {self.scenario.id} {self.scenario.name} start={self.report.start.status} memory={matched}"


@dataclass(frozen=True)
class RunnerScenarioSuiteReport:
    root: str
    record: RunnerScenarioSuiteRecord
    items: list[RunnerScenarioSuiteItem]
    recorded: bool
    history_count: int

    @property
    def ok(self) -> bool:
        return self.record.total > 0 and self.record.review == 0

    def render(self) -> str:
        lines = [
            "CMU Runner Scenario Suite",
            f"Version: {RUNNER_SCENARIO_SUITE_VERSION}",
            "Mode: saved scenario library evaluated through real autonomous-runner hooks in isolated stores.",
            f"Root: {self.root}",
            f"Suite Run: {self.record.id}",
            f"Filter: tag={self.record.tag}" if self.record.tag else "Filter: all scenarios",
            f"Recorded: {'yes' if self.recorded else 'no'}",
            f"History Runs: {self.history_count}",
            (
                "Summary: "
                f"total={self.record.total} pass={self.record.passed} review={self.record.review} "
                f"action_notes={self.record.action_notes} candidates={self.record.candidates} checkpoints={self.record.checkpoints}"
            ),
        ]
        if self.items:
            lines.extend(["", "Items:"])
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios matched."])
        lines.extend(
            [
                "",
                f"Verdict: {'pass' if self.ok else 'review'}",
                "Proof Meaning: saved scenario expectations now exercise the autonomous hook boundary and can be recorded over time.",
            ]
        )
        return "\n".join(lines)


def run_runner_scenario(
    root: Path | str,
    request: RunnerScenarioRequest,
    *,
    work_dir: Path | str | None = None,
) -> RunnerScenarioReport:
    source_root = Path(root)
    source_memories = MemoryStore(source_root).list(status=MemoryStatus.ACTIVE)
    source_receipts = read_source_receipts_without_init(source_root)
    parent = Path(work_dir) if work_dir is not None else source_root / ".manual" / "runner-scenarios"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="scenario-", dir=parent) as tmp:
        isolated_root = Path(tmp)
        seed_isolated_store(isolated_root, source_memories, source_receipts)
        hooks = AutonomousRunnerHooks(isolated_root)
        start = hooks.before_task(
            request.prompt,
            actor=request.actor,
            area=request.area,
            files=request.files,
            workflow=request.workflow,
            environment=request.environment,
            risk=request.risk,
            repeated_error=request.repeated_error,
            uncertainty=request.uncertainty,
            shared_contract=request.shared_contract,
            irreversible=request.irreversible,
            unfamiliar=request.unfamiliar,
            semantic=request.semantic,
        )
        after_task = None
        if request.run_after_task:
            after_task = hooks.after_task(
                reusable_learning=request.reusable_learning,
                title=request.title,
                situation=request.situation,
                signals=request.signals,
                outcome=request.outcome,
                worked=request.worked,
                failed=request.failed,
                future_use=request.future_use,
                evidence=request.evidence,
                liability_score=request.liability_score,
                suggested_next_type=request.suggested_next_type,
                confidence=request.confidence,
                scope=request.scope,
            )
        checkpoint = None
        receipt = start.response.get("receipt")
        if request.checkpoint_hash and isinstance(receipt, dict) and receipt.get("id"):
            checkpoint = hooks.after_checkpoint(
                receipt["id"],
                note=request.checkpoint_note,
                manual_commit={
                    "hash": request.checkpoint_hash,
                    "message": request.checkpoint_message,
                    "files": request.checkpoint_files,
                },
            )
        review_memory_id = matched_memory_id(start)
        review = hooks.review(review_memory_id) if review_memory_id else hooks.review()
        isolated_memories = MemoryStore(isolated_root).list(status=MemoryStatus.ACTIVE)
        isolated_receipts = MemoryUseStore(isolated_root).list()
        return RunnerScenarioReport(
            source_root=str(source_root),
            start=start,
            after_task=after_task,
            checkpoint=checkpoint,
            review=review,
            checks=runner_scenario_checks(request, start, after_task, checkpoint),
            source_memory_count=len(source_memories),
            source_receipt_count=len(source_receipts),
            isolated_memory_count=len(isolated_memories),
            isolated_receipt_count=len(isolated_receipts),
        )


def run_runner_scenario_suite(
    root: Path | str,
    *,
    tag: str = "",
    record: bool = False,
    work_dir: Path | str | None = None,
) -> RunnerScenarioSuiteReport:
    root_path = Path(root)
    scenarios = ScenarioLibraryStore(root_path).list(tag=tag)
    items = [
        RunnerScenarioSuiteItem(
            scenario=scenario,
            report=run_runner_scenario(root_path, request_from_scenario(scenario), work_dir=work_dir),
        )
        for scenario in scenarios
    ]
    passed = sum(1 for item in items if item.passed)
    action_notes = sum(1 for item in items if item.report.start.status == "action-note")
    candidates = sum(1 for item in items if item.report.after_task is not None and item.report.after_task.status == "candidate-saved")
    checkpoints = sum(1 for item in items if item.report.checkpoint is not None and item.report.checkpoint.status == "checkpoint-linked")
    record_item = RunnerScenarioSuiteRecord(
        id=f"rsr_{uuid4().hex[:12]}",
        created_at=utc_now(),
        tag=tag,
        total=len(items),
        passed=passed,
        review=len(items) - passed,
        action_notes=action_notes,
        candidates=candidates,
        checkpoints=checkpoints,
    )
    history_count = runner_scenario_history_count(root_path)
    if record:
        update_json(
            root_path / ".cmu" / "runner_scenario_runs.json",
            {"version": 1, "runs": []},
            lambda data: append_runner_scenario_run(data, record_item),
        )
        history_count += 1
    return RunnerScenarioSuiteReport(
        root=str(root_path),
        record=record_item,
        items=items,
        recorded=record,
        history_count=history_count,
    )


def request_from_scenario(scenario: ScenarioDefinition) -> RunnerScenarioRequest:
    return RunnerScenarioRequest(
        prompt=scenario.prompt,
        actor=scenario.actor,
        area=scenario.area,
        files=list(scenario.files),
        workflow=list(scenario.workflow),
        environment=list(scenario.environment),
        risk=scenario.risk,
        repeated_error=scenario.repeated_error,
        uncertainty=scenario.uncertainty,
        shared_contract=scenario.shared_contract,
        irreversible=scenario.irreversible,
        unfamiliar=scenario.unfamiliar,
        run_after_task=bool(scenario.expect_candidate),
        reusable_learning=scenario.expect_candidate in {"candidate-saved", "candidate-created"},
        title=scenario.name,
        situation=scenario.description or scenario.name,
        signals=list(scenario.learning_signals),
        worked=scenario.worked,
        failed=scenario.failed,
        future_use=scenario.future_use,
        evidence=list(scenario.evidence),
        expect_start="action-note" if scenario.expect_action == "action-note" else ("quiet" if scenario.expect_action == "quiet" else ""),
        expect_memory=scenario.expect_memory,
        expect_candidate=scenario.expect_candidate,
    )


def append_runner_scenario_run(data: dict, record: RunnerScenarioSuiteRecord) -> RunnerScenarioSuiteRecord:
    data["runs"].append(record.to_dict())
    return record


def runner_scenario_history_count(root: Path) -> int:
    data = read_json(root / ".cmu" / "runner_scenario_runs.json", {"version": 1, "runs": []})
    return len(data.get("runs", []))


def seed_isolated_store(isolated_root: Path, memories, receipts: list[MemoryUseReceipt]) -> None:
    memory_store = MemoryStore(isolated_root)
    use_store = MemoryUseStore(isolated_root)
    memory_store.init()
    use_store.init()
    for memory in memories:
        memory_store.add(memory)
    for receipt in receipts:
        use_store.add(receipt)


def read_source_receipts_without_init(root: Path) -> list[MemoryUseReceipt]:
    path = root / ".cmu" / "uses.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(
        [MemoryUseReceipt.from_dict(item) for item in data.get("uses", [])],
        key=lambda item: item.surfaced_at,
        reverse=True,
    )


def runner_scenario_checks(
    request: RunnerScenarioRequest,
    start: RunnerHookResult,
    after_task: RunnerHookResult | None,
    checkpoint: RunnerHookResult | None,
) -> list[RunnerScenarioCheck]:
    checks: list[RunnerScenarioCheck] = []
    if request.expect_start:
        checks.append(RunnerScenarioCheck("start", request.expect_start == start.status, request.expect_start, start.status))
    if request.expect_memory:
        expected = "none" if request.expect_memory == "none" else request.expect_memory
        actual = matched_memory_id(start) or "none"
        checks.append(RunnerScenarioCheck("memory", expected == actual, expected, actual))
    if request.expect_candidate:
        actual = after_task.status if after_task is not None else "not-run"
        checks.append(RunnerScenarioCheck("candidate", request.expect_candidate == actual, request.expect_candidate, actual))
    if request.expect_checkpoint:
        actual = checkpoint.status if checkpoint is not None else "not-run"
        checks.append(RunnerScenarioCheck("checkpoint", request.expect_checkpoint == actual, request.expect_checkpoint, actual))
    return checks


def matched_memory_id(start: RunnerHookResult) -> str:
    matched = start.response.get("matched_memory", {})
    if isinstance(matched, dict):
        return str(matched.get("id", ""))
    return ""
