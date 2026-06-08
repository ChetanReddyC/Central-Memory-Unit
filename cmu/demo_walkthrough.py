from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .install_check import InstallCheckReport, install_check
from .quickstart import QuickstartDemoReport, quickstart_demo
from .setup import SetupGuideReport, setup_guide


DEMO_WALKTHROUGH_VERSION = "cmu-demo-walkthrough/v1"


@dataclass(frozen=True)
class DemoWalkthroughStep:
    title: str
    status: str
    detail: str
    command: str


@dataclass(frozen=True)
class DemoWalkthroughReport:
    root: str
    applied: bool
    install_report: InstallCheckReport
    setup_report: SetupGuideReport
    quickstart_report: QuickstartDemoReport
    steps: list[DemoWalkthroughStep]

    @property
    def passed(self) -> bool:
        quickstart_ok = self.quickstart_report.applied if self.applied else not self.quickstart_report.applied
        return self.install_report.passed and quickstart_ok

    def render(self) -> str:
        lines = [
            "CMU Demo Walkthrough",
            f"Version: {DEMO_WALKTHROUGH_VERSION}",
            f"Root: {self.root}",
            f"Applied: {'yes' if self.applied else 'no'}",
            f"Status: {'pass' if self.passed else 'fail'}",
            mode_line(self.applied),
            "",
            "Walkthrough:",
        ]
        for index, step in enumerate(self.steps, start=1):
            lines.extend(
                [
                    f"{index}. {step.title}",
                    f"   Status: {step.status}",
                    f"   Command: {step.command}",
                    f"   Detail: {step.detail}",
                ]
            )
        lines.extend(
            [
                "",
                "Proof Summary:",
                f"- Install Check: {'pass' if self.install_report.passed else 'fail'}",
                f"- Setup Guide Host: {self.setup_report.host}",
                f"- Project Scripts: {format_scripts(self.setup_report.status.pyproject_scripts)}",
                f"- Agent Tools: {', '.join(self.setup_report.agent_tools)}",
                f"- MCP Tools: {', '.join(self.setup_report.mcp_tools)}",
                f"- Quickstart Applied: {'yes' if self.quickstart_report.applied else 'no'}",
            ]
        )
        if self.quickstart_report.memory_id:
            lines.append(f"- Demo Memory: {self.quickstart_report.memory_id}")
        if self.quickstart_report.receipt_id:
            lines.append(f"- Demo Receipt: {self.quickstart_report.receipt_id}")
        if self.quickstart_report.commit_hash:
            lines.append(f"- Demo Git Checkpoint: {self.quickstart_report.commit_hash}")
        if self.quickstart_report.reason:
            lines.append(f"- Quickstart Reason: {self.quickstart_report.reason}")
        lines.extend(
            [
                "",
                "Next Real Use:",
                "- Run `cmu start --actor agent --area <area> --file <path> \"<task>\"` before meaningful work.",
                "- Run `cmu remember` only when reusable situational intelligence appears.",
                "- Run `cmu use-link-auto` or `cmu use-link-latest` after a checkpoint, then `cmu use-review`.",
                "",
                "Proof Meaning: demo-walkthrough ties installation validation, host setup guidance, and the "
                "Git-backed quickstart proof into one operator path without inventing a separate demo-only memory loop.",
            ]
        )
        return "\n".join(lines)


def demo_walkthrough(root: Path | str = ".", *, apply: bool = False) -> DemoWalkthroughReport:
    root_path = Path(root)
    install_report = install_check(root_path)
    setup_report = setup_guide(root_path, host="all")
    quickstart_report = quickstart_demo(root_path, apply=apply)
    steps = [
        DemoWalkthroughStep(
            title="Validate adoption surface",
            status="pass" if install_report.passed else "fail",
            command="cmu install-check",
            detail="README, pyproject, SDK import, module entrypoint, setup-guide, and MCP schema checked.",
        ),
        DemoWalkthroughStep(
            title="Inspect host setup",
            status="pass",
            command="cmu setup-guide --host all",
            detail=(
                "CLI, Python SDK, MCP, and Codex MCP guidance generated from "
                f"{format_scripts(setup_report.status.pyproject_scripts)}."
            ),
        ),
        DemoWalkthroughStep(
            title="Run memory proof loop",
            status=quickstart_status(quickstart_report, apply),
            command="cmu quickstart-demo --apply" if apply else "cmu quickstart-demo",
            detail=quickstart_detail(quickstart_report, apply),
        ),
        DemoWalkthroughStep(
            title="Rehearse real work-cycle handoff",
            status="ready" if install_report.passed else "blocked",
            command='cmu start --actor agent --area <area> --file <path> "<task>"',
            detail="Use task-start before meaningful work, remember only reusable lessons, then link and review evidence.",
        ),
    ]
    return DemoWalkthroughReport(
        root=str(root_path),
        applied=apply,
        install_report=install_report,
        setup_report=setup_report,
        quickstart_report=quickstart_report,
        steps=steps,
    )


def mode_line(applied: bool) -> str:
    if applied:
        return "Mode: applies the existing quickstart proof; creates demo memory, receipt, Git checkpoint, and link."
    return "Mode: read-only walkthrough; no stores, memories, receipts, or Git checkpoints are mutated."


def format_scripts(scripts: dict[str, str]) -> str:
    if not scripts:
        return "none"
    return ", ".join(f"{name}={target}" for name, target in sorted(scripts.items()))


def quickstart_status(report: QuickstartDemoReport, applied: bool) -> str:
    if applied:
        return "pass" if report.applied else "fail"
    return "planned" if not report.applied and report.reason == "dry run" else "fail"


def quickstart_detail(report: QuickstartDemoReport, applied: bool) -> str:
    if report.applied:
        return "Created demo Practice memory, task-start receipt, Git checkpoint, linked evidence, and usefulness summary."
    if applied:
        return report.reason or "quickstart apply failed"
    return "Dry-run proof plan rendered; rerun with `--apply` inside a Git repository for linked evidence."
