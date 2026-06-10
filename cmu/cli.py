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
from .codex_adapter import codex_runner_report
from .demo_walkthrough import demo_walkthrough
from .dist_check import dist_check
from .doc_curation import DocumentCurationReport, apply_selected_curation_decisions, curate_documents
from .evidence_monitor import DEFAULT_MONITOR_MIN_CONFIDENCE, DEFAULT_MONITOR_MIN_SCORE, monitor_checkpoints
from .evidence_service_install import evidence_service_install
from .evidence_service import run_evidence_service
from .evidence_watch import run_evidence_watch
from .fixture_repos import FIXTURE_KINDS, create_fixture_repo
from .governance import governance_report
from .graphview import graph_memory_view_report
from .gravity import gravity_report
from .hardening_cycle import hardening_cycle_report
from .host_path_suite import run_host_path_suite
from .host_examples import host_examples
from .host_setup_manifest import host_setup_manifest
from .install_check import install_check
from .lifecycle_apply import apply_lifecycle_candidates
from .lifecycle import lifecycle_report
from .lifecycle_ops import (
    lifecycle_archive,
    lifecycle_demote,
    lifecycle_merge,
    lifecycle_proposals,
    lifecycle_scope_record,
)
from .lifecycle_settling import lifecycle_scope_suggestions, lifecycle_settle
from .mcp import StdioMcpServer, CmuMcpAdapter
from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from .onboarding import build_onboarding_seed
from .openai_adapter import openai_runner_report
from .pipeline import hybrid_pipeline_report
from .portable import export_bundle_from_root, import_portable_bundle, load_portable_bundle, validate_portable_bundle
from .portable_compat import portable_compat_report
from .portable_fixture_seed import seed_portable_fixtures
from .promotion import promote_memory, review_promotion
from .questions import ResolveQuestionRequest, question_report, resolve_question
from .quality import apply_decay_action, quality_report
from .quickstart import quickstart_demo
from .readiness import readiness_report
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
from .review_queue import review_queue
from .review_export import export_review_payload
from .review_inbox import review_inbox_from_export, review_inbox_from_reports
from .review_reminders import review_reminders
from .reminder_delivery import deliver_reminders_to_outbox
from .runner_hooks import runner_hooks_report
from .runner_scenarios import RunnerScenarioRequest, run_runner_scenario
from .scenarios import (
    ScenarioDefinition,
    ScenarioEvaluationRequest,
    ScenarioLibraryStore,
    compare_scenario_library,
    evaluate_scenario,
    run_scenario_library,
)
from .seed_plan import seed_plan_report
from .setup import HOST_CHOICES, setup_guide
from .store import MemoryStore
from .team_directory import TeamDirectoryStore, TeamScopeRecord, team_directory_report
from .team_review_action import apply_team_review_action
from .team_review_handoff import team_review_handoffs
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
from .evidence_session import run_evidence_session
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

    demo_parser = subparsers.add_parser("demo-walkthrough", help="Run the scripted local CMU adoption and proof walkthrough.")
    demo_parser.add_argument("--apply", action="store_true", help="Apply the Git-backed quickstart proof inside the walkthrough.")
    demo_parser.set_defaults(func=cmd_demo_walkthrough)

    setup_parser = subparsers.add_parser("setup-guide", help="Show read-only CLI, SDK, and MCP host setup guidance.")
    setup_parser.add_argument("--host", choices=HOST_CHOICES, default="all", help="Limit guidance to one host surface.")
    setup_parser.set_defaults(func=cmd_setup_guide)

    host_manifest_parser = subparsers.add_parser("host-setup-manifest", help="Write a machine-readable IDE/coding-agent setup manifest.")
    host_manifest_parser.add_argument("--host", choices=["all", "codex", "openai", "mcp"], default="all")
    host_manifest_parser.add_argument("--output", default=".cmu/host_setup_manifest.json")
    host_manifest_parser.add_argument("--write", action="store_true", help="Write the manifest JSON. Defaults to preview.")
    host_manifest_parser.set_defaults(func=cmd_host_setup_manifest)

    host_examples_parser = subparsers.add_parser("host-examples", help="Generate manifest-derived integration examples for common agent runtimes.")
    host_examples_parser.add_argument("--host", choices=["all", "codex", "openai", "mcp"], default="all")
    host_examples_parser.add_argument("--output", default=".cmu/host-examples")
    host_examples_parser.add_argument("--write", action="store_true", help="Write example files. Defaults to preview.")
    host_examples_parser.set_defaults(func=cmd_host_examples)

    install_check_parser = subparsers.add_parser("install-check", help="Validate README, package, SDK, CLI, and MCP adoption surfaces.")
    install_check_parser.set_defaults(func=cmd_install_check)

    dist_check_parser = subparsers.add_parser("dist-check", help="Build/install CMU in a temporary venv and validate installed CLI/MCP behavior.")
    dist_check_parser.add_argument("--python", default="", help="Python executable used to create the validation venv. Defaults to the current Python.")
    dist_check_parser.add_argument("--work-dir", default="", help="Directory for temporary validation files. Defaults to .manual under the project root.")
    dist_check_parser.add_argument("--keep-work-dir", action="store_true", help="Keep temporary validation files for inspection.")
    dist_check_parser.set_defaults(func=cmd_dist_check)

    mcp_parser = subparsers.add_parser("mcp", help="Run the CMU MCP stdio server.")
    mcp_parser.set_defaults(func=cmd_mcp)

    agent_tools_parser = subparsers.add_parser("agent-tools", help="Show the stable direct agent tool-call manifest as JSON.")
    agent_tools_parser.set_defaults(func=cmd_agent_tools)

    agent_call_parser = subparsers.add_parser("agent-call", help="Invoke one stable direct agent tool with JSON arguments.")
    agent_call_parser.add_argument("tool", help="Agent tool name from cmu agent-tools.")
    agent_call_parser.add_argument("--input", default="", help="Inline JSON object containing the tool arguments.")
    agent_call_parser.add_argument("--input-file", default="", help="Read JSON arguments from a file, or '-' for stdin.")
    agent_call_parser.set_defaults(func=cmd_agent_call)

    runner_hooks_parser = subparsers.add_parser("runner-hooks", help="Show or execute autonomous-runner CMU hook integration.")
    runner_hooks_parser.add_argument("prompt", nargs="*", help="Optional task prompt. When supplied, runs the real before_task hook.")
    runner_hooks_parser.add_argument("--actor", default="agent")
    runner_hooks_parser.add_argument("--area", default="")
    runner_hooks_parser.add_argument("--file", action="append", default=[])
    runner_hooks_parser.add_argument("--workflow", action="append", default=[])
    runner_hooks_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    runner_hooks_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    runner_hooks_parser.add_argument("--repeated-error", action="store_true")
    runner_hooks_parser.add_argument("--uncertainty", action="store_true")
    runner_hooks_parser.add_argument("--shared-contract", action="store_true")
    runner_hooks_parser.add_argument("--irreversible", action="store_true")
    runner_hooks_parser.add_argument("--unfamiliar", action="store_true")
    runner_hooks_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider for the executed before_task hook.",
    )
    runner_hooks_parser.add_argument("--json", action="store_true", help="Render the hook manifest/result as JSON.")
    runner_hooks_parser.set_defaults(func=cmd_runner_hooks)

    codex_runner_parser = subparsers.add_parser("codex-runner", help="Show or execute the Codex host adapter for autonomous-runner events.")
    codex_runner_parser.add_argument("--input", default="", help="Inline JSON event object.")
    codex_runner_parser.add_argument("--input-file", default="", help="Read JSON event object from a file, or '-' for stdin.")
    codex_runner_parser.add_argument("--json", action="store_true", help="Render the adapter manifest/result as JSON.")
    codex_runner_parser.set_defaults(func=cmd_codex_runner)

    openai_runner_parser = subparsers.add_parser("openai-runner", help="Show or execute the OpenAI Agents host adapter for autonomous-runner events.")
    openai_runner_parser.add_argument("--input", default="", help="Inline JSON event object.")
    openai_runner_parser.add_argument("--input-file", default="", help="Read JSON event object from a file, or '-' for stdin.")
    openai_runner_parser.add_argument("--json", action="store_true", help="Render the adapter manifest/result as JSON.")
    openai_runner_parser.set_defaults(func=cmd_openai_runner)

    runner_scenario_parser = subparsers.add_parser("runner-scenario", help="Run a read-only autonomous-runner lifecycle scenario against an isolated store.")
    runner_scenario_parser.add_argument("prompt", nargs="*", help="Task prompt to evaluate through runner hooks.")
    runner_scenario_parser.add_argument("--actor", default="agent")
    runner_scenario_parser.add_argument("--area", default="")
    runner_scenario_parser.add_argument("--file", action="append", default=[])
    runner_scenario_parser.add_argument("--workflow", action="append", default=[])
    runner_scenario_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    runner_scenario_parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    runner_scenario_parser.add_argument("--repeated-error", action="store_true")
    runner_scenario_parser.add_argument("--uncertainty", action="store_true")
    runner_scenario_parser.add_argument("--shared-contract", action="store_true")
    runner_scenario_parser.add_argument("--irreversible", action="store_true")
    runner_scenario_parser.add_argument("--unfamiliar", action="store_true")
    runner_scenario_parser.add_argument("--semantic", choices=["off", "local"], default="off")
    runner_scenario_parser.add_argument("--after-task", action="store_true", help="Run the after_task hook after before_task.")
    runner_scenario_parser.add_argument("--reusable-learning", action="store_true", help="Tell after_task to draft Candidate Memory from supplied learning fields.")
    runner_scenario_parser.add_argument("--title", default="")
    runner_scenario_parser.add_argument("--situation", default="")
    runner_scenario_parser.add_argument("--signal", action="append", default=[])
    runner_scenario_parser.add_argument("--outcome", default="")
    runner_scenario_parser.add_argument("--worked", default="")
    runner_scenario_parser.add_argument("--failed", default="")
    runner_scenario_parser.add_argument("--future-use", default="")
    runner_scenario_parser.add_argument("--evidence", action="append", default=[])
    runner_scenario_parser.add_argument("--liability", type=int, default=1)
    runner_scenario_parser.add_argument("--confidence", type=float, default=0.6)
    runner_scenario_parser.add_argument("--suggested-next-type", choices=[item.value for item in MemoryType], default=MemoryType.SITUATION.value)
    runner_scenario_parser.add_argument("--scope-owner", action="append", default=[])
    runner_scenario_parser.add_argument("--scope-code", action="append", default=[])
    runner_scenario_parser.add_argument("--scope-workflow", action="append", default=[])
    runner_scenario_parser.add_argument("--scope-env", action="append", default=[])
    runner_scenario_parser.add_argument("--scope-actor", action="append", default=[])
    runner_scenario_parser.add_argument("--scope-time", action="append", default=[])
    runner_scenario_parser.add_argument("--checkpoint-hash", default="")
    runner_scenario_parser.add_argument("--checkpoint-message", default="")
    runner_scenario_parser.add_argument("--checkpoint-file", action="append", default=[])
    runner_scenario_parser.add_argument("--checkpoint-note", default="")
    runner_scenario_parser.add_argument("--expect-start", choices=["action-note", "quiet", "silent-skip"], default="")
    runner_scenario_parser.add_argument("--expect-memory", default="", help="Expected surfaced memory id, or 'none'.")
    runner_scenario_parser.add_argument(
        "--expect-candidate",
        choices=["candidate-saved", "candidate-not-saved", "skipped-no-reusable-learning", "not-run"],
        default="",
    )
    runner_scenario_parser.add_argument("--expect-checkpoint", choices=["checkpoint-linked", "checkpoint-not-linked", "not-run"], default="")
    runner_scenario_parser.add_argument("--strict", action="store_true", help="Exit non-zero when supplied expectations fail.")
    runner_scenario_parser.set_defaults(func=cmd_runner_scenario)

    fixture_repo_parser = subparsers.add_parser("fixture-repo-create", help="Create a local repository fixture with CMU memory and scenario data.")
    fixture_repo_parser.add_argument("--kind", choices=sorted(FIXTURE_KINDS), default="checkout-release")
    fixture_repo_parser.add_argument("--output", required=True, help="Empty or new directory where the fixture repository should be created.")
    fixture_repo_parser.set_defaults(func=cmd_fixture_repo_create)

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

    portable_validate_parser = subparsers.add_parser(
        "portable-validate",
        help="Validate a portable CMU bundle without importing it.",
    )
    portable_validate_parser.add_argument("bundle", help="Portable bundle JSON file.")
    portable_validate_parser.set_defaults(func=cmd_portable_validate)

    portable_compat_parser = subparsers.add_parser(
        "portable-compat",
        help="Run saved portable bundle compatibility fixtures.",
    )
    portable_compat_parser.add_argument("--fixture-dir", required=True, help="Directory containing valid-, invalid-, and future- portable bundle JSON fixtures.")
    portable_compat_parser.set_defaults(func=cmd_portable_compat)

    portable_seed_parser = subparsers.add_parser("portable-fixture-seed", help="Seed portable compatibility fixtures from the real CMU store.")
    portable_seed_parser.add_argument("--output", required=True, help="Directory to receive valid, invalid, future, and legacy bundle fixtures.")
    portable_seed_parser.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty fixture directory.")
    portable_seed_parser.add_argument("--historical", action="store_true", help="Also seed a historical current-schema export fixture derived from the real store.")
    portable_seed_parser.set_defaults(func=cmd_portable_fixture_seed)

    hardening_cycle_parser = subparsers.add_parser("hardening-cycle", help="Run the five-surface CMU product-hardening operator gate.")
    hardening_cycle_parser.add_argument("--portable-fixture-dir", default="", help="Directory containing portable compatibility fixtures.")
    hardening_cycle_parser.add_argument("--evidence-limit", type=int, default=20, help="Number of recent commits for the evidence monitor snapshot.")
    hardening_cycle_parser.add_argument("--evidence-hours", type=int, default=72, help="Maximum hours after a receipt to consider a commit.")
    hardening_cycle_parser.add_argument("--reminder-days", type=int, default=14, help="Review-reminder due window.")
    hardening_cycle_parser.add_argument("--strict", action="store_true", help="Exit non-zero unless all five hardening checks pass.")
    hardening_cycle_parser.set_defaults(func=cmd_hardening_cycle)

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

    scenario_compare_parser = subparsers.add_parser("scenario-compare", help="Compare saved scenarios against a baseline CMU root.")
    scenario_compare_parser.add_argument("--baseline-root", required=True, help="CMU root to use as the before/baseline store.")
    scenario_compare_parser.add_argument("scenario", nargs="?", default="", help="Scenario id or exact name. Omit to compare the library.")
    scenario_compare_parser.add_argument("--tag", default="", help="Compare scenarios with this tag.")
    scenario_compare_parser.add_argument(
        "--semantic",
        choices=["off", "local"],
        default="off",
        help="Enable an explicit semantic retrieval provider for both stores. Defaults to off.",
    )
    scenario_compare_parser.add_argument("--strict", action="store_true", help="Exit non-zero when a passing baseline regresses.")
    scenario_compare_parser.set_defaults(func=cmd_scenario_compare)

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

    doc_curate_parser = subparsers.add_parser(
        "doc-curate",
        help="Curate markdown history into Candidate Memory drafts through stale-doc gates.",
    )
    doc_curate_parser.add_argument("path", nargs="*", help="Markdown file or directory to curate. Defaults to the repository root.")
    doc_curate_parser.add_argument("--apply", action="store_true", help="Persist Candidate Memories that pass the curation gates.")
    doc_curate_parser.add_argument("--select", action="append", default=[], help="With --apply, persist only matching curation paths, titles, or candidate ids.")
    doc_curate_parser.add_argument("--stale-days", type=int, default=120, help="Reject documents older than this many days unless --allow-stale is used.")
    doc_curate_parser.add_argument("--allow-stale", action="store_true", help="Allow old documents to draft Candidate Memory when other gates pass.")
    doc_curate_parser.set_defaults(func=cmd_doc_curate)

    seed_plan_parser = subparsers.add_parser("seed-plan", help="Show a read-only memory seeding workbench.")
    seed_plan_parser.add_argument("--doc", action="append", default=[], help="Markdown file or directory to include as doc-curation preview evidence.")
    seed_plan_parser.add_argument("--stale-days", type=int, default=120, help="Doc-curation stale gate when --doc is used.")
    seed_plan_parser.add_argument("--allow-stale", action="store_true", help="Allow old docs into seed-plan curation preview.")
    seed_plan_parser.set_defaults(func=cmd_seed_plan)

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

    lifecycle_apply_parser = subparsers.add_parser("lifecycle-apply", help="Apply controlled lifecycle transitions when safe gates pass.")
    lifecycle_apply_parser.add_argument("--candidate-ready", action="store_true", help="Promote Candidate memories that already pass the Situation gate.")
    lifecycle_apply_parser.add_argument("--limit", type=int, default=50, help="Maximum Candidate memories to inspect.")
    lifecycle_apply_parser.add_argument("--apply", action="store_true", help="Persist eligible lifecycle transitions. Defaults to dry-run.")
    lifecycle_apply_parser.set_defaults(func=cmd_lifecycle_apply)

    lifecycle_proposals_parser = subparsers.add_parser("lifecycle-proposals", help="Generate assisted Situation -> Practice/Anchor proposal cards.")
    lifecycle_proposals_parser.add_argument("--target", choices=["all", "practice", "anchor"], default="all")
    lifecycle_proposals_parser.add_argument("--limit", type=int, default=50)
    lifecycle_proposals_parser.set_defaults(func=cmd_lifecycle_proposals)

    lifecycle_merge_parser = subparsers.add_parser("lifecycle-merge", help="Merge one memory into another through an explicit lifecycle action.")
    lifecycle_merge_parser.add_argument("--target", required=True, help="Memory id that should remain active.")
    lifecycle_merge_parser.add_argument("--source", required=True, help="Memory id to retire into the target.")
    lifecycle_merge_parser.add_argument("--reason", required=True)
    lifecycle_merge_parser.add_argument("--approved-by", required=True)
    lifecycle_merge_parser.add_argument("--apply", action="store_true", help="Persist the merge. Defaults to preview.")
    lifecycle_merge_parser.set_defaults(func=cmd_lifecycle_merge)

    lifecycle_demote_parser = subparsers.add_parser("lifecycle-demote", help="Demote a memory through an explicit lifecycle action.")
    lifecycle_demote_parser.add_argument("memory_id")
    lifecycle_demote_parser.add_argument("--reason", required=True)
    lifecycle_demote_parser.add_argument("--approved-by", default="", help="Required for stable memory demotion.")
    lifecycle_demote_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], default="")
    lifecycle_demote_parser.add_argument("--apply", action="store_true", help="Persist the demotion. Defaults to preview.")
    lifecycle_demote_parser.set_defaults(func=cmd_lifecycle_demote)

    lifecycle_archive_parser = subparsers.add_parser("lifecycle-archive", help="Archive retired memories into a durable local archive.")
    lifecycle_archive_parser.add_argument("--memory", default="", help="Retired memory id to archive. Defaults to all retired memories.")
    lifecycle_archive_parser.add_argument("--apply", action="store_true", help="Write .cmu/memory_archive.json. Defaults to preview.")
    lifecycle_archive_parser.set_defaults(func=cmd_lifecycle_archive)

    lifecycle_scope_record_parser = subparsers.add_parser("lifecycle-scope-record", help="Record a broad or ambiguous scope change as a Candidate review item.")
    lifecycle_scope_record_parser.add_argument("memory_id")
    lifecycle_scope_record_parser.add_argument("--reason", required=True)
    lifecycle_scope_record_parser.add_argument("--requested-by", required=True)
    lifecycle_scope_record_parser.add_argument("--scope-owner", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--scope-code", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--scope-workflow", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--scope-env", "--scope-environment", dest="scope_env", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--scope-actor", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--scope-time", action="append", default=[])
    lifecycle_scope_record_parser.add_argument("--apply", action="store_true", help="Persist the Candidate scope-change record. Defaults to preview.")
    lifecycle_scope_record_parser.set_defaults(func=cmd_lifecycle_scope_record)

    lifecycle_settle_parser = subparsers.add_parser("lifecycle-settle", help="Settle memories in current scope from Memory Gravity and linked-use evidence.")
    lifecycle_settle_parser.add_argument("--memory", default="", help="Limit settling to one memory id.")
    lifecycle_settle_parser.add_argument("--min-gravity", type=float, default=3.2)
    lifecycle_settle_parser.add_argument("--apply", action="store_true", help="Persist settling evidence. Defaults to preview.")
    lifecycle_settle_parser.set_defaults(func=cmd_lifecycle_settle)

    lifecycle_scope_suggest_parser = subparsers.add_parser("lifecycle-scope-suggest", help="Create Candidate scope-refinement proposals from receipt pressure.")
    lifecycle_scope_suggest_parser.add_argument("--memory", default="", help="Limit scope suggestions to one memory id.")
    lifecycle_scope_suggest_parser.add_argument("--apply", action="store_true", help="Persist Candidate scope-refinement records. Defaults to preview.")
    lifecycle_scope_suggest_parser.set_defaults(func=cmd_lifecycle_scope_suggest)

    gravity_parser = subparsers.add_parser("gravity", help="Show the read-only Memory Gravity placement/settling view.")
    gravity_parser.add_argument("--memory", default="", help="Limit gravity view to one memory id.")
    gravity_parser.set_defaults(func=cmd_gravity)

    governance_parser = subparsers.add_parser("governance", help="Show the read-only Practice/Anchor governance view.")
    governance_parser.add_argument("--memory", default="", help="Limit governance view to one stable memory id.")
    governance_parser.set_defaults(func=cmd_governance)

    review_queue_parser = subparsers.add_parser("review-queue", help="Show compact human approval and governance review cards.")
    review_queue_parser.set_defaults(func=cmd_review_queue)

    review_reminders_parser = subparsers.add_parser("review-reminders", help="Show lightweight stable-memory review and approval reminders.")
    review_reminders_parser.add_argument("--days", type=int, default=14, help="Due-soon authority review window in days.")
    review_reminders_parser.add_argument("--json", action="store_true", help="Render a machine-readable reminder delivery payload.")
    review_reminders_parser.set_defaults(func=cmd_review_reminders)

    reminder_delivery_parser = subparsers.add_parser("reminder-delivery", help="Write review reminder payloads to a local notification outbox.")
    reminder_delivery_parser.add_argument("--days", type=int, default=14, help="Due-soon authority review window in days.")
    reminder_delivery_parser.add_argument("--channel", default="local-jsonl", help="Logical delivery channel label.")
    reminder_delivery_parser.add_argument("--outbox", default="", help="Outbox JSONL path. Defaults to .cmu/reminder_outbox.jsonl.")
    reminder_delivery_parser.add_argument("--apply", action="store_true", help="Append a delivery event to the outbox. Defaults to preview.")
    reminder_delivery_parser.set_defaults(func=cmd_reminder_delivery)

    review_export_parser = subparsers.add_parser("review-export", help="Export review queue, owner handoffs, and reminders as structured JSON.")
    review_export_parser.add_argument("--days", type=int, default=14)
    review_export_parser.add_argument("--output", default=".cmu/review_export.json")
    review_export_parser.add_argument("--write", action="store_true", help="Write the JSON review payload. Defaults to preview.")
    review_export_parser.set_defaults(func=cmd_review_export)

    review_inbox_parser = subparsers.add_parser("review-inbox", help="Render a read-only non-CLI review inbox from live stores or review-export JSON.")
    review_inbox_parser.add_argument("--input", default="", help="Optional cmu-review-export/v1 JSON payload. Defaults to live stores.")
    review_inbox_parser.add_argument("--json", action="store_true", help="Render the inbox as JSON.")
    review_inbox_parser.set_defaults(func=cmd_review_inbox)

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

    team_scope_add_parser = subparsers.add_parser("team-scope-add", help="Add a local repo/team ownership boundary record.")
    team_scope_add_parser.add_argument("--repo", required=True)
    team_scope_add_parser.add_argument("--team", required=True)
    team_scope_add_parser.add_argument("--owner", required=True)
    team_scope_add_parser.add_argument("--code", action="append", default=[])
    team_scope_add_parser.add_argument("--workflow", action="append", default=[])
    team_scope_add_parser.add_argument("--env", "--environment", dest="environment", action="append", default=[])
    team_scope_add_parser.add_argument("--authority-role", choices=["agent", "member", "owner", "team", "org"], default="")
    team_scope_add_parser.add_argument("--consequence", choices=["low", "medium", "high", "critical"], default="")
    team_scope_add_parser.set_defaults(func=cmd_team_scope_add)

    team_scope_parser = subparsers.add_parser("team-scope", help="Inspect local repo/team ownership boundaries and memory coverage.")
    team_scope_parser.set_defaults(func=cmd_team_scope)

    team_handoff_parser = subparsers.add_parser("team-review-handoff", help="Show focused owner/team review handoff cards.")
    team_handoff_parser.set_defaults(func=cmd_team_review_handoff)

    team_action_parser = subparsers.add_parser("team-review-action", help="Apply a controlled owner/team handoff action.")
    team_action_parser.add_argument("subject_id", help="Memory id or team-scope id from team-review-handoff.")
    team_action_parser.add_argument(
        "--action",
        choices=["authority", "team-metadata", "challenge", "strengthen", "retire", "split", "narrow-scope"],
        required=True,
    )
    team_action_parser.add_argument("--owner", default="")
    team_action_parser.add_argument("--approved-by", default="")
    team_action_parser.add_argument("--approver-role", choices=["agent", "member", "owner", "team", "org"], default="")
    team_action_parser.add_argument("--consequence", choices=["low", "medium", "high", "critical"], default="")
    team_action_parser.add_argument("--review-due", default="")
    team_action_parser.add_argument("--mismatch", default="")
    team_action_parser.add_argument("--benefit", default="")
    team_action_parser.add_argument("--risk", default="")
    team_action_parser.add_argument("--rollback", default="")
    team_action_parser.add_argument("--challenged-by", default="")
    team_action_parser.add_argument("--evidence", action="append", default=[])
    team_action_parser.add_argument("--retirement-reason", default="")
    team_action_parser.add_argument("--split-title", default="")
    team_action_parser.add_argument("--split-summary", default="")
    team_action_parser.add_argument("--split-use-path", default="")
    team_action_parser.add_argument("--split-avoid", default="")
    team_action_parser.add_argument("--split-challenge", default="")
    team_action_parser.add_argument("--scope-owner", action="append", default=[])
    team_action_parser.add_argument("--scope-code", action="append", default=[])
    team_action_parser.add_argument("--scope-workflow", action="append", default=[])
    team_action_parser.add_argument("--scope-env", "--scope-environment", dest="scope_env", action="append", default=[])
    team_action_parser.add_argument("--scope-actor", action="append", default=[])
    team_action_parser.add_argument("--scope-time", action="append", default=[])
    team_action_parser.set_defaults(func=cmd_team_review_action)

    quality_parser = subparsers.add_parser("quality", help="Show the read-only Memory Quality and Decay Model.")
    quality_parser.add_argument("--memory", default="", help="Limit quality view to one memory id.")
    quality_parser.add_argument("--include-retired", action="store_true", help="Include retired memory history.")
    quality_parser.set_defaults(func=cmd_quality)

    readiness_parser = subparsers.add_parser("readiness", help="Show the operator cleanup/readiness workflow for the memory base.")
    readiness_parser.add_argument("--include-retired", action="store_true", help="Include retired memory history.")
    readiness_parser.set_defaults(func=cmd_readiness)

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

    evidence_monitor_parser = subparsers.add_parser("evidence-monitor", help="Monitor recent Git checkpoints and link only clean high-confidence receipt matches.")
    evidence_monitor_parser.add_argument("--limit", type=int, default=20, help="Number of recent commits to inspect.")
    evidence_monitor_parser.add_argument("--hours", type=int, default=72, help="Maximum hours after a receipt to consider a commit.")
    evidence_monitor_parser.add_argument("--min-score", type=float, default=DEFAULT_MONITOR_MIN_SCORE, help="Minimum auto-link score before monitor review.")
    evidence_monitor_parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MONITOR_MIN_CONFIDENCE,
        help="Minimum clean link confidence before applying.",
    )
    evidence_monitor_parser.add_argument("--apply", action="store_true", help="Persist high-confidence clean checkpoint links. Defaults to dry-run.")
    evidence_monitor_parser.set_defaults(func=cmd_evidence_monitor)

    evidence_session_parser = subparsers.add_parser("evidence-session", help="Run and optionally record a session-level evidence monitor pass.")
    evidence_session_parser.add_argument("--limit", type=int, default=20, help="Number of recent commits to inspect.")
    evidence_session_parser.add_argument("--hours", type=int, default=72, help="Maximum hours after a receipt to consider a commit.")
    evidence_session_parser.add_argument("--min-score", type=float, default=DEFAULT_MONITOR_MIN_SCORE, help="Minimum auto-link score before monitor review.")
    evidence_session_parser.add_argument("--min-confidence", type=float, default=DEFAULT_MONITOR_MIN_CONFIDENCE, help="Minimum clean link confidence before applying.")
    evidence_session_parser.add_argument("--apply", action="store_true", help="Persist high-confidence clean checkpoint links.")
    evidence_session_parser.add_argument("--record", action="store_true", help="Record the session summary under .cmu/evidence_sessions.json.")
    evidence_session_parser.set_defaults(func=cmd_evidence_session)

    evidence_watch_parser = subparsers.add_parser("evidence-watch", help="Run a bounded evidence-session watch loop for schedulers or hosts.")
    evidence_watch_parser.add_argument("--cycles", type=int, default=1, help="Number of evidence-session cycles to run.")
    evidence_watch_parser.add_argument("--interval", type=float, default=0.0, help="Seconds to wait between cycles.")
    evidence_watch_parser.add_argument("--limit", type=int, default=20)
    evidence_watch_parser.add_argument("--hours", type=int, default=72)
    evidence_watch_parser.add_argument("--min-score", type=float, default=DEFAULT_MONITOR_MIN_SCORE)
    evidence_watch_parser.add_argument("--min-confidence", type=float, default=DEFAULT_MONITOR_MIN_CONFIDENCE)
    evidence_watch_parser.add_argument("--apply", action="store_true", help="Apply clean high-confidence links in each cycle.")
    evidence_watch_parser.add_argument("--record", action="store_true", help="Record each session summary under .cmu/evidence_sessions.json.")
    evidence_watch_parser.set_defaults(func=cmd_evidence_watch)

    evidence_service_parser = subparsers.add_parser("evidence-service", help="Run a background evidence-session service loop until stopped.")
    evidence_service_parser.add_argument("--interval", type=float, default=60.0, help="Seconds to wait between service cycles.")
    evidence_service_parser.add_argument("--max-cycles", type=int, default=0, help="Optional cycle cap for supervised runs or tests. Default is unbounded.")
    evidence_service_parser.add_argument("--limit", type=int, default=20)
    evidence_service_parser.add_argument("--hours", type=int, default=72)
    evidence_service_parser.add_argument("--min-score", type=float, default=DEFAULT_MONITOR_MIN_SCORE)
    evidence_service_parser.add_argument("--min-confidence", type=float, default=DEFAULT_MONITOR_MIN_CONFIDENCE)
    evidence_service_parser.add_argument("--apply", action="store_true", help="Apply clean high-confidence links in each service cycle.")
    evidence_service_parser.add_argument("--no-session-record", action="store_true", help="Do not record individual evidence-session summaries.")
    evidence_service_parser.add_argument("--no-service-record", action="store_true", help="Do not record .cmu/evidence_service_runs.json.")
    evidence_service_parser.add_argument("--stop-file", default=".cmu/evidence_service.stop")
    evidence_service_parser.set_defaults(func=cmd_evidence_service)

    evidence_install_parser = subparsers.add_parser("evidence-service-install", help="Generate OS/service-manager wrapper files for cmu evidence-service.")
    evidence_install_parser.add_argument("--target", choices=["systemd-user", "windows-task", "launchd"], default="systemd-user")
    evidence_install_parser.add_argument("--output", default=".cmu/service-wrappers")
    evidence_install_parser.add_argument("--interval", type=float, default=60.0)
    evidence_install_parser.add_argument("--no-apply", action="store_true", help="Generate a dry-run evidence-service wrapper instead of applying clean links.")
    evidence_install_parser.add_argument("--no-record", action="store_true", help="Generate wrapper with service/session recording disabled.")
    evidence_install_parser.add_argument("--write", action="store_true", help="Write wrapper files. Defaults to preview.")
    evidence_install_parser.set_defaults(func=cmd_evidence_service_install)

    host_path_parser = subparsers.add_parser("host-path-suite", help="Run fixture-backed scenario, runner, Codex adapter, and compare host-path checks.")
    host_path_parser.add_argument("--work-dir", default="", help="Directory for generated fixture repositories. Defaults to a temporary directory.")
    host_path_parser.add_argument("--strict", action="store_true", help="Exit non-zero unless every host-path fixture passes.")
    host_path_parser.set_defaults(func=cmd_host_path_suite)

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


def cmd_demo_walkthrough(args: argparse.Namespace, store: MemoryStore) -> int:
    report = demo_walkthrough(args.root, apply=args.apply)
    print(report.render())
    return 0 if report.passed else 1


def cmd_setup_guide(args: argparse.Namespace, store: MemoryStore) -> int:
    report = setup_guide(args.root, host=args.host)
    print(report.render())
    return 0


def cmd_host_setup_manifest(args: argparse.Namespace, store: MemoryStore) -> int:
    report = host_setup_manifest(args.root, host=args.host, output=args.output, write=args.write)
    print(report.render())
    return 0


def cmd_host_examples(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = host_examples(args.root, host=args.host, output=args.output, write=args.write)
    except ValueError as error:
        raise SystemExit(f"host-examples failed: {error}") from error
    print(report.render())
    return 0


def cmd_install_check(args: argparse.Namespace, store: MemoryStore) -> int:
    report = install_check(args.root)
    print(report.render())
    return 0 if report.passed else 1


def cmd_dist_check(args: argparse.Namespace, store: MemoryStore) -> int:
    report = dist_check(
        args.root,
        python_executable=args.python or None,
        work_dir=args.work_dir or None,
        keep_work_dir=args.keep_work_dir,
    )
    print(report.render())
    return 0 if report.passed else 1


def cmd_mcp(args: argparse.Namespace, store: MemoryStore) -> int:
    return StdioMcpServer(CmuMcpAdapter(args.root)).serve_forever()


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


def cmd_runner_hooks(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    report = runner_hooks_report(
        args.root,
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
        semantic=args.semantic,
    )
    if args.json:
        payload = {"root": report.root, "manifest": report.manifest, "result": report.result.to_dict() if report.result else None}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(report.render())
    return 0 if report.result is None or report.result.ok else 1


def cmd_codex_runner(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.input and args.input_file:
        raise SystemExit("codex-runner accepts either --input or --input-file, not both")
    event = None
    if args.input or args.input_file:
        raw_input = args.input
        if args.input_file:
            if args.input_file == "-":
                raw_input = sys.stdin.read()
            else:
                raw_input = Path(args.input_file).read_text(encoding="utf-8-sig")
        try:
            event = json.loads(raw_input)
        except json.JSONDecodeError as error:
            raise SystemExit(f"codex-runner input must be valid JSON: {error.msg}") from error
    report = codex_runner_report(args.root, event)
    if args.json:
        payload = {"root": report.root, "manifest": report.manifest, "result": report.result.to_dict() if report.result else None}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(report.render())
    return 0 if report.result is None or report.result.ok else 1


def cmd_openai_runner(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.input and args.input_file:
        raise SystemExit("openai-runner accepts either --input or --input-file, not both")
    event = None
    if args.input or args.input_file:
        raw_input = args.input
        if args.input_file:
            if args.input_file == "-":
                raw_input = sys.stdin.read()
            else:
                raw_input = Path(args.input_file).read_text(encoding="utf-8-sig")
        try:
            event = json.loads(raw_input)
        except json.JSONDecodeError as error:
            raise SystemExit(f"openai-runner input must be valid JSON: {error.msg}") from error
    report = openai_runner_report(args.root, event)
    if args.json:
        payload = {"root": report.root, "manifest": report.manifest, "result": report.result.to_dict() if report.result else None}
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(report.render())
    return 0 if report.result is None or report.result.ok else 1


def cmd_runner_scenario(args: argparse.Namespace, store: MemoryStore) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("runner-scenario requires a task prompt")
    request = RunnerScenarioRequest(
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
        semantic=args.semantic,
        run_after_task=args.after_task or args.reusable_learning,
        reusable_learning=args.reusable_learning,
        title=args.title,
        situation=args.situation,
        signals=args.signal,
        outcome=args.outcome,
        worked=args.worked,
        failed=args.failed,
        future_use=args.future_use,
        evidence=args.evidence,
        liability_score=args.liability,
        suggested_next_type=args.suggested_next_type,
        confidence=args.confidence,
        scope={
            "ownership": args.scope_owner,
            "code": args.scope_code,
            "workflow": args.scope_workflow,
            "environment": args.scope_env,
            "actor": args.scope_actor,
            "time": args.scope_time,
        },
        checkpoint_hash=args.checkpoint_hash,
        checkpoint_message=args.checkpoint_message,
        checkpoint_files=args.checkpoint_file,
        checkpoint_note=args.checkpoint_note,
        expect_start=args.expect_start,
        expect_memory=args.expect_memory,
        expect_candidate=args.expect_candidate,
        expect_checkpoint=args.expect_checkpoint,
    )
    report = run_runner_scenario(args.root, request)
    print(report.render())
    if args.strict and not report.passed:
        return 1
    return 0


def cmd_fixture_repo_create(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = create_fixture_repo(args.kind, args.output)
    except ValueError as error:
        raise SystemExit(f"fixture-repo-create failed: {error}") from error
    print(report.render())
    return 0


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


def cmd_portable_validate(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        bundle = load_portable_bundle(args.bundle)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"portable-validate failed: {error}") from error
    report = validate_portable_bundle(bundle)
    print(report.render())
    return 0 if report.valid else 1


def cmd_portable_compat(args: argparse.Namespace, store: MemoryStore) -> int:
    report = portable_compat_report(args.fixture_dir)
    print(report.render())
    return 0 if report.passed else 1


def cmd_portable_fixture_seed(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = seed_portable_fixtures(args.root, args.output, overwrite=args.overwrite, include_historical=args.historical)
    except ValueError as error:
        raise SystemExit(f"portable-fixture-seed failed: {error}") from error
    print(report.render())
    return 0


def cmd_hardening_cycle(args: argparse.Namespace, store: MemoryStore) -> int:
    report = hardening_cycle_report(
        args.root,
        store.list(),
        MemoryUseStore(args.root).list(),
        team_scopes=TeamDirectoryStore(args.root).list(),
        portable_fixture_dir=args.portable_fixture_dir or None,
        evidence_limit=args.evidence_limit,
        evidence_hours=args.evidence_hours,
        reminder_days=args.reminder_days,
    )
    print(report.render())
    return 1 if args.strict and not report.passed else 0


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


def cmd_scenario_compare(args: argparse.Namespace, store: MemoryStore) -> int:
    library = ScenarioLibraryStore(args.root)
    if args.scenario:
        scenarios = [library.get(args.scenario)]
        tag = ""
    else:
        scenarios = library.list(tag=args.tag)
        tag = args.tag
    baseline_root = Path(args.baseline_root)
    baseline_store = MemoryStore(baseline_root)
    baseline_memories = baseline_store.list()
    current_memories = store.list()
    baseline_semantic_index = load_semantic_index(argparse.Namespace(root=baseline_root, semantic=args.semantic), baseline_memories)
    current_semantic_index = load_semantic_index(args, current_memories)
    report = compare_scenario_library(
        scenarios,
        baseline_memories=baseline_memories,
        baseline_receipts=MemoryUseStore(baseline_root).list(),
        current_memories=current_memories,
        current_receipts=MemoryUseStore(args.root).list(),
        baseline_root=str(baseline_root),
        current_root=str(args.root),
        baseline_semantic_index=baseline_semantic_index,
        current_semantic_index=current_semantic_index,
        tag=tag,
    )
    print(report.render())
    if args.strict and report.has_regressions():
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


def cmd_doc_curate(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.stale_days < 0:
        raise SystemExit("doc-curate --stale-days must be zero or greater")
    memories = store.list()
    decisions = curate_documents(
        args.root,
        args.path,
        memories,
        stale_days=args.stale_days,
        allow_stale=args.allow_stale,
    )
    if args.apply:
        for memory in apply_selected_curation_decisions(decisions, args.select):
            store.add(memory)
    print(
        DocumentCurationReport(
            decisions=decisions,
            apply=args.apply,
            stale_days=args.stale_days,
            selected=args.select,
        ).render()
    )
    return 0


def cmd_seed_plan(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.stale_days < 0:
        raise SystemExit("seed-plan --stale-days must be zero or greater")
    memories = store.list()
    doc_decisions = (
        curate_documents(
            args.root,
            args.doc,
            memories,
            stale_days=args.stale_days,
            allow_stale=args.allow_stale,
        )
        if args.doc
        else []
    )
    report = seed_plan_report(
        memories,
        MemoryUseStore(args.root).list(),
        doc_decisions=doc_decisions,
    )
    print(report.render())
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


def cmd_lifecycle_apply(args: argparse.Namespace, store: MemoryStore) -> int:
    if not args.candidate_ready:
        raise SystemExit("lifecycle-apply currently requires --candidate-ready")
    memories = store.list()
    report = apply_lifecycle_candidates(memories, apply=args.apply, limit=args.limit)
    if args.apply:
        for item in report.items:
            if item.status == "promoted":
                store.update(find_memory(memories, item.memory_id))
    print(report.render())
    return 0


def cmd_lifecycle_proposals(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_proposals(store.list(), target=args.target, limit=args.limit)
    print(report.render())
    return 0


def cmd_lifecycle_merge(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_merge(
        store.list(),
        target_id=args.target,
        source_id=args.source,
        reason=args.reason,
        approved_by=args.approved_by,
        apply=args.apply,
    )
    for memory in report.changed_memories:
        store.update(memory)
    print(report.render())
    return 0 if report.ok else 1


def cmd_lifecycle_demote(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_demote(
        store.list(),
        memory_id=args.memory_id,
        reason=args.reason,
        approved_by=args.approved_by,
        approver_role=args.approver_role,
        apply=args.apply,
    )
    for memory in report.changed_memories:
        store.update(memory)
    print(report.render())
    return 0 if report.ok else 1


def cmd_lifecycle_archive(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = [*store.list(), *store.list(status=MemoryStatus.RETIRED)]
    report = lifecycle_archive(memories, root=args.root, memory_id=args.memory, apply=args.apply)
    print(report.render())
    return 0 if report.ok else 1


def cmd_lifecycle_scope_record(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_scope_record(
        store.list(),
        memory_id=args.memory_id,
        proposed_scope=MemoryScope(
            ownership=args.scope_owner,
            code=args.scope_code,
            workflow=args.scope_workflow,
            environment=args.scope_env,
            actor=args.scope_actor,
            time=args.scope_time,
        ),
        reason=args.reason,
        requested_by=args.requested_by,
        apply=args.apply,
    )
    for memory in report.created_memories:
        store.add(memory)
    print(report.render())
    return 0 if report.ok else 1


def cmd_lifecycle_settle(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_settle(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
        min_gravity=args.min_gravity,
        apply=args.apply,
    )
    for memory in report.changed_memories:
        store.update(memory)
    print(report.render())
    return 0 if report.ok else 1


def cmd_lifecycle_scope_suggest(args: argparse.Namespace, store: MemoryStore) -> int:
    report = lifecycle_scope_suggestions(
        store.list(),
        MemoryUseStore(args.root).list(),
        memory_id=args.memory,
        apply=args.apply,
    )
    for memory in report.created_memories:
        store.add(memory)
    print(report.render())
    return 0 if report.ok else 1


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


def cmd_review_queue(args: argparse.Namespace, store: MemoryStore) -> int:
    report = review_queue(store.list(), MemoryUseStore(args.root).list(), TeamDirectoryStore(args.root).list())
    print(report.render())
    return 0


def cmd_review_reminders(args: argparse.Namespace, store: MemoryStore) -> int:
    report = review_reminders(
        store.list(),
        MemoryUseStore(args.root).list(),
        team_scopes=TeamDirectoryStore(args.root).list(),
        days=args.days,
    )
    if args.json:
        print(json.dumps(report.to_delivery_payload(), indent=2, sort_keys=True))
    else:
        print(report.render())
    return 0


def cmd_reminder_delivery(args: argparse.Namespace, store: MemoryStore) -> int:
    reminders = review_reminders(
        store.list(),
        MemoryUseStore(args.root).list(),
        team_scopes=TeamDirectoryStore(args.root).list(),
        days=args.days,
    )
    report = deliver_reminders_to_outbox(
        reminders,
        root=args.root,
        channel=args.channel,
        outbox=args.outbox or None,
        apply=args.apply,
    )
    print(report.render())
    return 0


def cmd_review_export(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    receipts = MemoryUseStore(args.root).list()
    team_scopes = TeamDirectoryStore(args.root).list()
    report = export_review_payload(
        root=args.root,
        output=args.output,
        queue=review_queue(memories, receipts, team_scopes),
        handoffs=team_review_handoffs(memories, team_scopes),
        reminders=review_reminders(memories, receipts, team_scopes=team_scopes, days=args.days),
        write=args.write,
    )
    print(report.render())
    return 0


def cmd_review_inbox(args: argparse.Namespace, store: MemoryStore) -> int:
    if args.input:
        try:
            report = review_inbox_from_export(args.input)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"review-inbox failed: {error}") from error
    else:
        memories = store.list()
        receipts = MemoryUseStore(args.root).list()
        team_scopes = TeamDirectoryStore(args.root).list()
        report = review_inbox_from_reports(
            root=args.root,
            queue=review_queue(memories, receipts, team_scopes),
            handoffs=team_review_handoffs(memories, team_scopes),
            reminders=review_reminders(memories, receipts, team_scopes=team_scopes),
        )
    print(report.to_json() if args.json else report.render())
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


def cmd_team_scope_add(args: argparse.Namespace, store: MemoryStore) -> int:
    record = TeamScopeRecord.create(
        repo=args.repo,
        team=args.team,
        owner=args.owner,
        code=args.code,
        workflow=args.workflow,
        environment=args.environment,
        authority_role=args.authority_role,
        consequence=args.consequence,
    )
    TeamDirectoryStore(args.root).add(record)
    print("CMU Team Scope Added")
    print(record.render_summary())
    return 0


def cmd_team_scope(args: argparse.Namespace, store: MemoryStore) -> int:
    records = TeamDirectoryStore(args.root).list()
    report = team_directory_report(records, store.list())
    print(report.render())
    return 0


def cmd_team_review_handoff(args: argparse.Namespace, store: MemoryStore) -> int:
    report = team_review_handoffs(store.list(), TeamDirectoryStore(args.root).list())
    print(report.render())
    return 0


def cmd_team_review_action(args: argparse.Namespace, store: MemoryStore) -> int:
    report = apply_team_review_action(
        args.root,
        args.subject_id,
        action=args.action,
        owner=args.owner,
        approved_by=args.approved_by,
        approver_role=args.approver_role,
        consequence=args.consequence,
        review_due=args.review_due,
        mismatch=args.mismatch,
        benefit=args.benefit,
        risk=args.risk,
        rollback=args.rollback,
        challenged_by=args.challenged_by,
        evidence=args.evidence,
        retirement_reason=args.retirement_reason,
        split_title=args.split_title,
        split_summary=args.split_summary,
        split_use_path=args.split_use_path,
        split_avoid=args.split_avoid,
        split_challenge=args.split_challenge,
        scope=MemoryScope(
            ownership=args.scope_owner,
            code=args.scope_code,
            workflow=args.scope_workflow,
            environment=args.scope_env,
            actor=args.scope_actor,
            time=args.scope_time,
        ),
    )
    print(report.render())
    return 0 if report.applied else 1


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


def cmd_readiness(args: argparse.Namespace, store: MemoryStore) -> int:
    memories = store.list()
    if args.include_retired:
        memories.extend(store.list(status=MemoryStatus.RETIRED))
    print(
        readiness_report(
            memories,
            MemoryUseStore(args.root).list(),
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


def cmd_evidence_monitor(args: argparse.Namespace, store: MemoryStore) -> int:
    use_store = MemoryUseStore(args.root)
    report = monitor_checkpoints(
        args.root,
        store.list(),
        use_store.list(),
        limit=args.limit,
        hours=args.hours,
        min_score=args.min_score,
        min_confidence=args.min_confidence,
        apply=args.apply,
    )
    print(report.render())
    return 0 if not report.error else 1


def cmd_evidence_session(args: argparse.Namespace, store: MemoryStore) -> int:
    report = run_evidence_session(
        args.root,
        store.list(),
        MemoryUseStore(args.root).list(),
        limit=args.limit,
        hours=args.hours,
        min_score=args.min_score,
        min_confidence=args.min_confidence,
        apply=args.apply,
        record=args.record,
    )
    print(report.render())
    return 0 if report.ok else 1


def cmd_evidence_watch(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = run_evidence_watch(
            args.root,
            store.list(),
            MemoryUseStore(args.root).list(),
            cycles=args.cycles,
            interval_seconds=args.interval,
            limit=args.limit,
            hours=args.hours,
            min_score=args.min_score,
            min_confidence=args.min_confidence,
            apply=args.apply,
            record=args.record,
        )
    except ValueError as error:
        raise SystemExit(f"evidence-watch failed: {error}") from error
    print(report.render())
    return 0 if report.ok else 1


def cmd_evidence_service(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = run_evidence_service(
            args.root,
            store.list(),
            MemoryUseStore(args.root).list(),
            interval_seconds=args.interval,
            max_cycles=args.max_cycles,
            limit=args.limit,
            hours=args.hours,
            min_score=args.min_score,
            min_confidence=args.min_confidence,
            apply=args.apply,
            record_sessions=not args.no_session_record,
            record_service=not args.no_service_record,
            stop_file=args.stop_file,
        )
    except ValueError as error:
        raise SystemExit(f"evidence-service failed: {error}") from error
    print(report.render())
    return 0


def cmd_evidence_service_install(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        report = evidence_service_install(
            args.root,
            target=args.target,
            output=args.output,
            interval_seconds=args.interval,
            apply=not args.no_apply,
            record=not args.no_record,
            write=args.write,
        )
    except ValueError as error:
        raise SystemExit(f"evidence-service-install failed: {error}") from error
    print(report.render())
    return 0


def cmd_host_path_suite(args: argparse.Namespace, store: MemoryStore) -> int:
    report = run_host_path_suite(args.work_dir or None, keep=bool(args.work_dir))
    print(report.render())
    return 1 if args.strict and not report.passed else 0


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
