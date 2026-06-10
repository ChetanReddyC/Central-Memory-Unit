from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .evidence_session import EvidenceSessionReport, run_evidence_session
from .json_store import update_json
from .models import Memory, utc_now
from .runner_hooks import AutonomousRunnerHooks, RunnerHookResult
from .usage import MemoryUseReceipt, MemoryUseStore


WORK_LOOP_RUN_VERSION = "cmu-work-loop-run/v1"


@dataclass(frozen=True)
class WorkLoopEventResult:
    event: str
    status: str
    ok: bool
    mutates: bool
    receipt_id: str = ""
    memory_id: str = ""
    evidence_linked: int = 0
    evidence_needs_review: int = 0

    def render(self) -> str:
        detail = []
        if self.receipt_id:
            detail.append(f"receipt={self.receipt_id}")
        if self.memory_id:
            detail.append(f"memory={self.memory_id}")
        if self.evidence_linked or self.evidence_needs_review:
            detail.append(f"evidence linked={self.evidence_linked} review={self.evidence_needs_review}")
        suffix = f" ({'; '.join(detail)})" if detail else ""
        mutation = "mutating" if self.mutates else "read-only"
        return f"- {self.event}: {self.status} ok={str(self.ok).lower()} {mutation}{suffix}"


@dataclass(frozen=True)
class WorkLoopRunRecord:
    id: str
    created_at: str
    root: str
    event_count: int
    ok: bool
    auto_evidence: bool
    applied_evidence: bool
    statuses: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkLoopRunReport:
    record: WorkLoopRunRecord
    results: list[WorkLoopEventResult]
    recorded: bool

    @property
    def ok(self) -> bool:
        return self.record.ok

    def render(self) -> str:
        lines = [
            "CMU Automatic Work Loop Run",
            f"Version: {WORK_LOOP_RUN_VERSION}",
            "Mode: executes event-shaped runner hooks and optional evidence sessions from one host/runtime JSON input.",
            f"Run: {self.record.id}",
            f"Root: {self.record.root}",
            f"Recorded: {'yes' if self.recorded else 'no'}",
            f"Auto Evidence: {'yes' if self.record.auto_evidence else 'no'}",
            f"Applied Evidence: {'yes' if self.record.applied_evidence else 'no'}",
            f"Summary: events={self.record.event_count} ok={str(self.record.ok).lower()}",
            "",
            "Events:",
        ]
        if self.results:
            lines.extend(result.render() for result in self.results)
        else:
            lines.append("- none")
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can be invoked as part of a runtime work loop from structured events, including task start, after-work learning, checkpoint evidence, review, and long-session evidence passes.",
            ]
        )
        return "\n".join(lines)


def run_work_loop_events(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    payload: dict[str, Any],
    *,
    auto_evidence: bool = False,
    apply_evidence: bool = False,
    record: bool = False,
) -> WorkLoopRunReport:
    root_path = Path(root)
    events = normalize_events(payload)
    hooks = AutonomousRunnerHooks(root_path)
    results: list[WorkLoopEventResult] = []
    current_receipts = list(receipts)
    for event in events:
        result = execute_event(hooks, event)
        results.append(result_from_hook(event_type(event), result))
        current_receipts = MemoryUseStore(root_path).list()
        if auto_evidence and event_type(event) in {"checkpoint.created", "evidence.session"}:
            evidence = run_evidence_session(
                root_path,
                memories,
                current_receipts,
                apply=apply_evidence,
                record=True,
            )
            results.append(result_from_evidence(evidence))
            current_receipts = MemoryUseStore(root_path).list()
    ok = all(result.ok for result in results)
    record_item = WorkLoopRunRecord(
        id=f"wlr_{uuid4().hex[:12]}",
        created_at=utc_now(),
        root=str(root_path),
        event_count=len(events),
        ok=ok,
        auto_evidence=auto_evidence,
        applied_evidence=apply_evidence,
        statuses=[result.status for result in results],
    )
    if record:
        update_json(
            root_path / ".cmu" / "work_loop_runs.json",
            {"version": 1, "runs": []},
            lambda data: append_work_loop_run(data, record_item),
        )
    return WorkLoopRunReport(record=record_item, results=results, recorded=record)


def normalize_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("events"), list):
        return [event for event in payload["events"] if isinstance(event, dict)]
    if payload:
        return [payload]
    return []


def execute_event(hooks: AutonomousRunnerHooks, event: dict[str, Any]) -> RunnerHookResult:
    name = event_type(event)
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    if name in {"task.started", "task.start", "before_task"}:
        return hooks.before_task(
            str(data.get("prompt", "")),
            actor=str(data.get("actor", "agent")),
            area=str(data.get("area", "")),
            files=list(data.get("files", [])),
            workflow=list(data.get("workflow", [])),
            environment=list(data.get("environment", data.get("env", []))),
            risk=str(data.get("risk", "medium")),
            repeated_error=bool(data.get("repeated_error", False)),
            uncertainty=bool(data.get("uncertainty", False)),
            shared_contract=bool(data.get("shared_contract", False)),
            irreversible=bool(data.get("irreversible", False)),
            unfamiliar=bool(data.get("unfamiliar", False)),
            semantic=str(data.get("semantic", "off")),
        )
    if name in {"task.finished", "task.finish", "after_task"}:
        return hooks.after_task(
            reusable_learning=bool(data.get("reusable_learning", False)),
            situation=str(data.get("situation", data.get("prompt", ""))),
            future_use=str(data.get("future_use", "")),
            scope=dict(data.get("scope", {})),
            title=str(data.get("title", "")),
            signals=list(data.get("signals", [])),
            outcome=str(data.get("outcome", "")),
            worked=str(data.get("worked", "")),
            failed=str(data.get("failed", "")),
            evidence=list(data.get("evidence", [])),
            liability_score=int(data.get("liability_score", 1)),
            suggested_next_type=str(data.get("suggested_next_type", "situation")),
            confidence=float(data.get("confidence", 0.6)),
        )
    if name in {"checkpoint.created", "checkpoint", "after_checkpoint"}:
        return hooks.after_checkpoint(
            str(data.get("use_id", "")),
            commit_ref=str(data.get("commit_ref", "HEAD")),
            note=str(data.get("note", "")),
            manual_commit=data.get("manual_commit") if isinstance(data.get("manual_commit"), dict) else None,
        )
    if name in {"review.requested", "review"}:
        return hooks.review(str(data.get("memory_id", "")))
    if name == "evidence.session":
        return RunnerHookResult(hook="evidence_session_marker", status="evidence-session-requested", ok=True, mutates=False)
    return RunnerHookResult(hook=name or "unknown", status="unsupported-event", ok=False, mutates=False, response={"event": name})


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("event", event.get("type", ""))).strip()


def result_from_hook(event: str, result: RunnerHookResult) -> WorkLoopEventResult:
    receipt = result.response.get("receipt") if isinstance(result.response, dict) else None
    matched = result.response.get("matched_memory") if isinstance(result.response, dict) else None
    candidate = result.response.get("candidate") if isinstance(result.response, dict) else None
    memory_id = ""
    if isinstance(matched, dict):
        memory_id = str(matched.get("id", ""))
    if not memory_id and isinstance(candidate, dict):
        memory_id = str(candidate.get("id", ""))
    return WorkLoopEventResult(
        event=event or result.hook,
        status=result.status,
        ok=result.ok,
        mutates=result.mutates,
        receipt_id=str(receipt.get("id", "")) if isinstance(receipt, dict) else "",
        memory_id=memory_id,
    )


def result_from_evidence(report: EvidenceSessionReport) -> WorkLoopEventResult:
    return WorkLoopEventResult(
        event="evidence.session",
        status="recorded" if report.recorded else "observed",
        ok=report.ok,
        mutates=report.record.applied or report.recorded,
        evidence_linked=report.record.linked,
        evidence_needs_review=report.record.needs_review,
    )


def append_work_loop_run(data: dict[str, Any], record: WorkLoopRunRecord) -> WorkLoopRunRecord:
    data["runs"].append(record.to_dict())
    return record


def load_work_loop_payload(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
