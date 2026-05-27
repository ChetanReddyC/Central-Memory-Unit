from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from .json_store import read_json, update_json
from .models import Memory, MemoryScope, MemoryType, utc_now
from .remembering import RememberDecision, RememberRequest, remember_candidate


TRACE_STORE_FILE = "raw_traces.json"


@dataclass
class RawTrace:
    id: str
    prompt: str
    actor: str = "developer"
    area: str = ""
    files: list[str] = field(default_factory=list)
    workflow: list[str] = field(default_factory=list)
    environment: list[str] = field(default_factory=list)
    risk: str = "medium"
    learning_signals: list[str] = field(default_factory=list)
    outcome: str = ""
    worked: str = ""
    failed: str = ""
    future_use: str = ""
    evidence: list[str] = field(default_factory=list)
    status: str = "raw"
    distilled_memory_id: str = ""
    distillation_reason: str = ""
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())

    @classmethod
    def create(
        cls,
        *,
        prompt: str,
        actor: str = "developer",
        area: str = "",
        files: list[str] | None = None,
        workflow: list[str] | None = None,
        environment: list[str] | None = None,
        risk: str = "medium",
        learning_signals: list[str] | None = None,
        outcome: str = "",
        worked: str = "",
        failed: str = "",
        future_use: str = "",
        evidence: list[str] | None = None,
    ) -> "RawTrace":
        return cls(
            id=f"trace_{uuid4().hex[:12]}",
            prompt=prompt.strip(),
            actor=actor.strip() or "developer",
            area=area.strip(),
            files=clean_list(files or []),
            workflow=clean_list(workflow or []),
            environment=clean_list(environment or []),
            risk=risk.strip() or "medium",
            learning_signals=clean_list(learning_signals or []),
            outcome=outcome.strip(),
            worked=worked.strip(),
            failed=failed.strip(),
            future_use=future_use.strip(),
            evidence=clean_list(evidence or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RawTrace":
        return cls(
            id=data["id"],
            prompt=data["prompt"],
            actor=data.get("actor", "developer"),
            area=data.get("area", ""),
            files=list(data.get("files", [])),
            workflow=list(data.get("workflow", [])),
            environment=list(data.get("environment", [])),
            risk=data.get("risk", "medium"),
            learning_signals=list(data.get("learning_signals", [])),
            outcome=data.get("outcome", ""),
            worked=data.get("worked", ""),
            failed=data.get("failed", ""),
            future_use=data.get("future_use", ""),
            evidence=list(data.get("evidence", [])),
            status=data.get("status", "raw"),
            distilled_memory_id=data.get("distilled_memory_id", ""),
            distillation_reason=data.get("distillation_reason", ""),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )


class RawTraceStore:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.store_file = self.root / ".cmu" / TRACE_STORE_FILE

    def add(self, trace: RawTrace) -> RawTrace:
        return update_json(
            self.store_file,
            {"version": 1, "traces": []},
            lambda data: append_trace(data, trace),
        )

    def update(self, trace: RawTrace) -> RawTrace:
        trace.updated_at = utc_now()
        return update_json(
            self.store_file,
            {"version": 1, "traces": []},
            lambda data: replace_trace(data, trace),
        )

    def list(self, *, include_distilled: bool = True) -> list[RawTrace]:
        traces = [RawTrace.from_dict(item) for item in self._read()["traces"]]
        if not include_distilled:
            traces = [trace for trace in traces if trace.status == "raw"]
        return sorted(traces, key=lambda item: item.updated_at, reverse=True)

    def get(self, trace_id: str) -> RawTrace:
        for trace in self.list():
            if trace.id == trace_id:
                return trace
        raise KeyError(f"Raw trace not found: {trace_id}")

    def _read(self) -> dict:
        return read_json(self.store_file, {"version": 1, "traces": []})


@dataclass
class TraceDistillation:
    trace: RawTrace
    decision: RememberDecision

    @property
    def status(self) -> str:
        if self.decision.saved:
            return "candidate-ready"
        if "duplicate" in self.decision.reason.lower():
            return "duplicate"
        return "noise-rejected"

    def render(self) -> str:
        memory_id = self.decision.memory.id if self.decision.memory else "none"
        return "\n".join(
            [
                f"- {self.trace.id}: {self.status}",
                f"  Prompt: {self.trace.prompt}",
                f"  Reason: {self.decision.reason}",
                f"  Candidate: {memory_id}",
            ]
        )


@dataclass
class TraceDistillationReport:
    distillations: list[TraceDistillation]
    apply: bool = False

    def render(self) -> str:
        lines = [
            "CMU Raw Trace Distillation",
            "Mode: apply" if self.apply else "Mode: preview; no Candidate Memories or trace statuses are mutated.",
            "",
            "Summary:",
            f"- Traces Reviewed: {len(self.distillations)}",
            f"- Candidate Ready: {sum(1 for item in self.distillations if item.status == 'candidate-ready')}",
            f"- Noise Rejected: {sum(1 for item in self.distillations if item.status == 'noise-rejected')}",
            f"- Duplicates: {sum(1 for item in self.distillations if item.status == 'duplicate')}",
            "",
            "Distillation Lines:",
        ]
        if not self.distillations:
            lines.append("- None")
        else:
            for item in self.distillations:
                lines.append(item.render())
        lines.extend(
            [
                "",
                "Proof Meaning: raw task activity is separated from accepted memory; only traces with reusable future value cross into Candidate Memory.",
            ]
        )
        return "\n".join(lines)


def distill_trace(trace: RawTrace, existing_memories: list[Memory]) -> TraceDistillation:
    request = trace_to_remember_request(trace)
    decision = remember_candidate(existing_memories, request)
    if decision.saved and decision.memory is not None:
        decision.memory.evidence = [
            *decision.memory.evidence,
            f"Distilled from raw trace: {trace.id}",
        ]
    return TraceDistillation(trace=trace, decision=decision)


def trace_to_remember_request(trace: RawTrace) -> RememberRequest:
    return RememberRequest(
        situation=trace.prompt,
        signals=trace.learning_signals,
        outcome=trace.outcome,
        worked=trace.worked,
        failed=trace.failed,
        future_use=trace.future_use,
        evidence=trace.evidence,
        liability_score=liability_from_trace(trace),
        suggested_next_type=MemoryType.SITUATION,
        scope=MemoryScope(
            code=[*trace.files, *([trace.area] if trace.area else [])],
            workflow=trace.workflow,
            environment=trace.environment,
            actor=[trace.actor] if trace.actor else [],
        ),
        confidence=confidence_from_trace(trace),
    )


def apply_distillation(trace_store: RawTraceStore, trace: RawTrace, decision: RememberDecision) -> None:
    trace.status = "candidate-saved" if decision.saved else "rejected"
    trace.distilled_memory_id = decision.memory.id if decision.memory else ""
    trace.distillation_reason = decision.reason
    trace_store.update(trace)


def liability_from_trace(trace: RawTrace) -> int:
    score = {"low": 1, "medium": 3, "high": 4}.get(trace.risk, 3)
    if trace.learning_signals:
        score += 1
    if trace.failed:
        score += 1
    return max(1, min(score, 5))


def confidence_from_trace(trace: RawTrace) -> float:
    confidence = 0.45
    if trace.future_use:
        confidence += 0.15
    if trace.worked or trace.failed:
        confidence += 0.15
    if trace.evidence or trace.outcome:
        confidence += 0.15
    if trace.learning_signals:
        confidence += 0.1
    return min(confidence, 0.9)


def append_trace(data: dict, trace: RawTrace) -> RawTrace:
    data["traces"].append(trace.to_dict())
    return trace


def replace_trace(data: dict, trace: RawTrace) -> RawTrace:
    traces = data["traces"]
    for index, current in enumerate(traces):
        if current["id"] == trace.id:
            traces[index] = trace.to_dict()
            return trace
    raise KeyError(f"Raw trace not found: {trace.id}")


def clean_list(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]
