from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_api import AgentIntegration


class CentralMemoryUnit:
    """Small Python facade for agent runtimes that should not shell out to the CLI."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self._integration = AgentIntegration(self.root)

    def tools(self) -> dict[str, Any]:
        return self._integration.manifest()

    def task_start(
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
    ) -> dict[str, Any]:
        return self._integration.invoke(
            "cmu_task_start",
            {
                "prompt": prompt,
                "actor": actor,
                "area": area,
                "files": files or [],
                "workflow": workflow or [],
                "environment": environment or [],
                "risk": risk,
                "repeated_error": repeated_error,
                "uncertainty": uncertainty,
                "shared_contract": shared_contract,
                "irreversible": irreversible,
                "unfamiliar": unfamiliar,
                "semantic": semantic,
            },
        )

    def after_work(
        self,
        *,
        situation: str,
        future_use: str,
        scope: dict[str, list[str]],
        title: str = "",
        signals: list[str] | None = None,
        outcome: str = "",
        worked: str = "",
        failed: str = "",
        evidence: list[str] | None = None,
        liability_score: int = 1,
        suggested_next_type: str = "situation",
        confidence: float = 0.6,
    ) -> dict[str, Any]:
        return self._integration.invoke(
            "cmu_after_work",
            {
                "situation": situation,
                "title": title,
                "signals": signals or [],
                "outcome": outcome,
                "worked": worked,
                "failed": failed,
                "future_use": future_use,
                "evidence": evidence or [],
                "liability_score": liability_score,
                "suggested_next_type": suggested_next_type,
                "scope": scope,
                "confidence": confidence,
            },
        )

    def link_checkpoint(
        self,
        use_id: str,
        *,
        commit_ref: str = "HEAD",
        note: str = "",
        manual_commit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"use_id": use_id, "note": note}
        if manual_commit is None:
            payload["commit_ref"] = commit_ref
        else:
            payload["manual_commit"] = manual_commit
        return self._integration.invoke("cmu_link_checkpoint", payload)

    def review(self, memory_id: str = "") -> dict[str, Any]:
        return self._integration.invoke("cmu_review", {"memory_id": memory_id})
