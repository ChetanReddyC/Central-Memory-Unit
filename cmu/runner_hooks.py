from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .agent_api import AGENT_API_VERSION
from .sdk import CentralMemoryUnit


RUNNER_HOOKS_VERSION = "cmu-runner-hooks/v1"


@dataclass(frozen=True)
class RunnerHookResult:
    hook: str
    status: str
    ok: bool = True
    mutates: bool = False
    response: dict[str, Any] = field(default_factory=dict)
    next_hooks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutonomousRunnerHooks:
    """Event-shaped facade for autonomous runners.

    This layer deliberately delegates to CentralMemoryUnit/AgentIntegration so runner
    adapters do not duplicate trigger, retrieval, receipt, Candidate, or review logic.
    """

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.cmu = CentralMemoryUnit(self.root)

    def manifest(self) -> dict[str, Any]:
        return {
            "version": RUNNER_HOOKS_VERSION,
            "agent_api_version": AGENT_API_VERSION,
            "hooks": [
                {
                    "name": "before_task",
                    "event": "task.start",
                    "mutates": True,
                    "delegates_to": "cmu_task_start",
                    "purpose": "Run trigger, onboarding, and grounded memory guidance before meaningful work.",
                },
                {
                    "name": "after_task",
                    "event": "task.finish",
                    "mutates": True,
                    "delegates_to": "cmu_after_work",
                    "purpose": "Save Candidate Memory only when the completed task produced reusable learning.",
                },
                {
                    "name": "after_checkpoint",
                    "event": "checkpoint.created",
                    "mutates": True,
                    "delegates_to": "cmu_link_checkpoint",
                    "purpose": "Link a surfaced-memory receipt to Git or manual checkpoint evidence.",
                },
                {
                    "name": "review",
                    "event": "memory.review",
                    "mutates": False,
                    "delegates_to": "cmu_review",
                    "purpose": "Read usefulness and drag review cards without changing memory trust.",
                },
            ],
        }

    def before_task(
        self,
        prompt: str,
        *,
        actor: str = "agent",
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
        semantic: str = "off",
    ) -> RunnerHookResult:
        response = self.cmu.task_start(
            prompt,
            actor=actor,
            area=area,
            files=files or [],
            workflow=workflow or [],
            environment=environment or [],
            risk=risk,
            repeated_error=repeated_error,
            uncertainty=uncertainty,
            shared_contract=shared_contract,
            irreversible=irreversible,
            unfamiliar=unfamiliar,
            semantic=semantic,
        )
        receipt = response.get("receipt")
        next_hooks = ["after_task"]
        if isinstance(receipt, dict) and receipt.get("id"):
            next_hooks.append("after_checkpoint")
        next_hooks.append("review")
        return RunnerHookResult(
            hook="before_task",
            status=str(response.get("status", "unknown")),
            ok=bool(response.get("ok", False)),
            mutates=response.get("status") == "action-note",
            response=response,
            next_hooks=next_hooks,
        )

    def after_task(
        self,
        *,
        reusable_learning: bool,
        situation: str = "",
        future_use: str = "",
        scope: dict[str, list[str]] | None = None,
        title: str = "",
        signals: list[str] | None = None,
        outcome: str = "",
        worked: str = "",
        failed: str = "",
        evidence: list[str] | None = None,
        liability_score: int = 1,
        suggested_next_type: str = "situation",
        confidence: float = 0.6,
    ) -> RunnerHookResult:
        if not reusable_learning:
            return RunnerHookResult(
                hook="after_task",
                status="skipped-no-reusable-learning",
                ok=True,
                mutates=False,
                response={
                    "ok": True,
                    "status": "skipped-no-reusable-learning",
                    "guidance": "Do not call CMU after-work memory unless the task produced reusable situational intelligence.",
                },
                next_hooks=["after_checkpoint", "review"],
            )
        response = self.cmu.after_work(
            situation=situation,
            future_use=future_use,
            scope=scope or {},
            title=title,
            signals=signals or [],
            outcome=outcome,
            worked=worked,
            failed=failed,
            evidence=evidence or [],
            liability_score=liability_score,
            suggested_next_type=suggested_next_type,
            confidence=confidence,
        )
        return RunnerHookResult(
            hook="after_task",
            status=str(response.get("status", "unknown")),
            ok=bool(response.get("ok", False)),
            mutates=response.get("status") == "candidate-saved",
            response=response,
            next_hooks=["after_checkpoint", "review"],
        )

    def after_checkpoint(
        self,
        use_id: str,
        *,
        commit_ref: str = "HEAD",
        note: str = "",
        manual_commit: dict[str, Any] | None = None,
    ) -> RunnerHookResult:
        response = self.cmu.link_checkpoint(
            use_id,
            commit_ref=commit_ref,
            note=note,
            manual_commit=manual_commit,
        )
        return RunnerHookResult(
            hook="after_checkpoint",
            status=str(response.get("status", "unknown")),
            ok=bool(response.get("ok", False)),
            mutates=response.get("status") == "checkpoint-linked",
            response=response,
            next_hooks=["review"],
        )

    def review(self, memory_id: str = "") -> RunnerHookResult:
        response = self.cmu.review(memory_id)
        return RunnerHookResult(
            hook="review",
            status=str(response.get("status", "unknown")),
            ok=bool(response.get("ok", False)),
            mutates=False,
            response=response,
            next_hooks=[],
        )


@dataclass(frozen=True)
class RunnerHooksReport:
    root: str
    manifest: dict[str, Any]
    result: RunnerHookResult | None = None

    def render(self) -> str:
        lines = [
            "CMU Autonomous Runner Hooks",
            f"Version: {RUNNER_HOOKS_VERSION}",
            f"Agent Boundary: {self.manifest['agent_api_version']}",
            "Mode: runner integration contract. Without a prompt this is read-only; with a prompt it runs the real before_task hook.",
            f"Root: {self.root}",
            "",
            "Hook Sequence:",
        ]
        for hook in self.manifest["hooks"]:
            mutation = "mutating" if hook["mutates"] else "read-only"
            lines.append(f"- {hook['name']} ({hook['event']}): {hook['delegates_to']} [{mutation}]")
            lines.append(f"  Purpose: {hook['purpose']}")
        lines.extend(
            [
                "",
                "Runner Rule:",
                "- Call before_task before meaningful work.",
                "- Call after_task only when reusable situational intelligence appeared.",
                "- Call after_checkpoint when work reaches a Git or explicit checkpoint.",
                "- Call review to inspect usefulness/drag evidence; do not change stable trust silently.",
            ]
        )
        if self.result is not None:
            lines.extend(
                [
                    "",
                    "Executed Hook:",
                    f"- Hook: {self.result.hook}",
                    f"- Status: {self.result.status}",
                    f"- Mutated Store: {'yes' if self.result.mutates else 'no'}",
                    f"- Next Hooks: {', '.join(self.result.next_hooks) if self.result.next_hooks else 'none'}",
                ]
            )
            receipt = self.result.response.get("receipt")
            if isinstance(receipt, dict) and receipt.get("id"):
                lines.append(f"- Receipt: {receipt['id']}")
            matched = self.result.response.get("matched_memory", {})
            if matched:
                lines.append(f"- Matched Memory: {matched.get('id')} {matched.get('title')}")
        lines.extend(
            [
                "",
                "Proof Meaning: autonomous runners can use these event hooks without parsing human CLI output, "
                "while CMU still enforces the existing AgentIntegration gates for trigger, retrieval, receipts, "
                "Candidate Memory, checkpoint evidence, and review.",
            ]
        )
        return "\n".join(lines)


def runner_hooks_report(
    root: Path | str = ".",
    *,
    prompt: str = "",
    actor: str = "agent",
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
    semantic: str = "off",
) -> RunnerHooksReport:
    hooks = AutonomousRunnerHooks(root)
    result = None
    if prompt.strip():
        result = hooks.before_task(
            prompt,
            actor=actor,
            area=area,
            files=files or [],
            workflow=workflow or [],
            environment=environment or [],
            risk=risk,
            repeated_error=repeated_error,
            uncertainty=uncertainty,
            shared_contract=shared_contract,
            irreversible=irreversible,
            unfamiliar=unfamiliar,
            semantic=semantic,
        )
    return RunnerHooksReport(root=str(root), manifest=hooks.manifest(), result=result)
