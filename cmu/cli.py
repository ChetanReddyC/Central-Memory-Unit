from __future__ import annotations

import argparse
from pathlib import Path

from .challenges import ChallengeRequest, ResolveChallengeRequest, challenge_stable_memory, resolve_challenge
from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryType
from .onboarding import build_onboarding_seed
from .promotion import promote_memory, review_promotion
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
from .store import MemoryStore
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
    semantic_audit,
    semantic_audit_recommendations,
    use_review,
    use_summary,
    use_threshold_report,
)


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

    promote_parser = subparsers.add_parser("promote", help="Promote memory after gates pass.")
    promote_parser.add_argument("memory_id", help="Memory id to promote.")
    promote_parser.add_argument(
        "--to",
        choices=[MemoryType.SITUATION.value, MemoryType.ANCHOR.value, MemoryType.PRACTICE.value],
        default=MemoryType.SITUATION.value,
    )
    promote_parser.add_argument("--approved-by", default="", help="Owner or team approving stable Practice/Anchor promotion.")
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
    semantic_audit_parser.set_defaults(func=cmd_semantic_audit)

    return parser


def cmd_init(args: argparse.Namespace, store: MemoryStore) -> int:
    path = store.init()
    print(f"Initialized CMU store at {path}")
    return 0


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


def cmd_promote(args: argparse.Namespace, store: MemoryStore) -> int:
    decision = promote_memory(store.list(), args.memory_id, MemoryType(args.to), approved_by=args.approved_by)
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
    if args.recommendations:
        if args.memory:
            raise SystemExit("semantic-audit --recommendations reviews all memories and does not accept --memory")
        report = semantic_audit_recommendations(use_store.list(), store.list())
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
