from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .agent_api import AgentIntegration
from .antipatterns import anti_pattern_report
from .analytics import usefulness_analytics_report
from .authority import authority_report, set_memory_authority
from .challenges import ChallengeRequest, ResolveChallengeRequest, challenge_stable_memory, resolve_challenge
from .governance import governance_report
from .graphview import graph_memory_view_report
from .gravity import gravity_report
from .lifecycle import lifecycle_report
from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from .onboarding import build_onboarding_seed
from .pipeline import hybrid_pipeline_report
from .portable import export_bundle_from_root, import_portable_bundle, load_portable_bundle
from .promotion import promote_memory, review_promotion
from .questions import ResolveQuestionRequest, question_report, resolve_question
from .quality import apply_decay_action, quality_report
from .quickstart import quickstart_demo
from .remembering import RememberRequest, remember_candidate
from .retrieval import (
    PersistentSemanticIndex,
    PreflightQuery,
    action_threshold,
    build_action_note,
    rank_memories,
    semantic_index_status,
    semantic_proposal_diagnostics,
)
from .scenarios import (
    ScenarioDefinition,
    ScenarioEvaluationRequest,
    ScenarioLibraryStore,
    evaluate_scenario,
    run_scenario_library,
)
from .store import MemoryStore
from .traces import RawTrace, RawTraceStore, TraceDistillationReport, apply_distillation, distill_trace
from .triggers import decide_trigger
from .usage import (
    CommitLinkRequest,
    DEFAULT_AUTO_LINK_MIN_SCORE,
    MemoryUseReceipt,
    MemoryUseStore,
    apply_usage_adjustments,
    auto_link_receipts,
    link_commit,
    link_git_commit,
    prepare_use_review_followup,
    resolve_receipt_without_commit,
    semantic_audit,
    semantic_audit_recommendations,
    use_review,
    use_summary,
    use_threshold_report,
)
from .workcycle import WorkCycleRequest, work_cycle_report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = MemoryStore(args.root)
    return args.func(args, store)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cmu", description="Central Memory Unit local v0 spine.")
    parser.add_argument("--root", default=".", help="Project root containing the .cmu store.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create the local CMU store.")
    init_parser.set_defaults(func=cmd_init)

    quickstart_parser = subparsers.add_parser("quickstart-demo", help="Run a small Git-backed CMU proof loop.")
    quickstart_parser.add_argument("--apply", action="store_true", help="Create the demo memory, receipt, commit, and link evidence.")
    quickstart_parser.set_defaults(func=cmd_quickstart_demo)

    agent_tools_parser = subparsers.add_parser("agent-tools", help="Show the stable direct agent tool-call manifest as JSON.")
    agent_tools_parser.set_defaults(func=cmd_agent_tools)

    agent_call_parser = subparsers.add_parser("agent-call", help="Invoke one stable direct agent tool with JSON arguments.")
    agent_call_parser.add_argument("tool", help="Agent tool name from cmu agent-tools.")
    agent_call_parser.add_argument("--input", default="", help="Inline JSON object containing the tool arguments.")
    agent_call_parser.add_argument("--input-file", default="", help="Read JSON arguments from a file, or '-' for stdin.")
    agent_call_parser.set_defaults(func=cmd_agent_call)

    portable_export_parser = subparsers.add_parser(
        "portable-export",
        help="Export memories and evidence receipts as a versioned portable JSON bundle.",
    )
    portable_export_parser.add_argument("--output", default="-", help="Write bundle to a file, or '-' for stdout.")
    portable_export_parser.add_argument("--include-retired", action="store_true", help="Include retired memory history.")
    portable_export_parser.add_argument("--memory", default="", help="Export one memory and its receipts only.")
    portable_export_parser.add_argument("--no-uses", action="store_true", help="Exclude use receipts from the bundle.")
    portable_export_parser.set_defaults(func=cmd_portable_export)

    portable_import_parser = subparsers.add_parser(
        "portable-import",
        help="Preview or apply a versioned portable CMU bundle.",
    )
    portable_import_parser.add_argument("bundle", help="Portable bundle JSON file.")
    portable_import_parser.add_argument("--apply", action="store_true", help="Write the import plan. Default is dry-run.")
    portable_import_parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update existing records with matching ids instead of treating differences as conflicts.",
    )
    portable_import_parser.set_defaults(func=cmd_portable_import)

    add_parser = subparsers.add_parser("add", help="Add a structured CMU memory.")
    add_parser.add_argument("--type", choices=[item.value for item in MemoryType], default=MemoryType.SITUATION.value)
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--summary", required=True)
    add_parser.add_argument("--signal", action="append", default=[])
    add_parser.add_argument("--evidence", action="append", default=[])
    add_parser.add_argument("--scope-owner", action="append", default=[])
    add_parser.add_argument("--scope-code", action="append", default=[])
    add_parser.add_argument("--scope-workflow", action="append", default=[])
    add_parser.add_argument("--scope-env", action="append", default=[])
    add_parser.add_argument("--scope-actor", action="append", default=[])
    add_parser.add_argument("--scope-time", action="append", default=[])
    add_parser.add_argument("--use-path", default="")
    add_parser.add_argument("--avoid", default="")
    add_parser.add_argument("--challenge", default="")
    add_parser.add_argument("--liability", type=int, default=1)
    add_parser.add_argument("--confidence", type=float, default=0.6)
    add_parser.add_argument("--approved-by", default="", help="Owner or team approving a stable Practice/Anchor memory.")
    add_parser.add_argument("--authority-owner", default="", help="Accountable person or team for consequence-based authority.")
    add_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], default="")
    add_parser.add_argument("--consequence", choices=["low", "medium", "high", "critical"], default="")
    add_parser.add_argument("--review-due", default="", help="Optional ISO-8601 authority review expiry.")
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="List stored CMU memories.")
    list_parser.add_argument("--type", choices=[item.value for item in MemoryType])
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.set_defaults(func=cmd_list)

    relate_parser = subparsers.add_parser("relate", help="Create a graph relationship between two memories.")
    relate_parser.add_argument("source_id", help="Memory id that carries the relationship.")
    relate_parser.add_argument("--type", choices=[item.value for item in MemoryRelationType], required=True)
    relate_parser.add_argument("--target", required=True, help="Memory id the source relates to.")
    relate_parser.add_argument("--reason", default="", help="Why this relationship should guide retrieval.")
    relate_parser.set_defaults(func=cmd_relate)

    relations_parser = subparsers.add_parser("relations", help="Inspect graph relationships for one memory.")
    relations_parser.add_argument("memory_id", help="Memory id to inspect.")
    relations_parser.set_defaults(func=cmd_relations)

    graph_parser = subparsers.add_parser("graph", help="Show the read-only Graph Memory View.")
    graph_parser.add_argument("memory_id", nargs="?", default="", help="Optional root memory id for a focused path.")
    graph_parser.add_argument("--depth", type=int, default=3, help="Maximum relationship depth for a focused path.")
    graph_parser.add_argument("--include-retired", action="store_true", help="Include retired memory history in the graph.")
    graph_parser.set_defaults(func=cmd_graph)

    preflight_parser = subparsers.add_parser("preflight", help="Run a task-start CMU preflight.")
    preflight_parser.add_argument("prompt", nargs="*", help="Task prompt to check against memory.")
    preflight_parser.add_argument("--actor", default="developer")
    preflight_parser.add_argument("--area", default="")
    preflight_parser.add_argument("--file", action="append", default=[])
    preflight_parser.add_argument("--workflow", action="append", default=[])
    preflight_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    preflight_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    preflight_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    preflight_parser.add_argument("--show-matches", action="store_true")
    preflight_parser.add_argument(
        "--show-semantic-proposals",
        action="store_true",
        help="Show diagnostic-only semantic proposal decisions without changing retrieval or receipts.",
    )
    preflight_parser.set_defaults(func=cmd_preflight)

    pipeline_parser = subparsers.add_parser("retrieval-pipeline", help="Show the read-only full Hybrid Retrieval Pipeline.")
    pipeline_parser.add_argument("prompt", nargs="*", help="Task prompt to inspect against memory.")
    pipeline_parser.add_argument("--actor", default="developer")
    pipeline_parser.add_argument("--area", default="")
    pipeline_parser.add_argument("--file", action="append", default=[])
    pipeline_parser.add_argument("--workflow", action="append", default=[])
    pipeline_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    pipeline_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    pipeline_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    pipeline_parser.set_defaults(func=cmd_retrieval_pipeline)

    onboard_parser = subparsers.add_parser("onboard", help="Generate a tiny task-bound CMU Onboarding Seed.")
    onboard_parser.add_argument("prompt", nargs="*", help="Task prompt to onboard against.")
    onboard_parser.add_argument("--actor", default="developer")
    onboard_parser.add_argument("--area", default="")
    onboard_parser.add_argument("--file", action="append", default=[])
    onboard_parser.add_argument("--workflow", action="append", default=[])
    onboard_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    onboard_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    onboard_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    onboard_parser.add_argument(
        "--show-semantic-proposals",
        action="store_true",
        help="Show diagnostic-only semantic proposal decisions without creating receipts.",
    )
    onboard_parser.set_defaults(func=cmd_onboard)

    semantic_status_parser = subparsers.add_parser(
        "semantic-status",
        help="Inspect the local semantic index without refreshing or changing retrieval behavior.",
    )
    semantic_status_parser.set_defaults(func=cmd_semantic_status)

    trigger_parser = subparsers.add_parser("trigger", help="Decide whether the task should call CMU memory.")
    trigger_parser.add_argument("prompt", nargs="*", help="Task prompt to evaluate.")
    trigger_parser.add_argument("--actor", default="developer")
    trigger_parser.add_argument("--area", default="")
    trigger_parser.add_argument("--file", action="append", default=[])
    trigger_parser.add_argument("--workflow", action="append", default=[])
    trigger_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    trigger_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    trigger_parser.add_argument("--repeated-error", action="store_true")
    trigger_parser.add_argument("--uncertainty", action="store_true")
    trigger_parser.add_argument("--shared-contract", action="store_true")
    trigger_parser.add_argument("--irreversible", action="store_true")
    trigger_parser.add_argument("--unfamiliar", action="store_true")
    trigger_parser.set_defaults(func=cmd_trigger)

    start_parser = subparsers.add_parser("start", help="Run the task-start CMU Work Cycle entrypoint.")
    start_parser.add_argument("prompt", nargs="*", help="Task prompt to start against.")
    start_parser.add_argument("--actor", default="developer")
    start_parser.add_argument("--area", default="")
    start_parser.add_argument("--file", action="append", default=[])
    start_parser.add_argument("--workflow", action="append", default=[])
    start_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    start_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    start_parser.add_argument("--repeated-error", action="store_true")
    start_parser.add_argument("--uncertainty", action="store_true")
    start_parser.add_argument("--shared-contract", action="store_true")
    start_parser.add_argument("--irreversible", action="store_true")
    start_parser.add_argument("--unfamiliar", action="store_true")
    start_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    start_parser.add_argument("--show-matches", action="store_true")
    start_parser.add_argument(
        "--show-semantic-proposals",
        action="store_true",
        help="Show diagnostic-only semantic proposal decisions without changing retrieval or receipts.",
    )
    start_parser.set_defaults(func=cmd_start)

    work_cycle_parser = subparsers.add_parser("work-cycle", help="Run the read-only full CMU Work Cycle integration proof.")
    work_cycle_parser.add_argument("prompt", nargs="*", help="Task prompt to run through the full cycle.")
    work_cycle_parser.add_argument("--actor", default="developer")
    work_cycle_parser.add_argument("--area", default="")
    work_cycle_parser.add_argument("--file", action="append", default=[])
    work_cycle_parser.add_argument("--workflow", action="append", default=[])
    work_cycle_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    work_cycle_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    work_cycle_parser.add_argument("--repeated-error", action="store_true")
    work_cycle_parser.add_argument("--uncertainty", action="store_true")
    work_cycle_parser.add_argument("--shared-contract", action="store_true")
    work_cycle_parser.add_argument("--irreversible", action="store_true")
    work_cycle_parser.add_argument("--unfamiliar", action="store_true")
    work_cycle_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    work_cycle_parser.add_argument("--learning-signal", action="append", default=[])
    work_cycle_parser.add_argument("--outcome", default="")
    work_cycle_parser.add_argument("--worked", default="")
    work_cycle_parser.add_argument("--failed", default="")
    work_cycle_parser.add_argument("--future-use", default="")
    work_cycle_parser.add_argument("--evidence", action="append", default=[])
    work_cycle_parser.set_defaults(func=cmd_work_cycle)

    evaluate_parser = subparsers.add_parser("evaluate-scenario", help="Run a read-only CMU structural scenario evaluation.")
    evaluate_parser.add_argument("prompt", nargs="*", help="Task scenario prompt to evaluate.")
    evaluate_parser.add_argument("--actor", default="developer")
    evaluate_parser.add_argument("--area", default="")
    evaluate_parser.add_argument("--file", action="append", default=[])
    evaluate_parser.add_argument("--workflow", action="append", default=[])
    evaluate_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    evaluate_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    evaluate_parser.add_argument("--repeated-error", action="store_true")
    evaluate_parser.add_argument("--uncertainty", action="store_true")
    evaluate_parser.add_argument("--shared-contract", action="store_true")
    evaluate_parser.add_argument("--irreversible", action="store_true")
    evaluate_parser.add_argument("--unfamiliar", action="store_true")
    evaluate_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    evaluate_parser.add_argument("--expect-trigger", choices=["must-call", "should-call", "silent-skip"], default="")
    evaluate_parser.add_argument("--expect-action", choices=["action-note", "quiet"], default="")
    evaluate_parser.add_argument("--expect-memory", default="", help="Expected surfaced memory id, or 'none'.")
    evaluate_parser.add_argument("--expect-candidate", choices=["draft-recommended", "not-recommended"], default="")
    evaluate_parser.add_argument("--learning-signal", action="append", default=[], help="Reusable learning signal observed in the scenario.")
    evaluate_parser.add_argument("--worked", default="", help="What worked in the scenario, if reusable.")
    evaluate_parser.add_argument("--failed", default="", help="What failed in the scenario, if reusable.")
    evaluate_parser.add_argument("--future-use", default="", help="Why future work should reuse this learning.")
    evaluate_parser.add_argument("--evidence", action="append", default=[], help="Evidence observed in the scenario.")
    evaluate_parser.set_defaults(func=cmd_evaluate_scenario)

    scenario_add_parser = subparsers.add_parser("scenario-add", help="Save a repeatable read-only scenario evaluation.")
    scenario_add_parser.add_argument("prompt", nargs="*", help="Task scenario prompt to save.")
    scenario_add_parser.add_argument("--name", required=True, help="Short scenario name.")
    scenario_add_parser.add_argument("--description", default="", help="Why this scenario belongs in the library.")
    scenario_add_parser.add_argument("--tag", action="append", default=[], help="Scenario grouping tag.")
    scenario_add_parser.add_argument("--actor", default="developer")
    scenario_add_parser.add_argument("--area", default="")
    scenario_add_parser.add_argument("--file", action="append", default=[])
    scenario_add_parser.add_argument("--workflow", action="append", default=[])
    scenario_add_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    scenario_add_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    scenario_add_parser.add_argument("--repeated-error", action="store_true")
    scenario_add_parser.add_argument("--uncertainty", action="store_true")
    scenario_add_parser.add_argument("--shared-contract", action="store_true")
    scenario_add_parser.add_argument("--irreversible", action="store_true")
    scenario_add_parser.add_argument("--unfamiliar", action="store_true")
    scenario_add_parser.add_argument("--expect-trigger", choices=["must-call", "should-call", "silent-skip"], default="")
    scenario_add_parser.add_argument("--expect-action", choices=["action-note", "quiet"], default="")
    scenario_add_parser.add_argument("--expect-memory", default="", help="Expected surfaced memory id, or 'none'.")
    scenario_add_parser.add_argument("--expect-candidate", choices=["draft-recommended", "not-recommended"], default="")
    scenario_add_parser.add_argument("--learning-signal", action="append", default=[], help="Reusable learning signal expected in the scenario.")
    scenario_add_parser.add_argument("--worked", default="", help="What worked in the scenario, if reusable.")
    scenario_add_parser.add_argument("--failed", default="", help="What failed in the scenario, if reusable.")
    scenario_add_parser.add_argument("--future-use", default="", help="Why future work should reuse this learning.")
    scenario_add_parser.add_argument("--evidence", action="append", default=[], help="Evidence expected in the scenario.")
    scenario_add_parser.set_defaults(func=cmd_scenario_add)

    scenario_list_parser = subparsers.add_parser("scenario-list", help="List saved scenario-library cases.")
    scenario_list_parser.add_argument("--tag", default="", help="Only list scenarios with this tag.")
    scenario_list_parser.add_argument("--limit", type=int, default=50)
    scenario_list_parser.set_defaults(func=cmd_scenario_list)

    scenario_run_parser = subparsers.add_parser("scenario-run", help="Run saved scenario-library cases.")
    scenario_run_parser.add_argument("scenario", nargs="?", default="", help="Scenario id or exact name. Omit to run the library.")
    scenario_run_parser.add_argument("--tag", default="", help="Run scenarios with this tag.")
    scenario_run_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    scenario_run_parser.add_argument("--strict", action="store_true", help="Exit non-zero when any scenario needs review.")
    scenario_run_parser.set_defaults(func=cmd_scenario_run)

    trace_add_parser = subparsers.add_parser("trace-add", help="Capture raw task activity for later Candidate Memory distillation.")
    trace_add_parser.add_argument("prompt", nargs="*", help="Raw task/activity prompt to capture.")
    trace_add_parser.add_argument("--actor", default="developer")
    trace_add_parser.add_argument("--area", default="")
    trace_add_parser.add_argument("--file", action="append", default=[])
    trace_add_parser.add_argument("--workflow", action="append", default=[])
    trace_add_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    trace_add_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    trace_add_parser.add_argument("--learning-signal", action="append", default=[], help="Reusable learning signal observed in the trace.")
    trace_add_parser.add_argument("--outcome", default="", help="Observed outcome from the work.")
    trace_add_parser.add_argument("--worked", default="", help="What worked, if reusable.")
    trace_add_parser.add_argument("--failed", default="", help="What failed, if reusable.")
    trace_add_parser.add_argument("--future-use", default="", help="Why future work should reuse this trace.")
    trace_add_parser.add_argument("--evidence", action="append", default=[], help="Evidence observed in the raw trace.")
    trace_add_parser.set_defaults(func=cmd_trace_add)

    trace_distill_parser = subparsers.add_parser("trace-distill", help="Distill raw traces into Candidate Memory drafts when quality gates pass.")
    trace_distill_parser.add_argument("trace_id", nargs="?", help="Raw trace id to distill. Omit to review pending raw traces.")
    trace_distill_parser.add_argument("--apply", action="store_true", help="Persist Candidate Memories and trace distillation status.")
    trace_distill_parser.add_argument("--include-distilled", action="store_true", help="Include traces that were already distilled or rejected.")
    trace_distill_parser.set_defaults(func=cmd_trace_distill)

    remember_parser = subparsers.add_parser("remember", help="Store a direct agent-submitted Candidate Memory.")
    remember_parser.add_argument("--situation", required=True)
    remember_parser.add_argument("--title", default="")
    remember_parser.add_argument("--signal", action="append", default=[])
    remember_parser.add_argument("--outcome", default="")
    remember_parser.add_argument("--worked", default="")
    remember_parser.add_argument("--failed", default="")
    remember_parser.add_argument("--future-use", required=True)
    remember_parser.add_argument("--evidence", action="append", default=[])
    remember_parser.add_argument("--liability", type=int, default=1)
    remember_parser.add_argument("--suggested-next-type", choices=[item.value for item in MemoryType], default=MemoryType.SITUATION.value)
    remember_parser.add_argument("--scope-owner", action="append", default=[])
    remember_parser.add_argument("--scope-code", action="append", default=[])
    remember_parser.add_argument("--scope-workflow", action="append", default=[])
    remember_parser.add_argument("--scope-env", action="append", default=[])
    remember_parser.add_argument("--scope-actor", action="append", default=[])
    remember_parser.add_argument("--scope-time", action="append", default=[])
    remember_parser.add_argument("--confidence", type=float, default=0.6)
    remember_parser.set_defaults(func=cmd_remember)

    review_parser = subparsers.add_parser("review", help="Review memory for promotion.")
    review_parser.add_argument("memory_id", nargs="?", help="Memory id to review. Omit to list candidates.")
    review_parser.add_argument(
        "--to",
        choices=[MemoryType.SITUATION.value, MemoryType.ANCHOR.value, MemoryType.PRACTICE.value],
        default=MemoryType.SITUATION.value,
    )
    review_parser.set_defaults(func=cmd_review)

    lifecycle_parser = subparsers.add_parser("lifecycle", help="Show the read-only Core Memory Lifecycle structural view.")
    lifecycle_parser.add_argument("--memory", default="", help="Limit lifecycle view to one memory id.")
    lifecycle_parser.set_defaults(func=cmd_lifecycle)

    gravity_parser = subparsers.add_parser("gravity", help="Show the read-only Memory Gravity placement/settling view.")
    gravity_parser.add_argument("--memory", default="", help="Limit gravity view to one memory id.")
    gravity_parser.set_defaults(func=cmd_gravity)

    governance_parser = subparsers.add_parser("governance", help="Show the read-only Practice/Anchor governance view.")
    governance_parser.add_argument("--memory", default="", help="Limit governance view to one stable memory id.")
    governance_parser.set_defaults(func=cmd_governance)

    authority_parser = subparsers.add_parser("authority", help="Show the read-only Team and Authority Model.")
    authority_parser.add_argument("--memory", default="", help="Limit authority view to one memory id.")
    authority_parser.add_argument("--all", action="store_true", help="Include non-stable memories in the authority view.")
    authority_parser.set_defaults(func=cmd_authority)

    authority_set_parser = subparsers.add_parser("authority-set", help="Apply explicit consequence-based authority metadata.")
    authority_set_parser.add_argument("memory_id", help="Memory id to assign authority metadata.")
    authority_set_parser.add_argument("--owner", required=True, help="Accountable person or team.")
    authority_set_parser.add_argument("--approved-by", required=True, help="Person or team approving this memory.")
    authority_set_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], required=True)
    authority_set_parser.add_argument("--consequence", choices=["low", "medium", "high", "critical"], required=True)
    authority_set_parser.add_argument("--review-due", default="", help="Optional ISO-8601 authority review expiry.")
    authority_set_parser.set_defaults(func=cmd_authority_set)

    quality_parser = subparsers.add_parser("quality", help="Show the read-only Memory Quality and Decay Model.")
    quality_parser.add_argument("--memory", default="", help="Limit quality view to one memory id.")
    quality_parser.add_argument("--include-retired", action="store_true", help="Include retired memory history.")
    quality_parser.set_defaults(func=cmd_quality)

    decay_parser = subparsers.add_parser("decay-apply", help="Apply an explicit evidence-backed memory decay action.")
    decay_parser.add_argument("memory_id", help="Memory id to weaken, demote, or retire.")
    decay_parser.add_argument("--action", choices=["weaken", "demote", "retire"], required=True)
    decay_parser.add_argument("--reason", required=True, help="Evidence-backed reason for the decay action.")
    decay_parser.add_argument("--approved-by", default="", help="Required for stable-memory decay actions.")
    decay_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], default="")
    decay_parser.set_defaults(func=cmd_decay_apply)

    analytics_parser = subparsers.add_parser("analytics", help="Show the read-only Usefulness and Drag Analytics view.")
    analytics_parser.add_argument("--memory", default="", help="Limit analytics view to one memory id.")
    analytics_parser.set_defaults(func=cmd_analytics)

    anti_pattern_parser = subparsers.add_parser("anti-pattern", help="Show the read-only Anti-Pattern workflow view.")
    anti_pattern_parser.add_argument("prompt", nargs="*", help="Optional task prompt to test against anti-pattern warnings.")
    anti_pattern_parser.add_argument("--memory", default="", help="Limit anti-pattern view to one memory id.")
    anti_pattern_parser.add_argument("--actor", default="developer")
    anti_pattern_parser.add_argument("--area", default="")
    anti_pattern_parser.add_argument("--file", action="append", default=[])
    anti_pattern_parser.add_argument("--workflow", action="append", default=[])
    anti_pattern_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    anti_pattern_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    anti_pattern_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    anti_pattern_parser.set_defaults(func=cmd_anti_pattern)

    question_parser = subparsers.add_parser("question", help="Show the read-only Question workflow view.")
    question_parser.add_argument("prompt", nargs="*", help="Optional task prompt to test against unresolved questions.")
    question_parser.add_argument("--memory", default="", help="Limit question view to one memory id.")
    question_parser.add_argument("--include-retired", action="store_true", help="Include answered/retired Question memories.")
    question_parser.add_argument("--actor", default="developer")
    question_parser.add_argument("--area", default="")
    question_parser.add_argument("--file", action="append", default=[])
    question_parser.add_argument("--workflow", action="append", default=[])
    question_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    question_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    question_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider. Defaults to off.",
    )
    question_parser.set_defaults(func=cmd_question)

    resolve_question_parser = subparsers.add_parser("resolve-question", help="Answer and retire a Question memory.")
    resolve_question_parser.add_argument("question_id", help="Active Question memory id to resolve.")
    resolve_question_parser.add_argument("--outcome", choices=["retire", "situation", "exception"], required=True)
    resolve_question_parser.add_argument("--answer", required=True, help="Evidence-backed answer to the unresolved question.")
    resolve_question_parser.add_argument("--resolved-by", required=True, help="Owner or team resolving the question.")
    resolve_question_parser.add_argument("--evidence", action="append", default=[], required=True)
    resolve_question_parser.add_argument("--title", default="", help="Optional title for a Situation/Exception outcome memory.")
    resolve_question_parser.add_argument("--use-path", default="", help="Optional future-use path for the outcome memory.")
    resolve_question_parser.add_argument("--avoid", default="", help="Optional warning for the outcome memory.")
    resolve_question_parser.add_argument("--review-if", default="", help="Optional review condition for the outcome memory.")
    resolve_question_parser.set_defaults(func=cmd_resolve_question)

    promote_parser = subparsers.add_parser("promote", help="Promote memory after gates pass.")
    promote_parser.add_argument("memory_id", help="Memory id to promote.")
    promote_parser.add_argument(
        "--to",
        choices=[MemoryType.SITUATION.value, MemoryType.ANCHOR.value, MemoryType.PRACTICE.value],
        default=MemoryType.SITUATION.value,
    )
    promote_parser.add_argument("--approved-by", default="", help="Owner or team approving stable Practice/Anchor promotion.")
    promote_parser.add_argument("--authority-owner", default="", help="Accountable person or team for stable-memory authority.")
    promote_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], default="")
    promote_parser.add_argument("--consequence", choices=["low", "medium", "high", "critical"], default="")
    promote_parser.add_argument("--review-due", default="", help="Optional ISO-8601 authority review expiry.")
    promote_parser.set_defaults(func=cmd_promote)

    challenge_parser = subparsers.add_parser("challenge", help="Record a deliberate challenge to Practice or Anchor memory.")
    challenge_parser.add_argument("memory_id", help="Practice or Anchor memory id to challenge.")
    challenge_parser.add_argument("--mismatch", required=True, help="What no longer fits or appears wrong.")
    challenge_parser.add_argument("--benefit", required=True, help="Expected benefit of changing or excepting the stable memory.")
    challenge_parser.add_argument("--risk", required=True, help="Risk introduced by the challenge or proposed change.")
    challenge_parser.add_argument("--rollback", required=True, help="Rollback path if the challenge outcome is wrong.")
    challenge_parser.add_argument("--challenged-by", default="", help="Person, team, or agent raising the challenge.")
    challenge_parser.add_argument("--evidence", action="append", default=[])
    challenge_parser.add_argument("--confidence", type=float, default=0.6)
    challenge_parser.set_defaults(func=cmd_challenge)

    resolve_parser = subparsers.add_parser("resolve-challenge", help="Apply an approved stable-memory challenge outcome.")
    resolve_parser.add_argument("challenge_id", help="Challenge Candidate memory id to resolve.")
    resolve_parser.add_argument(
        "--outcome",
        choices=["exception", "strengthen", "update", "retire", "split"],
        required=True,
    )
    resolve_parser.add_argument("--approved-by", required=True, help="Owner or team approving the challenge outcome.")
    resolve_parser.add_argument("--replacement-title", default="", help="Optional new title for an approved update.")
    resolve_parser.add_argument("--replacement-summary", default="", help="Replacement stable-memory summary for update.")
    resolve_parser.add_argument("--replacement-use-path", default="", help="Replacement default path for update.")
    resolve_parser.add_argument("--replacement-avoid", default="", help="Replacement warning for update.")
    resolve_parser.add_argument("--replacement-challenge", default="", help="Replacement challenge condition for update.")
    resolve_parser.add_argument("--retirement-reason", default="", help="Approved reason for retiring stable memory.")
    resolve_parser.add_argument("--split-title", default="", help="Title for a split-off stable memory.")
    resolve_parser.add_argument("--split-summary", default="", help="Summary for a split-off stable memory.")
    resolve_parser.add_argument("--split-use-path", default="", help="Default path for a split-off stable memory.")
    resolve_parser.add_argument("--split-avoid", default="", help="Warning for a split-off stable memory.")
    resolve_parser.add_argument("--split-challenge", default="", help="Challenge condition for a split-off stable memory.")
    resolve_parser.add_argument("--scope-owner", action="append", default=[])
    resolve_parser.add_argument("--scope-code", action="append", default=[])
    resolve_parser.add_argument("--scope-workflow", action="append", default=[])
    resolve_parser.add_argument("--scope-env", action="append", default=[])
    resolve_parser.add_argument("--scope-actor", action="append", default=[])
    resolve_parser.add_argument("--scope-time", action="append", default=[])
    resolve_parser.add_argument("--evidence", action="append", default=[], help="Resolution evidence for mutating outcomes.")
    resolve_parser.set_defaults(func=cmd_resolve_challenge)

    use_link_parser = subparsers.add_parser("use-link", help="Link a surfaced memory use receipt to a Git commit.")
    use_link_parser.add_argument("use_id", help="Memory use receipt id from preflight.")
    use_link_parser.add_argument("--commit", required=True, help="Git commit hash that accepted or checkpointed the work.")
    use_link_parser.add_argument("--message", default="", help="Optional commit message override.")
    use_link_parser.add_argument("--file", action="append", default=[], help="Optional changed-file override.")
    use_link_parser.add_argument("--manual", action="store_true", help="Use supplied message/files instead of inspecting Git.")
    use_link_parser.add_argument("--note", default="", help="Short evidence note for the link.")
    use_link_parser.set_defaults(func=cmd_use_link)

    use_link_latest_parser = subparsers.add_parser("use-link-latest", help="Link a memory use receipt to the latest Git commit.")
    use_link_latest_parser.add_argument("use_id", help="Memory use receipt id from preflight.")
    use_link_latest_parser.add_argument("--ref", default="HEAD", help="Git ref to inspect. Defaults to HEAD.")
    use_link_latest_parser.add_argument("--message", default="", help="Optional commit message override.")
    use_link_latest_parser.add_argument("--file", action="append", default=[], help="Optional changed-file override.")
    use_link_latest_parser.add_argument("--note", default="", help="Short evidence note for the link.")
    use_link_latest_parser.set_defaults(func=cmd_use_link_latest)

    use_link_auto_parser = subparsers.add_parser("use-link-auto", help="Match unlinked memory use receipts to recent Git commits.")
    use_link_auto_parser.add_argument("--limit", type=int, default=20, help="Number of recent commits to inspect.")
    use_link_auto_parser.add_argument("--hours", type=int, default=72, help="Maximum hours after a receipt to consider a commit.")
    use_link_auto_parser.add_argument("--min-score", type=float, default=DEFAULT_AUTO_LINK_MIN_SCORE, help="Minimum auto-match score required.")
    use_link_auto_parser.add_argument("--apply", action="store_true", help="Persist confident auto-links. Defaults to dry-run.")
    use_link_auto_parser.set_defaults(func=cmd_use_link_auto)

    use_resolve_parser = subparsers.add_parser("use-resolve", help="Resolve a memory use receipt without linking a Git commit.")
    use_resolve_parser.add_argument("use_id", help="Memory use receipt id to resolve.")
    use_resolve_parser.add_argument(
        "--outcome",
        choices=["no-checkpoint", "not-applicable", "superseded"],
        required=True,
        help="Why this receipt should not wait for a Git commit link.",
    )
    use_resolve_parser.add_argument("--note", required=True, help="Short explanation for the no-commit resolution.")
    use_resolve_parser.add_argument("--resolved-by", default="", help="Person, team, or agent applying the resolution.")
    use_resolve_parser.set_defaults(func=cmd_use_resolve)

    use_list_parser = subparsers.add_parser("use-list", help="List memory use receipts.")
    use_list_parser.add_argument("--limit", type=int, default=20)
    use_list_parser.set_defaults(func=cmd_use_list)

    use_summary_parser = subparsers.add_parser("use-summary", help="Summarize use receipts for one memory.")
    use_summary_parser.add_argument("memory_id", help="Memory id to summarize.")
    use_summary_parser.set_defaults(func=cmd_use_summary)

    use_review_parser = subparsers.add_parser("use-review", help="Review usefulness and drag signals from memory use receipts.")
    use_review_parser.add_argument("memory_id", nargs="?", help="Optional memory id to review.")
    use_review_parser.add_argument("--thresholds", action="store_true", help="Show diagnostic threshold behavior across real use receipts.")
    use_review_parser.add_argument("--prepare", choices=["strengthen", "challenge", "scope-review"], default="", help="Prepare a follow-up action from the use-review card.")
    use_review_parser.add_argument("--apply", action="store_true", help="Persist the prepared follow-up when safe and explicitly requested.")
    use_review_parser.add_argument("--approved-by", default="", help="Owner or team approving a use-review strengthen follow-up.")
    use_review_parser.add_argument("--mismatch", default="", help="Challenge mismatch override for --prepare challenge.")
    use_review_parser.add_argument("--benefit", default="", help="Challenge expected benefit override for --prepare challenge.")
    use_review_parser.add_argument("--risk", default="", help="Challenge risk override for --prepare challenge.")
    use_review_parser.add_argument("--rollback", default="", help="Challenge rollback path override for --prepare challenge.")
    use_review_parser.add_argument("--challenged-by", default="", help="Person, team, or agent raising a prepared challenge.")
    use_review_parser.add_argument("--evidence", action="append", default=[], help="Additional evidence for a prepared challenge.")
    use_review_parser.add_argument("--scope-owner", action="append", default=[], help="Replacement ownership scope for approved scope-review apply.")
    use_review_parser.add_argument("--scope-code", action="append", default=[], help="Replacement code scope for approved scope-review apply.")
    use_review_parser.add_argument("--scope-workflow", action="append", default=[], help="Replacement workflow scope for approved scope-review apply.")
    use_review_parser.add_argument("--scope-env", action="append", default=[], help="Replacement environment scope for approved scope-review apply.")
    use_review_parser.add_argument("--scope-actor", action="append", default=[], help="Replacement actor scope for approved scope-review apply.")
    use_review_parser.add_argument("--scope-time", action="append", default=[], help="Replacement time/version scope for approved scope-review apply.")
    use_review_parser.set_defaults(func=cmd_use_review)

    semantic_audit_parser = subparsers.add_parser(
        "semantic-audit",
        help="Review semantic-assisted use evidence without mutating memories or receipts.",
    )
    semantic_audit_parser.add_argument("--memory", default="", help="Limit semantic audit to one memory id.")
    semantic_audit_parser.add_argument("--recommendations", action="store_true", help="Group semantic audit evidence into read-only next-action recommendations.")
    semantic_audit_parser.add_argument("--details", action="store_true", help="Include receipt-level semantic evidence and auto-link candidate details.")
    semantic_audit_parser.add_argument("--open-only", action="store_true", help="With --recommendations --details, show only unresolved semantic receipt details.")
    semantic_audit_parser.add_argument("--commands-only", action="store_true", help="With --recommendations --details --open-only, render only closure commands for unresolved semantic receipts.")
    semantic_audit_parser.add_argument("--receipt", default="", help="With --recommendations --details, limit receipt-level details to one semantic receipt id.")
    semantic_audit_parser.add_argument("--limit", type=int, default=20, help="With --recommendations --details, number of recent commits to inspect for link candidates.")
    semantic_audit_parser.add_argument("--hours", type=int, default=72, help="With --recommendations --details, maximum hours after a receipt to consider a commit.")
    semantic_audit_parser.add_argument("--min-score", type=float, default=DEFAULT_AUTO_LINK_MIN_SCORE, help="With --recommendations --details, minimum candidate score to show.")
    semantic_audit_parser.add_argument("--candidate-limit", type=int, default=0, help="With --recommendations --details, maximum candidate commits to show per unresolved receipt. Defaults to all plausible candidates.")
    semantic_audit_parser.add_argument(
        "--action",
        choices=["link", "partial", "drag", "positive", "neutral", "none"],
        default="",
        help="With --recommendations, render only one recommendation bucket.",
    )
    semantic_audit_parser.add_argument(
        "--command-type",
        choices=["all", "link", "resolve"],
        default="all",
        help="With --commands-only, render all closure commands, only use-link commands, or only use-resolve commands.",
    )
    semantic_audit_parser.add_argument(
        "--resolve-outcome",
        choices=["all", "no-checkpoint", "not-applicable", "superseded"],
        default="all",
        help="With --commands-only, limit use-resolve command options to one outcome.",
    )
    semantic_audit_parser.set_defaults(func=cmd_semantic_audit)

    return parser


def cmd_init(args: argparse.Namespace, store: MemoryStore) -> int:
    path = store.init()
    print(f"Initialized CMU store at {path}")
    return 0


def cmd_quickstart_demo(args: argparse.Namespace, store: MemoryStore) -> int:
    report = quickstart_demo(args.root, apply=args.apply)
    print(report.render())
    return 0 if report.applied or not args.apply else 1


def cmd_add(args: argparse.Namespace, store: MemoryStore) -> int:
    memory_type = MemoryType(args.type)
    if memory_type in {MemoryType.ANCHOR, MemoryType.PRACTICE} and not args.approved_by.strip():
        raise SystemExit(f"add --type {memory_type.value} requires --approved-by")
    memory = Memory.create(
        type=memory_type,
        title=args.title,
        summary=args.summary,
        signals=args.signal,
        scope=MemoryScope(
            ownership=args.scope_owner,
            code=args.scope_code,
            workflow=args.scope_workflow,
            environment=args.scope_env,
            actor=args.scope_actor,
            time=args.scope_time,
        ),
        evidence=args.evidence,
        use_this_path=args.use_path,
        avoid_this=args.avoid,
        challenge_only_if=args.challenge,
        liability_score=args.liability,
        confidence=args.confidence,
        approved_by=args.approved_by,
    )
    if args.authority_owner or args.approver_role or args.consequence or args.review_due:
        decision = set_memory_authority(
            memory,
            owner=args.authority_owner,
            approved_by=args.approved_by,
            approver_role=args.approver_role,
            consequence=args.consequence,
            review_due_at=args.review_due,
        )
        if not decision.applied:
            raise SystemExit(decision.reason)
    store.add(memory)
    print(f"Added {memory.type.value} memory {memory.id}: {memory.title}")
    return 0


def cmd_list(args: argparse.Namespace, store: MemoryStore) -> int:
    memory_type = MemoryType(args.type) if args.type else None
    memories = store.list(type=memory_type)[: args.limit]
    if not memories:
        print("No CMU memories found.")
        return 0
    for memory in memories:
        print(f"{memory.id} [{memory.type.value}] L{memory.liability_score} C{memory.confidence:.2f} - {memory.title}")
    return 0


def cmd_relate(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    source = find_memory(memories, args.source_id)
    target = find_memory(memories, args.target)
    relation_type = MemoryRelationType(args.type)
    for relationship in source.relationships:
        if relationship.type == relation_type and relationship.target_id == target.id:
            print("CMU Memory Relationship Not Applied")
            print(f"Reason: relationship already exists from {source.id} to {target.id} as {relation_type.value}")
            return 0
    source.relationships.append(
        MemoryRelationship(
            type=relation_type,
            target_id=target.id,
            reason=args.reason.strip(),
        )
    )
    store.update(source)
    print("CMU Memory Relationship Applied")
    print(f"Source: {source.id} {source.title}")
    print(f"Type: {relation_type.value}")
    print(f"Target: {target.id} {target.title}")
    if args.reason.strip():
        print(f"Reason: {args.reason.strip()}")
    return 0


def cmd_relations(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    memory = find_memory(memories, args.memory_id)
    memory_by_id = {item.id: item for item in memories}
    lines = [
        "CMU Memory Relationships",
        f"Memory: {memory.id} {memory.title}",
        "Outgoing:",
    ]
    if not memory.relationships:
        lines.append("- None")
    else:
        for relationship in memory.relationships:
            target = memory_by_id.get(relationship.target_id)
            label = f"{target.id} {target.title}" if target is not None else relationship.target_id
            reason = f" - {relationship.reason}" if relationship.reason else ""
            lines.append(f"- {relationship.type.value} -> {label}{reason}")
    incoming = [
        (source, relationship)
        for source in memories
        for relationship in source.relationships
        if relationship.target_id == memory.id
    ]
    lines.append("Incoming:")
    if not incoming:
        lines.append("- None")
    else:
        for source, relationship in incoming:
            reason = f" - {relationship.reason}" if relationship.reason else ""
            lines.append(f"- {relationship.type.value} <- {source.id} {source.title}{reason}")
    print("\n".join(lines))
    return 0


def cmd_graph(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.depth < 1:
        raise SystemExit("graph --depth must be at least 1")
    memories = store.list()
    if args.include_retired:
        memories.extend(store.list(status=MemoryStatus.RETIRED))
    try:
        report = graph_memory_view_report(
            memories,
            root_id=args.memory_id,
            max_depth=args.depth,
            include_retired=args.include_retired,
        )
    except KeyError as error:
        raise SystemExit(error.args[0]) from error
    print(report.render())
    return 0


def cmd_agent_tools(args: argparse.Namespace, store: MemoryStore) -> int:
    print(json.dumps(AgentIntegration(args.root).manifest(), indent=2, ensure_ascii=True))
    return 0


def cmd_agent_call(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.input and args.input_file:
        raise SystemExit("agent-call accepts either --input or --input-file, not both")
    raw_input = args.input or "{}"
    if args.input_file:
        if args.input_file == "-":
            raw_input = sys.stdin.read()
        else:
            raw_input = Path(args.input_file).read_text(encoding="utf-8")
    try:
        arguments = json.loads(raw_input)
    except json.JSONDecodeError as error:
        raise SystemExit(f"agent-call --input must be valid JSON: {error.msg}") from error
    response = AgentIntegration(args.root).invoke(args.tool, arguments)
    print(json.dumps(response, indent=2, ensure_ascii=True))
    return 0 if response["ok"] else 1


def cmd_portable_export(args: argparse.Namespace, store: MemoryStore) -> int:
    bundle = export_bundle_from_root(
        args.root,
        include_retired=args.include_retired,
        memory_id=args.memory,
        include_uses=not args.no_uses,
    )
    rendered = bundle.render_json()
    if args.output == "-":
        print(rendered, end="")
    else:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print("CMU Portable Export Written")
        print(f"Path: {args.output}")
        print(f"Schema: {bundle.schema}")
        print(f"Memories: {len(bundle.memories)}")
        print(f"Use Receipts: {len(bundle.uses)}")
        if bundle.warnings:
            print("Warnings:")
            for warning in bundle.warnings[:10]:
                print(f"- {warning}")
    return 0


def cmd_portable_import(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        bundle = load_portable_bundle(args.bundle)
        report = import_portable_bundle(
            args.root,
            bundle,
            apply=args.apply,
            update_existing=args.update_existing,
        )
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"portable-import failed: {error}") from error
    print(report.render())
    return 0 if not (args.apply and report.conflicts) else 1


def cmd_preflight(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("preflight requires a task prompt")
    query = build_query(args, prompt)
    memories = store.list()
    use_store = MemoryUseStore(args.root)
    semantic_index = load_semantic_index(args, memories)
    matches = actionable_matches(memories, query, use_store, semantic_index)
    note = build_action_note(matches[0]) if matches else None
    if args.show_matches:
        for match in matches[:5]:
            print(format_preflight_match(match))
        if note:
            print()
    if args.show_semantic_proposals:
        print_semantic_proposal_diagnostics(memories, query, semantic_index)
        if note:
            print()
    if note is None:
        print("CMU stayed quiet: no memory crossed the action threshold.")
        return 0
    print(note.render())
    if matches:
        receipt = MemoryUseReceipt.create(
            matches[0].memory,
            query,
            matches[0],
            source_command="preflight",
            semantic_mode=getattr(args, "semantic", "off"),
        )
        use_store.add(receipt)
        print(f"Use Receipt: {receipt.id}")
    return 0


def cmd_retrieval_pipeline(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("retrieval-pipeline requires a task prompt")
    memories = store.list()
    query = build_query(args, prompt)
    semantic_index = load_semantic_index(args, memories)
    report = hybrid_pipeline_report(
        memories,
        MemoryUseStore(args.root).list(),
        query,
        semantic_index=semantic_index,
    )
    print(report.render())
    return 0


def cmd_onboard(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("onboard requires a task prompt")
    query = build_query(args, prompt)
    memories = store.list()
    semantic_index = load_semantic_index(args, memories)
    seed = build_onboarding_seed(memories, query, semantic_index=semantic_index)
    print(seed.render())
    if args.show_semantic_proposals:
        print()
        print_semantic_proposal_diagnostics(memories, query, semantic_index)
    return 0


def cmd_semantic_status(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    path = Path(args.root) / ".cmu" / "semantic_index.json"
    print(semantic_index_status(path, memories).render())
    return 0


def cmd_trigger(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("trigger requires a task prompt")
    query = build_query(args, prompt)
    decision = trigger_decision_from_args(args, query)
    print(decision.render())
    return 0


def cmd_start(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("start requires a task prompt")
    query = build_query(args, prompt)
    decision = trigger_decision_from_args(args, query)
    print("CMU Start")
    print(decision.render())
    if decision.level == "silent-skip":
        print("Work Cycle: silent-skip; no onboarding seed, Action Note, or receipt created.")
        return 0

    memories = store.list()
    use_store = MemoryUseStore(args.root)
    semantic_index = load_semantic_index(args, memories)
    seed = build_onboarding_seed(memories, query, semantic_index=semantic_index)
    print()
    print(seed.render())

    matches = actionable_matches(memories, query, use_store, semantic_index)
    note = build_action_note(matches[0]) if matches else None
    if args.show_matches:
        print()
        for match in matches[:5]:
            print(format_preflight_match(match))
    if args.show_semantic_proposals:
        print()
        print_semantic_proposal_diagnostics(memories, query, semantic_index)
    if note is None:
        print()
        print("CMU stayed quiet: no memory crossed the action threshold.")
        return 0
    print()
    print(note.render())
    receipt = MemoryUseReceipt.create(
        matches[0].memory,
        query,
        matches[0],
        source_command="start",
        semantic_mode=getattr(args, "semantic", "off"),
    )
    use_store.add(receipt)
    print(f"Use Receipt: {receipt.id}")
    return 0


def cmd_work_cycle(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("work-cycle requires a task prompt")
    memories = store.list()
    query = build_query(args, prompt)
    semantic_index = load_semantic_index(args, memories)
    report = work_cycle_report(
        memories,
        MemoryUseStore(args.root).list(),
        WorkCycleRequest(
            prompt=prompt,
            query=query,
            repeated_error=args.repeated_error,
            uncertainty=args.uncertainty,
            shared_contract=args.shared_contract,
            irreversible=args.irreversible,
            unfamiliar=args.unfamiliar,
            learning_signals=args.learning_signal,
            outcome=args.outcome,
            worked=args.worked,
            failed=args.failed,
            future_use=args.future_use,
            evidence=args.evidence,
        ),
        semantic_index=semantic_index,
    )
    print(report.render())
    return 0


def cmd_evaluate_scenario(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("evaluate-scenario requires a task prompt")
    memories = store.list()
    query = build_query(args, prompt)
    semantic_index = load_semantic_index(args, memories)
    request = ScenarioEvaluationRequest(
        prompt=prompt,
        query=query,
        repeated_error=args.repeated_error,
        uncertainty=args.uncertainty,
        shared_contract=args.shared_contract,
        irreversible=args.irreversible,
        unfamiliar=args.unfamiliar,
        expect_trigger=args.expect_trigger,
        expect_action=args.expect_action,
        expect_memory=args.expect_memory,
        expect_candidate=args.expect_candidate,
        learning_signals=args.learning_signal,
        worked=args.worked,
        failed=args.failed,
        future_use=args.future_use,
        evidence=args.evidence,
    )
    report = evaluate_scenario(
        memories,
        MemoryUseStore(args.root).list(),
        request,
        semantic_index=semantic_index,
    )
    print(report.render())
    return 0


def cmd_scenario_add(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("scenario-add requires a task prompt")
    scenario = ScenarioDefinition.create(
        name=args.name,
        prompt=prompt,
        actor=args.actor,
        area=args.area,
        files=args.file,
        workflow=args.workflow,
        environment=args.environment,
        risk=args.risk,
        repeated_error=args.repeated_error,
        uncertainty=args.uncertainty,
        shared_contract=args.shared_contract,
        irreversible=args.irreversible,
        unfamiliar=args.unfamiliar,
        expect_trigger=args.expect_trigger,
        expect_action=args.expect_action,
        expect_memory=args.expect_memory,
        expect_candidate=args.expect_candidate,
        learning_signals=args.learning_signal,
        worked=args.worked,
        failed=args.failed,
        future_use=args.future_use,
        evidence=args.evidence,
        tags=args.tag,
        description=args.description,
    )
    ScenarioLibraryStore(args.root).add(scenario)
    print("CMU Scenario Saved")
    print(f"ID: {scenario.id}")
    print(f"Name: {scenario.name}")
    print(f"Prompt: {scenario.prompt}")
    if scenario.tags:
        print(f"Tags: {', '.join(scenario.tags)}")
    return 0


def cmd_scenario_list(args: argparse.Namespace, store: MemoryStore) -> int:
    scenarios = ScenarioLibraryStore(args.root).list(tag=args.tag)
    if not scenarios:
        print("No saved CMU scenarios.")
        return 0
    print("CMU Scenario Library")
    for scenario in scenarios[: args.limit]:
        print(scenario.render_summary())
    if len(scenarios) > args.limit:
        print(f"... {len(scenarios) - args.limit} more")
    return 0


def cmd_scenario_run(args: argparse.Namespace, store: MemoryStore) -> int:
    library = ScenarioLibraryStore(args.root)
    if args.scenario:
        scenarios = [library.get(args.scenario)]
        tag = ""
    else:
        scenarios = library.list(tag=args.tag)
        tag = args.tag
    memories = store.list()
    semantic_index = load_semantic_index(args, memories)
    report = run_scenario_library(
        scenarios,
        memories,
        MemoryUseStore(args.root).list(),
        semantic_index=semantic_index,
        tag=tag,
    )
    print(report.render())
    if args.strict and report.has_review_items():
        return 1
    return 0


def cmd_trace_add(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("trace-add requires a task prompt")
    trace = RawTrace.create(
        prompt=prompt,
        actor=args.actor,
        area=args.area,
        files=args.file,
        workflow=args.workflow,
        environment=args.environment,
        risk=args.risk,
        learning_signals=args.learning_signal,
        outcome=args.outcome,
        worked=args.worked,
        failed=args.failed,
        future_use=args.future_use,
        evidence=args.evidence,
    )
    RawTraceStore(args.root).add(trace)
    distillation = distill_trace(trace, store.list())
    print("CMU Raw Trace Captured")
    print(f"ID: {trace.id}")
    print(f"Status: {trace.status}")
    print()
    print("Distillation Preview:")
    print(distillation.render())
    return 0


def cmd_trace_distill(args: argparse.Namespace, store: MemoryStore) -> int:
    trace_store = RawTraceStore(args.root)
    if args.trace_id:
        trace = trace_store.get(args.trace_id)
        traces = [trace] if trace.status == "raw" or args.include_distilled else []
    else:
        traces = trace_store.list(include_distilled=args.include_distilled)
    memories = store.list()
    distillations = []
    for trace in traces:
        distillation = distill_trace(trace, memories)
        distillations.append(distillation)
        if args.apply and trace.status == "raw":
            if distillation.decision.saved and distillation.decision.memory is not None:
                store.add(distillation.decision.memory)
                memories.append(distillation.decision.memory)
            apply_distillation(trace_store, trace, distillation.decision)
    print(TraceDistillationReport(distillations=distillations, apply=args.apply).render())
    return 0


def cmd_remember(args: argparse.Namespace, store: MemoryStore) -> int:
    request = RememberRequest(
        situation=args.situation,
        title=args.title,
        signals=args.signal,
        outcome=args.outcome,
        worked=args.worked,
        failed=args.failed,
        future_use=args.future_use,
        evidence=args.evidence,
        liability_score=args.liability,
        suggested_next_type=MemoryType(args.suggested_next_type),
        scope=MemoryScope(
            ownership=args.scope_owner,
            code=args.scope_code,
            workflow=args.scope_workflow,
            environment=args.scope_env,
            actor=args.scope_actor,
            time=args.scope_time,
        ),
        confidence=args.confidence,
    )
    decision = remember_candidate(store.list(), request)
    if decision.saved and decision.memory is not None:
        store.add(decision.memory)
    print(decision.render())
    return 0


def cmd_review(args: argparse.Namespace, store: MemoryStore) -> int:
    if not args.memory_id:
        candidates = store.list(type=MemoryType.CANDIDATE)
        if not candidates:
            print("No Candidate Memories need review.")
            return 0
        for memory in candidates:
            print(f"{memory.id} [candidate] L{memory.liability_score} C{memory.confidence:.2f} - {memory.title}")
        return 0
    review = review_promotion(store.list(), args.memory_id, MemoryType(args.to))
    print(review.render())
    return 0


def cmd_lifecycle(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_report(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
    )
    print(report.render())
    return 0


def cmd_gravity(args: argparse.Namespace, store: MemoryStore) -> int:
    report = gravity_report(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
    )
    print(report.render())
    return 0


def cmd_governance(args: argparse.Namespace, store: MemoryStore) -> int:
    report = governance_report(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
    )
    print(report.render())
    return 0


def cmd_authority(args: argparse.Namespace, store: MemoryStore) -> int:
    print(authority_report(store.list(), memory_id=args.memory, include_all=args.all).render())
    return 0


def cmd_authority_set(args: argparse.Namespace, store: MemoryStore) -> int:
    memory = find_memory(store.list(), args.memory_id)
    decision = set_memory_authority(
        memory,
        owner=args.owner,
        approved_by=args.approved_by,
        approver_role=args.approver_role,
        consequence=args.consequence,
        review_due_at=args.review_due,
    )
    if decision.applied and decision.memory is not None:
        store.update(decision.memory)
    print(decision.render())
    return 0


def cmd_quality(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    if args.include_retired:
        memories.extend(store.list(status=MemoryStatus.RETIRED))
    print(
        quality_report(
            memories,
            MemoryUseStore(args.root).list(),
            memory_id=args.memory,
            include_retired=args.include_retired,
        ).render()
    )
    return 0


def cmd_decay_apply(args: argparse.Namespace, store: MemoryStore) -> int:
    decision = apply_decay_action(
        store.list(),
        MemoryUseStore(args.root).list(),
        args.memory_id,
        action=args.action,
        reason=args.reason,
        approved_by=args.approved_by,
        approver_role=args.approver_role,
    )
    if decision.applied and decision.memory is not None:
        store.update(decision.memory)
    print(decision.render())
    return 0


def cmd_analytics(args: argparse.Namespace, store: MemoryStore) -> int:
    report = usefulness_analytics_report(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
    )
    print(report.render())
    return 0


def cmd_anti_pattern(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    memories = store.list()
    query = build_query(args, prompt) if prompt else None
    semantic_index = load_semantic_index(args, memories) if query is not None else None
    report = anti_pattern_report(
        memories,
        MemoryUseStore(args.root).list(),
        query=query,
        memory_id=args.memory,
        semantic_index=semantic_index,
    )
    print(report.render())
    return 0


def cmd_question(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    memories = store.list()
    if args.include_retired:
        memories = [*memories, *store.list(status=MemoryStatus.RETIRED)]
    query = build_query(args, prompt) if prompt else None
    semantic_index = load_semantic_index(args, memories) if query is not None else None
    report = question_report(
        memories,
        query=query,
        memory_id=args.memory,
        include_retired=args.include_retired,
        semantic_index=semantic_index,
    )
    print(report.render())
    return 0


def cmd_resolve_question(args: argparse.Namespace, store: MemoryStore) -> int:
    decision = resolve_question(
        store.list(),
        ResolveQuestionRequest(
            question_id=args.question_id,
            outcome=args.outcome,
            answer=args.answer,
            resolved_by=args.resolved_by,
            evidence=args.evidence,
            title=args.title,
            use_path=args.use_path,
            avoid=args.avoid,
            review_if=args.review_if,
        ),
    )
    if decision.applied:
        if decision.outcome_memory is not None:
            store.add(decision.outcome_memory)
        if decision.question is not None:
            store.update(decision.question)
    print(decision.render())
    return 0


def cmd_promote(args: argparse.Namespace, store: MemoryStore) -> int:
    decision = promote_memory(
        store.list(),
        args.memory_id,
        MemoryType(args.to),
        approved_by=args.approved_by,
        authority_owner=args.authority_owner,
        approver_role=args.approver_role,
        consequence=args.consequence,
        review_due_at=args.review_due,
    )
    if decision.promoted and decision.memory is not None:
        store.update(decision.memory)
    print(decision.render())
    return 0


def cmd_challenge(args: argparse.Namespace, store: MemoryStore) -> int:
    request = ChallengeRequest(
        memory_id=args.memory_id,
        mismatch=args.mismatch,
        benefit=args.benefit,
        risk=args.risk,
        rollback=args.rollback,
        challenged_by=args.challenged_by,
        evidence=args.evidence,
        confidence=args.confidence,
    )
    decision = challenge_stable_memory(store.list(), request)
    if decision.saved and decision.challenge_memory is not None:
        store.add(decision.challenge_memory)
    print(decision.render())
    return 0


def cmd_resolve_challenge(args: argparse.Namespace, store: MemoryStore) -> int:
    request = ResolveChallengeRequest(
        challenge_id=args.challenge_id,
        outcome=args.outcome,
        approved_by=args.approved_by,
        replacement_title=args.replacement_title,
        replacement_summary=args.replacement_summary,
        replacement_use_path=args.replacement_use_path,
        replacement_avoid=args.replacement_avoid,
        replacement_challenge=args.replacement_challenge,
        retirement_reason=args.retirement_reason,
        split_title=args.split_title,
        split_summary=args.split_summary,
        split_use_path=args.split_use_path,
        split_avoid=args.split_avoid,
        split_challenge=args.split_challenge,
        split_scope=MemoryScope(
            ownership=args.scope_owner,
            code=args.scope_code,
            workflow=args.scope_workflow,
            environment=args.scope_env,
            actor=args.scope_actor,
            time=args.scope_time,
        ),
        evidence=args.evidence,
    )
    decision = resolve_challenge(store.list(), request)
    if decision.applied:
        if decision.outcome_memory is not None:
            store.add(decision.outcome_memory)
        if decision.stable_memory is not None:
            store.update(decision.stable_memory)
        if decision.challenge_memory is not None:
            store.update(decision.challenge_memory)
    print(decision.render())
    return 0


def cmd_use_link(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    receipt = use_store.get(args.use_id)
    if args.manual:
        decision = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=args.use_id,
                commit_hash=args.commit,
                message=args.message,
                files=args.file,
                note=args.note,
                metadata_source="manual",
            ),
        )
    else:
        decision = link_git_commit(
            receipt,
            root=args.root,
            ref=args.commit,
            note=args.note,
            message_override=args.message,
            files_override=args.file,
        )
    if decision.linked and decision.receipt is not None:
        use_store.update(decision.receipt)
    print(decision.render())
    return 0


def cmd_use_link_latest(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    receipt = use_store.get(args.use_id)
    decision = link_git_commit(
        receipt,
        root=args.root,
        ref=args.ref,
        note=args.note,
        message_override=args.message,
        files_override=args.file,
    )
    if decision.linked and decision.receipt is not None:
        use_store.update(decision.receipt)
    print(decision.render())
    return 0


def cmd_use_link_auto(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    report = auto_link_receipts(
        use_store.list(),
        store.list(),
        root=args.root,
        limit=args.limit,
        hours=args.hours,
        min_score=args.min_score,
        apply=args.apply,
    )
    if args.apply:
        for decision in report.decisions:
            if decision.applied and decision.matched:
                use_store.update(decision.receipt)
    print(report.render())
    return 0


def cmd_use_resolve(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    receipt = use_store.get(args.use_id)
    decision = resolve_receipt_without_commit(
        receipt,
        outcome=args.outcome,
        note=args.note,
        resolved_by=args.resolved_by,
    )
    if decision.resolved and decision.receipt is not None:
        use_store.update(decision.receipt)
    print(decision.render())
    return 0


def cmd_use_list(args: argparse.Namespace, store: MemoryStore) -> int:
    receipts = MemoryUseStore(args.root).list()[: args.limit]
    if not receipts:
        print("No CMU memory use receipts found.")
        return 0
    for receipt in receipts:
        commit = receipt.commit_hash or "unlinked"
        outcome = receipt.outcome_signal or "surfaced"
        semantic = f" semantic={receipt.semantic_mode}" if receipt.semantic_mode and receipt.semantic_mode != "off" else ""
        print(f"{receipt.id} {receipt.source_command} {outcome} {commit} - {receipt.memory_id} {receipt.memory_title}{semantic}")
    return 0


def cmd_use_summary(args: argparse.Namespace, store: MemoryStore) -> int:
    summary = use_summary(MemoryUseStore(args.root).list(), args.memory_id)
    print(summary.render())
    return 0


def cmd_use_review(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    if args.thresholds:
        if args.memory_id:
            raise SystemExit("use-review --thresholds reviews all receipts and does not accept a memory id")
        if args.prepare or args.apply:
            raise SystemExit("use-review --thresholds is diagnostic only and cannot be combined with --prepare or --apply")
        report = use_threshold_report(use_store.list(), store.list())
        print(report.render())
        return 0
    if args.prepare:
        if not args.memory_id:
            raise SystemExit("use-review --prepare requires a memory id")
        followup = prepare_use_review_followup(
            use_store.list(),
            store.list(),
            args.memory_id,
            args.prepare,
            apply=args.apply,
            approved_by=args.approved_by,
            mismatch=args.mismatch,
            benefit=args.benefit,
            risk=args.risk,
            rollback=args.rollback,
            challenged_by=args.challenged_by,
            evidence=args.evidence,
            proposed_scope=MemoryScope(
                ownership=args.scope_owner,
                code=args.scope_code,
                workflow=args.scope_workflow,
                environment=args.scope_env,
                actor=args.scope_actor,
                time=args.scope_time,
            ),
        )
        if args.apply and followup.applied:
            if followup.action == "strengthen" and followup.memory is not None:
                store.update(followup.memory)
            elif followup.action == "challenge" and followup.challenge_memory is not None:
                store.add(followup.challenge_memory)
            elif followup.action == "scope-review" and followup.memory is not None:
                store.update(followup.memory)
        print(followup.render())
        return 0
    report = use_review(use_store.list(), store.list(), args.memory_id or "")
    print(report.render())
    return 0


def cmd_semantic_audit(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    if args.details and not args.recommendations:
        raise SystemExit("semantic-audit --details is only available with --recommendations")
    if args.open_only and not args.details:
        raise SystemExit("semantic-audit --open-only is only available with --recommendations --details")
    if args.commands_only and not (args.recommendations and args.details and args.open_only):
        raise SystemExit("semantic-audit --commands-only requires --recommendations --details --open-only")
    if args.command_type != "all" and not args.commands_only:
        raise SystemExit("semantic-audit --command-type requires --commands-only")
    if args.resolve_outcome != "all" and not args.commands_only:
        raise SystemExit("semantic-audit --resolve-outcome requires --commands-only")
    if args.receipt and not (args.recommendations and args.details):
        raise SystemExit("semantic-audit --receipt requires --recommendations --details")
    if (args.limit != 20 or args.hours != 72 or args.min_score != DEFAULT_AUTO_LINK_MIN_SCORE or args.candidate_limit) and not (args.recommendations and args.details):
        raise SystemExit("semantic-audit candidate tuning requires --recommendations --details")
    if args.candidate_limit < 0:
        raise SystemExit("semantic-audit --candidate-limit must be zero or greater")
    if args.action and not args.recommendations:
        raise SystemExit("semantic-audit --action requires --recommendations")
    if args.recommendations:
        if args.memory:
            raise SystemExit("semantic-audit --recommendations reviews all memories and does not accept --memory")
        report = semantic_audit_recommendations(
            use_store.list(),
            store.list(),
            root=args.root,
            details=args.details,
            limit=args.limit,
            hours=args.hours,
            min_score=args.min_score,
            candidate_limit=args.candidate_limit,
            open_only=args.open_only,
            commands_only=args.commands_only,
            receipt_id=args.receipt,
            action_filter=args.action,
            command_type=args.command_type,
            resolve_outcome=args.resolve_outcome,
        )
        print(report.render())
        return 0
    report = semantic_audit(use_store.list(), store.list(), args.memory)
    print(report.render())
    return 0


def build_query(args: argparse.Namespace, prompt: str) -> PreflightQuery:
    return PreflightQuery(
        prompt=prompt,
        actor=args.actor,
        area=args.area,
        files=args.file,
        workflow=args.workflow,
        environment=args.environment,
        risk=args.risk,
    )


def load_semantic_index(args: argparse.Namespace, memories: list[Memory]):
    if getattr(args, "semantic", "off") == "local":
        return PersistentSemanticIndex.load_or_build(Path(args.root) / ".cmu" / "semantic_index.json", memories)
    return None


def actionable_matches(memories: list[Memory], query: PreflightQuery, use_store: MemoryUseStore, semantic_index):
    threshold = action_threshold(query.risk)
    raw_matches = rank_memories(memories, query, semantic_index=semantic_index)
    actionable = [match for match in raw_matches if match.score >= threshold]
    return apply_usage_adjustments(actionable, use_store.list())


def trigger_decision_from_args(args: argparse.Namespace, query: PreflightQuery):
    return decide_trigger(
        query,
        repeated_error=args.repeated_error,
        uncertainty=args.uncertainty,
        shared_contract=args.shared_contract,
        irreversible=args.irreversible,
        unfamiliar=args.unfamiliar,
    )


def project_root() -> Path:
    return Path.cwd()


def format_preflight_match(match) -> str:
    lines = [f"match {match.score}: {match.memory.id} - {match.memory.title}"]
    if match.score_breakdown:
        lines.append("  score:")
        for item in match.score_breakdown[:8]:
            lines.append(f"    - {item}")
    if match.is_graph_expanded():
        lines.append(f"  via: {match.graph_source_id} {match.graph_source_title}")
        lines.append(f"  relation: {match.graph_relation_type}")
        if match.graph_relation_reason:
            lines.append(f"  reason: {match.graph_relation_reason}")
    return "\n".join(lines)


def print_semantic_proposal_diagnostics(memories: list[Memory], query: PreflightQuery, semantic_index) -> None:
    print("CMU Semantic Proposal Diagnostics")
    diagnostics = semantic_proposal_diagnostics(memories, query, semantic_index)
    if not diagnostics:
        print("No available semantic proposal signal. Enable --semantic local to inspect proposal behavior.")
        return
    for diagnostic in diagnostics:
        print(diagnostic.render())


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise SystemExit(f"Memory not found: {memory_id}")
