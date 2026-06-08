from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .runner_hooks import AutonomousRunnerHooks, RUNNER_HOOKS_VERSION, RunnerHookResult


CODEX_RUNNER_ADAPTER_VERSION = "cmu-codex-runner-adapter/v1"

CODEX_EVENT_ALIASES = {
    "task_started": "codex.task_started",
    "codex.task_started": "codex.task_started",
    "task_finished": "codex.task_finished",
    "codex.task_finished": "codex.task_finished",
    "checkpoint_created": "codex.checkpoint_created",
    "codex.checkpoint_created": "codex.checkpoint_created",
    "review_requested": "codex.review_requested",
    "codex.review_requested": "codex.review_requested",
}


@dataclass(frozen=True)
class CodexRunnerAdapterResult:
    version: str
    host: str
    event: str
    ok: bool
    status: str
    hook_result: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodexRunnerAdapterReport:
    root: str
    manifest: dict[str, Any]
    result: CodexRunnerAdapterResult | None = None

    def render(self) -> str:
        lines = [
            "CMU Codex Runner Adapter",
            f"Version: {CODEX_RUNNER_ADAPTER_VERSION}",
            f"Runner Hooks: {RUNNER_HOOKS_VERSION}",
            "Mode: host-specific JSON event bridge for Codex-style autonomous task events.",
            f"Root: {self.root}",
            "",
            "Events:",
        ]
        for event in self.manifest["events"]:
            mutation = "mutating" if event["mutates"] else "read-only"
            lines.append(f"- {event['event']}: {event['hook']} [{mutation}]")
            lines.append(f"  Purpose: {event['purpose']}")
        lines.extend(
            [
                "",
                "Input Shape:",
                '- {"event": "codex.task_started", "payload": {"prompt": "...", "risk": "high"}}',
                '- {"event": "codex.task_finished", "payload": {"reusable_learning": false}}',
                '- {"event": "codex.checkpoint_created", "payload": {"use_id": "use_...", "manual_commit": {"hash": "...", "files": []}}}',
                '- {"event": "codex.review_requested", "payload": {"memory_id": "mem_..."}}',
            ]
        )
        if self.result is not None:
            lines.extend(
                [
                    "",
                    "Executed Event:",
                    f"- Event: {self.result.event}",
                    f"- Status: {self.result.status}",
                    f"- OK: {'yes' if self.result.ok else 'no'}",
                ]
            )
            hook_result = self.result.hook_result or {}
            if hook_result:
                lines.append(f"- Hook: {hook_result.get('hook')}")
                lines.append(f"- Mutated Store: {'yes' if hook_result.get('mutates') else 'no'}")
                receipt = hook_result.get("response", {}).get("receipt")
                if isinstance(receipt, dict) and receipt.get("id"):
                    lines.append(f"- Receipt: {receipt['id']}")
            if self.result.error:
                lines.append(f"- Error: {self.result.error}")
        lines.extend(
            [
                "",
                "Proof Meaning: Codex-style runner events can enter CMU through a stable host adapter while "
                "the existing runner hooks and AgentIntegration boundary still own trigger, retrieval, receipts, "
                "Candidate Memory, checkpoint linking, and review behavior.",
            ]
        )
        return "\n".join(lines)


class CodexRunnerAdapter:
    """Host-specific event adapter for Codex-like autonomous runners."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.hooks = AutonomousRunnerHooks(self.root)

    def manifest(self) -> dict[str, Any]:
        return {
            "version": CODEX_RUNNER_ADAPTER_VERSION,
            "host": "codex",
            "runner_hooks_version": RUNNER_HOOKS_VERSION,
            "events": [
                {
                    "event": "codex.task_started",
                    "hook": "before_task",
                    "mutates": True,
                    "purpose": "Run CMU before meaningful Codex work and create a receipt only when memory surfaces.",
                },
                {
                    "event": "codex.task_finished",
                    "hook": "after_task",
                    "mutates": True,
                    "purpose": "Save Candidate Memory only when the finished task produced reusable learning.",
                },
                {
                    "event": "codex.checkpoint_created",
                    "hook": "after_checkpoint",
                    "mutates": True,
                    "purpose": "Link a surfaced-memory receipt to Git or explicit checkpoint metadata.",
                },
                {
                    "event": "codex.review_requested",
                    "hook": "review",
                    "mutates": False,
                    "purpose": "Inspect usefulness and drag review cards without changing stable trust.",
                },
            ],
        }

    def handle(self, event: dict[str, Any]) -> CodexRunnerAdapterResult:
        if not isinstance(event, dict):
            return self._invalid("", "Codex runner event must be a JSON object.")
        raw_event = text_value(event.get("event"))
        normalized = CODEX_EVENT_ALIASES.get(raw_event)
        if not normalized:
            return self._invalid(raw_event, f"Unknown Codex runner event: {raw_event or '<missing>'}")
        payload = event.get("payload", {})
        if payload == {}:
            payload = {key: value for key, value in event.items() if key != "event"}
        if not isinstance(payload, dict):
            return self._invalid(normalized, "Codex runner event payload must be a JSON object.")
        try:
            result = self._dispatch(normalized, payload)
        except (KeyError, TypeError, ValueError) as error:
            return self._invalid(normalized, str(error))
        return CodexRunnerAdapterResult(
            version=CODEX_RUNNER_ADAPTER_VERSION,
            host="codex",
            event=normalized,
            ok=result.ok,
            status=result.status,
            hook_result=result.to_dict(),
        )

    def _dispatch(self, event: str, payload: dict[str, Any]) -> RunnerHookResult:
        if event == "codex.task_started":
            return self.hooks.before_task(
                required_text(payload, "prompt"),
                actor=optional_text(payload, "actor") or "agent",
                area=optional_text(payload, "area"),
                files=text_list(payload, "files"),
                workflow=text_list(payload, "workflow"),
                environment=text_list(payload, "environment") or text_list(payload, "env"),
                risk=optional_text(payload, "risk") or "medium",
                repeated_error=bool(payload.get("repeated_error", False)),
                uncertainty=bool(payload.get("uncertainty", False)),
                shared_contract=bool(payload.get("shared_contract", False)),
                irreversible=bool(payload.get("irreversible", False)),
                unfamiliar=bool(payload.get("unfamiliar", False)),
                semantic=optional_text(payload, "semantic") or "off",
            )
        if event == "codex.task_finished":
            return self.hooks.after_task(
                reusable_learning=bool(payload.get("reusable_learning", False)),
                title=optional_text(payload, "title"),
                situation=optional_text(payload, "situation"),
                signals=text_list(payload, "signals"),
                outcome=optional_text(payload, "outcome"),
                worked=optional_text(payload, "worked"),
                failed=optional_text(payload, "failed"),
                future_use=optional_text(payload, "future_use"),
                evidence=text_list(payload, "evidence"),
                liability_score=int(payload.get("liability_score", payload.get("liability", 1))),
                suggested_next_type=optional_text(payload, "suggested_next_type") or "situation",
                confidence=float(payload.get("confidence", 0.6)),
                scope=dict_value(payload.get("scope", {})),
            )
        if event == "codex.checkpoint_created":
            return self.hooks.after_checkpoint(
                required_text(payload, "use_id"),
                commit_ref=optional_text(payload, "commit_ref") or "HEAD",
                note=optional_text(payload, "note"),
                manual_commit=optional_dict(payload.get("manual_commit")),
            )
        if event == "codex.review_requested":
            return self.hooks.review(optional_text(payload, "memory_id"))
        raise ValueError(f"unsupported Codex runner event: {event}")

    def _invalid(self, event: str, error: str) -> CodexRunnerAdapterResult:
        return CodexRunnerAdapterResult(
            version=CODEX_RUNNER_ADAPTER_VERSION,
            host="codex",
            event=event,
            ok=False,
            status="invalid-event",
            error=error,
        )


def codex_runner_report(root: Path | str = ".", event: dict[str, Any] | None = None) -> CodexRunnerAdapterReport:
    adapter = CodexRunnerAdapter(root)
    result = adapter.handle(event) if event is not None else None
    return CodexRunnerAdapterReport(root=str(root), manifest=adapter.manifest(), result=result)


def required_text(payload: dict[str, Any], key: str) -> str:
    value = optional_text(payload, key)
    if not value:
        raise ValueError(f"{key} is required")
    return value


def optional_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def text_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def text_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("manual_commit must be a JSON object")
    return value


def dict_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("scope must be a JSON object")
    return value
