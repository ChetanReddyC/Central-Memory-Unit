from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .models import MemoryScope, MemoryType
from .onboarding import build_onboarding_seed
from .remembering import RememberRequest, remember_candidate
from .retrieval import PersistentSemanticIndex, PreflightQuery, action_threshold, build_action_note, rank_memories
from .store import MemoryStore
from .triggers import decide_trigger
from .usage import (
    CommitLinkRequest,
    MemoryUseReceipt,
    MemoryUseStore,
    apply_usage_adjustments,
    link_commit,
    link_git_commit,
    use_review,
)


AGENT_API_VERSION = "cmu-agent-tools/v1"


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str
    mutates: bool
    required: list[str]
    optional: list[str] = field(default_factory=list)


AGENT_TOOL_DEFINITIONS = [
    AgentToolDefinition(
        name="cmu_task_start",
        description="Check trigger, onboarding, and grounded memory guidance before meaningful work. Creates a use receipt only when an Action Note surfaces.",
        mutates=True,
        required=["prompt"],
        optional=[
            "actor",
            "area",
            "files",
            "workflow",
            "environment",
            "risk",
            "repeated_error",
            "uncertainty",
            "shared_contract",
            "irreversible",
            "unfamiliar",
            "semantic",
        ],
    ),
    AgentToolDefinition(
        name="cmu_after_work",
        description="Draft and store Candidate Memory only when completed work carries reusable situational intelligence.",
        mutates=True,
        required=["situation", "future_use", "scope"],
        optional=[
            "title",
            "signals",
            "outcome",
            "worked",
            "failed",
            "evidence",
            "liability_score",
            "suggested_next_type",
            "confidence",
        ],
    ),
    AgentToolDefinition(
        name="cmu_link_checkpoint",
        description="Link a surfaced-memory receipt to Git checkpoint evidence read from Git, or to explicit manual metadata when a Git ref is unavailable.",
        mutates=True,
        required=["use_id"],
        optional=["commit_ref", "note", "manual_commit"],
    ),
    AgentToolDefinition(
        name="cmu_review",
        description="Read usefulness and drag review cards without silently changing memory trust, scope, or authority.",
        mutates=False,
        required=[],
        optional=["memory_id"],
    ),
]


class AgentIntegration:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.memory_store = MemoryStore(self.root)
        self.use_store = MemoryUseStore(self.root)

    def manifest(self) -> dict[str, Any]:
        return {
            "api_version": AGENT_API_VERSION,
            "tools": [asdict(tool) for tool in AGENT_TOOL_DEFINITIONS],
        }

    def invoke(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "cmu_task_start": self.task_start,
            "cmu_after_work": self.after_work,
            "cmu_link_checkpoint": self.link_checkpoint,
            "cmu_review": self.review,
        }
        if tool not in handlers:
            return self._response(
                tool,
                ok=False,
                status="unknown-tool",
                error=f"Unknown CMU agent tool: {tool}",
                available_tools=[definition.name for definition in AGENT_TOOL_DEFINITIONS],
            )
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._response(tool, ok=False, status="invalid-request", error="Tool arguments must be a JSON object.")
        try:
            return handlers[tool](arguments)
        except (KeyError, TypeError, ValueError) as error:
            return self._response(tool, ok=False, status="invalid-request", error=str(error))

    def task_start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        prompt = required_text(arguments, "prompt")
        query = preflight_query(arguments, prompt)
        trigger = decide_trigger(
            query,
            repeated_error=bool(arguments.get("repeated_error", False)),
            uncertainty=bool(arguments.get("uncertainty", False)),
            shared_contract=bool(arguments.get("shared_contract", False)),
            irreversible=bool(arguments.get("irreversible", False)),
            unfamiliar=bool(arguments.get("unfamiliar", False)),
        )
        if trigger.level == "silent-skip":
            return self._response(
                "cmu_task_start",
                status="silent-skip",
                trigger=trigger,
                guidance="Proceed without CMU memory; no onboarding seed, Action Note, or receipt is needed.",
                onboarding_seed=None,
                action_note=None,
                receipt=None,
            )

        memories = self.memory_store.list()
        semantic_mode = semantic_mode_from(arguments)
        semantic_index = load_semantic_index(self.root, memories, semantic_mode)
        seed = build_onboarding_seed(memories, query, semantic_index=semantic_index)
        matches = actionable_matches(memories, query, self.use_store, semantic_index)
        if not matches:
            return self._response(
                "cmu_task_start",
                status="quiet",
                trigger=trigger,
                guidance="CMU was called, but no grounded memory crossed the action threshold.",
                onboarding_seed=seed,
                action_note=None,
                receipt=None,
            )

        match = matches[0]
        note = build_action_note(match)
        receipt = MemoryUseReceipt.create(
            match.memory,
            query,
            match,
            source_command="agent.task-start",
            semantic_mode=semantic_mode,
        )
        self.use_store.add(receipt)
        return self._response(
            "cmu_task_start",
            status="action-note",
            trigger=trigger,
            guidance=f"Checked CMU and found {note.recognized_situation}; use the Action Note before changing code.",
            onboarding_seed=seed,
            action_note=note,
            receipt=receipt,
            matched_memory={"id": match.memory.id, "title": match.memory.title, "score": match.score},
        )

    def after_work(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = RememberRequest(
            situation=required_text(arguments, "situation"),
            title=optional_text(arguments, "title"),
            signals=text_list(arguments, "signals"),
            outcome=optional_text(arguments, "outcome"),
            worked=optional_text(arguments, "worked"),
            failed=optional_text(arguments, "failed"),
            future_use=required_text(arguments, "future_use"),
            evidence=text_list(arguments, "evidence"),
            liability_score=int(arguments.get("liability_score", 1)),
            suggested_next_type=MemoryType(arguments.get("suggested_next_type", MemoryType.SITUATION.value)),
            scope=memory_scope(arguments.get("scope")),
            confidence=float(arguments.get("confidence", 0.6)),
        )
        decision = remember_candidate(self.memory_store.list(), request)
        if decision.saved and decision.memory is not None:
            self.memory_store.add(decision.memory)
        return self._response(
            "cmu_after_work",
            ok=decision.saved,
            status="candidate-saved" if decision.saved else "candidate-not-saved",
            decision=decision,
        )

    def link_checkpoint(self, arguments: dict[str, Any]) -> dict[str, Any]:
        use_id = required_text(arguments, "use_id")
        receipt = self.use_store.get(use_id)
        manual_commit = arguments.get("manual_commit")
        if manual_commit is not None:
            if not isinstance(manual_commit, dict):
                raise ValueError("manual_commit must be a JSON object")
            decision = link_commit(
                receipt,
                CommitLinkRequest(
                    use_id=use_id,
                    commit_hash=required_text(manual_commit, "hash"),
                    message=optional_text(manual_commit, "message"),
                    files=text_list(manual_commit, "files"),
                    commit_time=optional_text(manual_commit, "time"),
                    metadata_source="agent-manual",
                    note=optional_text(arguments, "note"),
                ),
            )
        else:
            decision = link_git_commit(
                receipt,
                root=self.root,
                ref=optional_text(arguments, "commit_ref") or "HEAD",
                note=optional_text(arguments, "note"),
            )
        if decision.linked and decision.receipt is not None:
            self.use_store.update(decision.receipt)
        return self._response(
            "cmu_link_checkpoint",
            ok=decision.linked,
            status="checkpoint-linked" if decision.linked else "checkpoint-not-linked",
            decision=decision,
        )

    def review(self, arguments: dict[str, Any]) -> dict[str, Any]:
        memory_id = optional_text(arguments, "memory_id")
        report = use_review(self.use_store.list(), self.memory_store.list(), memory_id)
        return self._response(
            "cmu_review",
            status="review-ready" if report.cards else "no-review-evidence",
            memory_id=memory_id,
            cards=report.cards,
            guidance="Review cards are read-only. Stable memory trust, scope, and authority must not change without an explicit follow-up path.",
        )

    def _response(self, tool: str, *, ok: bool = True, status: str, **payload: Any) -> dict[str, Any]:
        return to_json_value(
            {
                "api_version": AGENT_API_VERSION,
                "tool": tool,
                "ok": ok,
                "status": status,
                **payload,
            }
        )


def preflight_query(arguments: dict[str, Any], prompt: str) -> PreflightQuery:
    risk = optional_text(arguments, "risk") or "medium"
    if risk not in {"low", "medium", "high"}:
        raise ValueError("risk must be one of: low, medium, high")
    return PreflightQuery(
        prompt=prompt,
        actor=optional_text(arguments, "actor") or "agent",
        area=optional_text(arguments, "area"),
        files=text_list(arguments, "files"),
        workflow=text_list(arguments, "workflow"),
        environment=text_list(arguments, "environment"),
        risk=risk,
    )


def memory_scope(value: Any) -> MemoryScope:
    if not isinstance(value, dict):
        raise ValueError("scope must be a JSON object")
    return MemoryScope(
        ownership=text_list(value, "ownership"),
        code=text_list(value, "code"),
        workflow=text_list(value, "workflow"),
        environment=text_list(value, "environment"),
        actor=text_list(value, "actor"),
        time=text_list(value, "time"),
    )


def semantic_mode_from(arguments: dict[str, Any]) -> str:
    mode = optional_text(arguments, "semantic") or "off"
    if mode not in {"off", "local"}:
        raise ValueError("semantic must be one of: off, local")
    return mode


def load_semantic_index(root: Path, memories, semantic_mode: str):
    if semantic_mode == "local":
        return PersistentSemanticIndex.load_or_build(root / ".cmu" / "semantic_index.json", memories)
    return None


def actionable_matches(memories, query: PreflightQuery, use_store: MemoryUseStore, semantic_index):
    matches = rank_memories(memories, query, semantic_index=semantic_index)
    grounded = [match for match in matches if match.score >= action_threshold(query.risk)]
    return apply_usage_adjustments(grounded, use_store.list())


def required_text(arguments: dict[str, Any], key: str) -> str:
    value = optional_text(arguments, key)
    if not value:
        raise ValueError(f"{key} is required")
    return value


def optional_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def text_list(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return to_json_value(asdict(value))
    if isinstance(value, dict):
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value
