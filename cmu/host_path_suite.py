from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from .codex_adapter import CodexRunnerAdapter
from .fixture_repos import FIXTURE_KINDS, create_fixture_repo
from .models import MemoryStatus
from .openai_adapter import OpenAIRunnerAdapter
from .runner_scenarios import RunnerScenarioRequest, run_runner_scenario
from .scenarios import ScenarioLibraryStore, compare_scenario_library, run_scenario_library
from .store import MemoryStore
from .usage import MemoryUseStore


HOST_PATH_SUITE_VERSION = "cmu-host-path-suite/v1"


@dataclass(frozen=True)
class HostPathSuiteItem:
    kind: str
    scenario_passed: bool
    runner_passed: bool
    codex_ok: bool
    openai_ok: bool
    comparison_class: str
    fixture_root: str

    @property
    def passed(self) -> bool:
        return (
            self.scenario_passed
            and self.runner_passed
            and self.codex_ok
            and self.openai_ok
            and self.comparison_class == "unchanged-pass"
        )

    def render(self) -> str:
        return (
            f"- {self.kind}: {'pass' if self.passed else 'review'} "
            f"scenario={'pass' if self.scenario_passed else 'fail'} "
            f"runner={'pass' if self.runner_passed else 'fail'} "
            f"codex={'pass' if self.codex_ok else 'fail'} "
            f"openai={'pass' if self.openai_ok else 'fail'} "
            f"compare={self.comparison_class}"
        )


@dataclass
class HostPathSuiteReport:
    work_dir: Path
    items: list[HostPathSuiteItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(item.passed for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Host Path Suite",
            f"Version: {HOST_PATH_SUITE_VERSION}",
            "Mode: fixture-backed host-path suite using scenario-run, runner-scenario, Codex/OpenAI host adapters, and scenario-compare behavior.",
            f"Work Dir: {self.work_dir}",
            f"Status: {'pass' if self.passed else 'review'}",
            "",
            "Fixtures:",
        ]
        lines.extend(item.render() for item in self.items) if self.items else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: generated repo fixtures now exercise the same memory behavior through library scenarios, autonomous runner hooks, Codex-style events, OpenAI Agents-style events, and before/after comparison.",
            ]
        )
        return "\n".join(lines)


def run_host_path_suite(work_dir: Path | str | None = None, *, keep: bool = False) -> HostPathSuiteReport:
    if work_dir is None:
        with TemporaryDirectory(prefix="cmu-host-path-") as tmp:
            return _run_suite(Path(tmp))
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    report = _run_suite(root)
    if not keep:
        pass
    return report


def _run_suite(root: Path) -> HostPathSuiteReport:
    items: list[HostPathSuiteItem] = []
    for kind in sorted(FIXTURE_KINDS):
        fixture_root = root / kind
        if fixture_root.exists() and any(fixture_root.iterdir()):
            fixture_root = root / f"{kind}-suite"
        created = create_fixture_repo(kind, fixture_root)
        memories = MemoryStore(fixture_root).list(status=MemoryStatus.ACTIVE)
        receipts = MemoryUseStore(fixture_root).list()
        scenarios = ScenarioLibraryStore(fixture_root).list()
        scenario_report = run_scenario_library(scenarios, memories, receipts, tag="fixture")
        scenario = scenarios[0]
        runner_report = run_runner_scenario(
            fixture_root,
            RunnerScenarioRequest(
                prompt=scenario.prompt,
                actor=scenario.actor,
                area=scenario.area,
                files=scenario.files,
                workflow=scenario.workflow,
                environment=scenario.environment,
                risk=scenario.risk,
                irreversible=scenario.irreversible,
                expect_start=scenario.expect_action or "action-note",
                expect_memory=created.memory_id,
            ),
            work_dir=fixture_root / ".manual" / "host-path-suite",
        )
        codex_result = CodexRunnerAdapter(fixture_root).handle(
            {
                "event": "codex.task_started",
                "payload": {
                    "prompt": scenario.prompt,
                    "actor": scenario.actor,
                    "area": scenario.area,
                    "files": scenario.files,
                    "workflow": scenario.workflow,
                    "environment": scenario.environment,
                    "risk": scenario.risk,
                    "irreversible": scenario.irreversible,
                },
            }
        )
        openai_result = OpenAIRunnerAdapter(fixture_root).handle(
            {
                "event": "openai.run.started",
                "payload": {
                    "input": scenario.prompt,
                    "actor": scenario.actor,
                    "area": scenario.area,
                    "files": scenario.files,
                    "workflow": scenario.workflow,
                    "environment": scenario.environment,
                    "risk": scenario.risk,
                    "irreversible": scenario.irreversible,
                },
            }
        )
        comparison = compare_scenario_library(
            scenarios,
            baseline_memories=memories,
            baseline_receipts=receipts,
            current_memories=memories,
            current_receipts=receipts,
            baseline_root=str(fixture_root),
            current_root=str(fixture_root),
            tag="fixture",
        )
        items.append(
            HostPathSuiteItem(
                kind=kind,
                scenario_passed=bool(scenario_report.items) and not scenario_report.has_review_items(),
                runner_passed=runner_report.passed,
                codex_ok=codex_result.ok and codex_result.status == "action-note",
                openai_ok=openai_result.ok and openai_result.status == "action-note",
                comparison_class=comparison.items[0].classification if comparison.items else "missing",
                fixture_root=str(fixture_root),
            )
        )
    return HostPathSuiteReport(work_dir=root, items=items)
