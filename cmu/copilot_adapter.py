from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .codex_adapter import dict_value, optional_dict, optional_text, required_text, text_list, text_value
from .runner_hooks import AutonomousRunnerHooks, RUNNER_HOOKS_VERSION, RunnerHookResult


COPILOT_RUNNER_ADAPTER_VERSION = "cmu-copilot-runner-adapter/v1"

COPILOT_EVENT_ALIASES = {
    "chat.started": "copilot.chat.started",
    "copilot.chat.started": "copilot.chat.started",
    "chat.finished": "copilot.chat.finished",
    "copilot.chat.finished": "copilot.chat.finished",
    "checkpoint.created": "copilot.checkpoint.created",
    "copilot.checkpoint.created": "copilot.checkpoint.created",
    "review.requested": "copilot.review.requested",
    "copilot.review.requested": "copilot.review.requested",
}


@dataclass(frozen=True)
class CopilotRunnerAdapterResult:
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
class CopilotRunnerAdapterReport:
    root: str
    manifest: dict[str, Any]
    result: CopilotRunnerAdapterResult | None = None

    def render(self) -> str:
        lines = [
            "CMU Copilot Runner Adapter",
            f"Version: {COPILOT_RUNNER_ADAPTER_VERSION}",
            f"Runner Hooks: {RUNNER_HOOKS_VERSION}",
            "Mode: host-specific JSON event bridge for VS Code/GitHub Copilot-style chat work events.",
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
                '- {"event": "copilot.chat.started", "payload": {"message": "...", "risk": "medium"}}',
                '- {"event": "copilot.chat.finished", "payload": {"reusable_learning": false}}',
                '- {"event": "copilot.checkpoint.created", "payload": {"use_id": "use_...", "manual_commit": {"hash": "...", "files": []}}}',
                '- {"event": "copilot.review.requested", "payload": {"memory_id": "mem_..."}}',
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
                "Proof Meaning: VS Code/GitHub Copilot-style work can enter CMU through the same runner hook contract as other hosts, without CLI prose parsing or duplicated memory behavior.",
            ]
        )
        return "\n".join(lines)


class CopilotRunnerAdapter:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.hooks = AutonomousRunnerHooks(self.root)

    def manifest(self) -> dict[str, Any]:
        return {
            "version": COPILOT_RUNNER_ADAPTER_VERSION,
            "host": "copilot",
            "runner_hooks_version": RUNNER_HOOKS_VERSION,
            "events": [
                {
                    "event": "copilot.chat.started",
                    "hook": "before_task",
                    "mutates": True,
                    "purpose": "Run CMU before meaningful Copilot chat/edit work and create a receipt only when memory surfaces.",
                },
                {
                    "event": "copilot.chat.finished",
                    "hook": "after_task",
                    "mutates": True,
                    "purpose": "Save Candidate Memory only when the chat/edit session produced reusable learning.",
                },
                {
                    "event": "copilot.checkpoint.created",
                    "hook": "after_checkpoint",
                    "mutates": True,
                    "purpose": "Link a surfaced-memory receipt to Git or explicit checkpoint metadata.",
                },
                {
                    "event": "copilot.review.requested",
                    "hook": "review",
                    "mutates": False,
                    "purpose": "Inspect usefulness and drag review cards without changing stable trust.",
                },
            ],
        }

    def handle(self, event: dict[str, Any]) -> CopilotRunnerAdapterResult:
        if not isinstance(event, dict):
            return self._invalid("", "Copilot runner event must be a JSON object.")
        raw_event = text_value(event.get("event"))
        normalized = COPILOT_EVENT_ALIASES.get(raw_event)
        if not normalized:
            return self._invalid(raw_event, f"Unknown Copilot runner event: {raw_event or '<missing>'}")
        payload = event.get("payload", {})
        if payload == {}:
            payload = {key: value for key, value in event.items() if key != "event"}
        if not isinstance(payload, dict):
            return self._invalid(normalized, "Copilot runner event payload must be a JSON object.")
        try:
            result = self._dispatch(normalized, payload)
        except (KeyError, TypeError, ValueError) as error:
            return self._invalid(normalized, str(error))
        return CopilotRunnerAdapterResult(
            version=COPILOT_RUNNER_ADAPTER_VERSION,
            host="copilot",
            event=normalized,
            ok=result.ok,
            status=result.status,
            hook_result=result.to_dict(),
        )

    def _dispatch(self, event: str, payload: dict[str, Any]) -> RunnerHookResult:
        if event == "copilot.chat.started":
            prompt = optional_text(payload, "message") or optional_text(payload, "prompt")
            if not prompt:
                raise ValueError("message is required")
            return self.hooks.before_task(
                prompt,
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
        if event == "copilot.chat.finished":
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
        if event == "copilot.checkpoint.created":
            return self.hooks.after_checkpoint(
                required_text(payload, "use_id"),
                commit_ref=optional_text(payload, "commit_ref") or "HEAD",
                note=optional_text(payload, "note"),
                manual_commit=optional_dict(payload.get("manual_commit")),
            )
        if event == "copilot.review.requested":
            return self.hooks.review(optional_text(payload, "memory_id"))
        raise ValueError(f"unsupported Copilot runner event: {event}")

    def _invalid(self, event: str, error: str) -> CopilotRunnerAdapterResult:
        return CopilotRunnerAdapterResult(
            version=COPILOT_RUNNER_ADAPTER_VERSION,
            host="copilot",
            event=event,
            ok=False,
            status="invalid-event",
            error=error,
        )


def copilot_runner_report(root: Path | str = ".", event: dict[str, Any] | None = None) -> CopilotRunnerAdapterReport:
    adapter = CopilotRunnerAdapter(root)
    result = adapter.handle(event) if event is not None else None
    return CopilotRunnerAdapterReport(root=str(root), manifest=adapter.manifest(), result=result)
