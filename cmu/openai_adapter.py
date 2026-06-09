from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .codex_adapter import dict_value, optional_dict, optional_text, required_text, text_list, text_value
from .runner_hooks import AutonomousRunnerHooks, RUNNER_HOOKS_VERSION, RunnerHookResult


OPENAI_RUNNER_ADAPTER_VERSION = "cmu-openai-runner-adapter/v1"

OPENAI_EVENT_ALIASES = {
    "run.started": "openai.run.started",
    "openai.run.started": "openai.run.started",
    "run.completed": "openai.run.completed",
    "openai.run.completed": "openai.run.completed",
    "checkpoint.created": "openai.checkpoint.created",
    "openai.checkpoint.created": "openai.checkpoint.created",
    "review.requested": "openai.review.requested",
    "openai.review.requested": "openai.review.requested",
}


@dataclass(frozen=True)
class OpenAIRunnerAdapterResult:
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
class OpenAIRunnerAdapterReport:
    root: str
    manifest: dict[str, Any]
    result: OpenAIRunnerAdapterResult | None = None

    def render(self) -> str:
        lines = [
            "CMU OpenAI Runner Adapter",
            f"Version: {OPENAI_RUNNER_ADAPTER_VERSION}",
            f"Runner Hooks: {RUNNER_HOOKS_VERSION}",
            "Mode: host-specific JSON event bridge for OpenAI Agents-style autonomous run events.",
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
                '- {"event": "openai.run.started", "payload": {"input": "...", "risk": "high"}}',
                '- {"event": "openai.run.completed", "payload": {"reusable_learning": false}}',
                '- {"event": "openai.checkpoint.created", "payload": {"use_id": "use_...", "manual_commit": {"hash": "...", "files": []}}}',
                '- {"event": "openai.review.requested", "payload": {"memory_id": "mem_..."}}',
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
                "Proof Meaning: OpenAI Agents-style run events can enter CMU through the same autonomous runner hook boundary as other hosts, without parsing CLI prose or bypassing receipt and Candidate Memory gates.",
            ]
        )
        return "\n".join(lines)


class OpenAIRunnerAdapter:
    """Host-specific event adapter for OpenAI Agents-style runners."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.hooks = AutonomousRunnerHooks(self.root)

    def manifest(self) -> dict[str, Any]:
        return {
            "version": OPENAI_RUNNER_ADAPTER_VERSION,
            "host": "openai",
            "runner_hooks_version": RUNNER_HOOKS_VERSION,
            "events": [
                {
                    "event": "openai.run.started",
                    "hook": "before_task",
                    "mutates": True,
                    "purpose": "Run CMU before meaningful OpenAI Agents work and create a receipt only when memory surfaces.",
                },
                {
                    "event": "openai.run.completed",
                    "hook": "after_task",
                    "mutates": True,
                    "purpose": "Save Candidate Memory only when the run produced reusable learning.",
                },
                {
                    "event": "openai.checkpoint.created",
                    "hook": "after_checkpoint",
                    "mutates": True,
                    "purpose": "Link a surfaced-memory receipt to Git or explicit checkpoint metadata.",
                },
                {
                    "event": "openai.review.requested",
                    "hook": "review",
                    "mutates": False,
                    "purpose": "Inspect usefulness and drag review cards without changing stable trust.",
                },
            ],
        }

    def handle(self, event: dict[str, Any]) -> OpenAIRunnerAdapterResult:
        if not isinstance(event, dict):
            return self._invalid("", "OpenAI runner event must be a JSON object.")
        raw_event = text_value(event.get("event"))
        normalized = OPENAI_EVENT_ALIASES.get(raw_event)
        if not normalized:
            return self._invalid(raw_event, f"Unknown OpenAI runner event: {raw_event or '<missing>'}")
        payload = event.get("payload", {})
        if payload == {}:
            payload = {key: value for key, value in event.items() if key != "event"}
        if not isinstance(payload, dict):
            return self._invalid(normalized, "OpenAI runner event payload must be a JSON object.")
        try:
            result = self._dispatch(normalized, payload)
        except (KeyError, TypeError, ValueError) as error:
            return self._invalid(normalized, str(error))
        return OpenAIRunnerAdapterResult(
            version=OPENAI_RUNNER_ADAPTER_VERSION,
            host="openai",
            event=normalized,
            ok=result.ok,
            status=result.status,
            hook_result=result.to_dict(),
        )

    def _dispatch(self, event: str, payload: dict[str, Any]) -> RunnerHookResult:
        if event == "openai.run.started":
            prompt = optional_text(payload, "input") or optional_text(payload, "prompt")
            if not prompt:
                raise ValueError("input is required")
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
        if event == "openai.run.completed":
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
        if event == "openai.checkpoint.created":
            return self.hooks.after_checkpoint(
                required_text(payload, "use_id"),
                commit_ref=optional_text(payload, "commit_ref") or "HEAD",
                note=optional_text(payload, "note"),
                manual_commit=optional_dict(payload.get("manual_commit")),
            )
        if event == "openai.review.requested":
            return self.hooks.review(optional_text(payload, "memory_id"))
        raise ValueError(f"unsupported OpenAI runner event: {event}")

    def _invalid(self, event: str, error: str) -> OpenAIRunnerAdapterResult:
        return OpenAIRunnerAdapterResult(
            version=OPENAI_RUNNER_ADAPTER_VERSION,
            host="openai",
            event=event,
            ok=False,
            status="invalid-event",
            error=error,
        )


def openai_runner_report(root: Path | str = ".", event: dict[str, Any] | None = None) -> OpenAIRunnerAdapterReport:
    adapter = OpenAIRunnerAdapter(root)
    result = adapter.handle(event) if event is not None else None
    return OpenAIRunnerAdapterReport(root=str(root), manifest=adapter.manifest(), result=result)
