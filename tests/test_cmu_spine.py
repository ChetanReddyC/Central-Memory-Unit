import json
import os
import subprocess
import sys
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from cmu.agent_api import AGENT_API_VERSION, AgentIntegration
from cmu.authority import authority_card, authority_report, set_memory_authority
from cmu.challenges import ChallengeRequest, ResolveChallengeRequest, challenge_stable_memory, resolve_challenge
from cmu.cli import main
from cmu.codex_adapter import CODEX_RUNNER_ADAPTER_VERSION, CodexRunnerAdapter, codex_runner_report
from cmu.copilot_adapter import COPILOT_RUNNER_ADAPTER_VERSION, CopilotRunnerAdapter, copilot_runner_report
from cmu.demo_walkthrough import demo_walkthrough
from cmu.dist_check import dist_check
from cmu.evidence_monitor import monitor_checkpoints
from cmu.evidence_service import run_evidence_service
from cmu.evidence_service_install import evidence_service_install
from cmu.evidence_session import run_evidence_session
from cmu.evidence_watch import run_evidence_watch
from cmu.fixture_repos import create_fixture_repo
from cmu.hardening_cycle import hardening_cycle_report
from cmu.host_examples import host_examples
from cmu.host_path_suite import run_host_path_suite
from cmu.host_setup_manifest import host_setup_manifest
from cmu.ide_workflow import ide_workflow
from cmu.install_check import REQUIRED_README_COMMANDS, REQUIRED_SCRIPTS, install_check
from cmu.lifecycle_settling import lifecycle_scope_suggestions, lifecycle_settle
from cmu.mcp import MCP_SERVER_NAME, CmuMcpAdapter, mcp_tool_definitions
from cmu.mcp_setup_check import mcp_setup_check
from cmu.models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from cmu.onboarding import NORMAL_SEED_WORD_LIMIT, build_onboarding_seed, word_count
from cmu.openai_adapter import OPENAI_RUNNER_ADAPTER_VERSION, OpenAIRunnerAdapter, openai_runner_report
from cmu.portable import PORTABLE_BUNDLE_VERSION, export_bundle_from_root, import_portable_bundle, validate_portable_bundle
from cmu.portable_compat import portable_compat_report
from cmu.portable_fixture_seed import seed_portable_fixtures
from cmu.promotion import promote_memory, review_promotion
from cmu.product_console import product_console_report
from cmu.publish_check import publish_check
from cmu.quality import apply_decay_action, quality_card, quality_report
from cmu.readiness import readiness_report
from cmu.remembering import RememberRequest, remember_candidate
from cmu.retrieval_metrics import (
    retrieval_benchmark_report,
    retrieval_metrics_report,
    seed_retrieval_evaluation_cases,
)
from cmu.retrieval import (
    HashingEmbeddingProvider,
    InMemorySemanticIndex,
    Match,
    PersistentSemanticIndex,
    PreflightQuery,
    SemanticSignal,
    preflight,
    rank_memories,
)
from cmu.review_queue import review_queue
from cmu.review_export import export_review_payload
from cmu.review_inbox import review_inbox_from_export, review_inbox_from_reports
from cmu.review_reminders import review_reminders
from cmu.reminder_delivery import deliver_reminders_to_outbox
from cmu.reminder_dispatch import dispatch_reminder_outbox
from cmu.runner_hooks import RUNNER_HOOKS_VERSION, AutonomousRunnerHooks, runner_hooks_report
from cmu.runner_scenarios import RUNNER_SCENARIO_VERSION, RunnerScenarioRequest, run_runner_scenario
from cmu.scenarios import ScenarioDefinition, ScenarioLibraryStore, compare_scenario_library
from cmu.scenarios import compare_scenario_library_to_no_memory
from cmu.sdk import CentralMemoryUnit
from cmu.setup import setup_guide
from cmu.store import MemoryStore
from cmu.team_directory import TeamDirectoryStore, TeamScopeRecord, team_directory_report
from cmu.team_review_action import apply_team_review_action
from cmu.team_review_handoff import team_review_handoffs
from cmu.traces import RawTraceStore
from cmu.triggers import decide_trigger
from cmu.usage import (
    CommitLinkRequest,
    MemoryUseReceipt,
    MemoryUseStore,
    format_git_metadata_error,
    inspect_git_commit,
    link_commit,
    use_summary,
    usage_adjustment,
)


class MemoryStoreTests(unittest.TestCase):
    def test_store_round_trips_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Migration order matters",
                summary="Apply schema migrations before service rollout.",
                signals=["migration", "deploy"],
                scope=MemoryScope(code=["billing migrations"]),
            )

            store.add(memory)

            loaded = store.list()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].title, "Migration order matters")

    def test_store_round_trips_memory_relationships(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use retry budget",
                summary="Retries should respect an explicit budget.",
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Webhook timeout root cause",
                summary="Webhook timeout debugging revealed the retry budget practice.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="This situation teaches the retry budget practice.",
                    )
                ],
            )

            store.add(practice)
            store.add(situation)

            loaded = {memory.id: memory for memory in store.list()}
            self.assertEqual(loaded[situation.id].relationships[0].type, MemoryRelationType.RELATED_PRACTICE)
            self.assertEqual(loaded[situation.id].relationships[0].target_id, practice.id)

    def test_cli_relate_adds_memory_relationship(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use retry budget",
                summary="Retries should respect an explicit budget.",
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Webhook timeout root cause",
                summary="Webhook timeout debugging revealed the retry budget practice.",
            )
            store.add(practice)
            store.add(situation)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "relate",
                        situation.id,
                        "--type",
                        "related_practice",
                        "--target",
                        practice.id,
                        "--reason",
                        "Timeout debugging should lead to the retry budget practice.",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Memory Relationship Applied", output.getvalue())
            loaded = {memory.id: memory for memory in MemoryStore(tmp).list()}
            self.assertEqual(loaded[situation.id].relationships[0].target_id, practice.id)
            self.assertEqual(loaded[situation.id].relationships[0].type, MemoryRelationType.RELATED_PRACTICE)

    def test_cli_relations_shows_outgoing_and_incoming_links(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use retry budget",
                summary="Retries should respect an explicit budget.",
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Webhook timeout root cause",
                summary="Webhook timeout debugging revealed the retry budget practice.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="This situation teaches the retry budget practice.",
                    )
                ],
            )
            store.add(practice)
            store.add(situation)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "relations", practice.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("CMU Memory Relationships", rendered)
            self.assertIn("Incoming:", rendered)
            self.assertIn("related_practice <-", rendered)
            self.assertIn(situation.title, rendered)

    def test_cli_add_can_store_approved_stable_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "add",
                        "--type",
                        "anchor",
                        "--title",
                        "Credential rotation lock ordering",
                        "--summary",
                        "Credential rotation must hold the lock before updating active secrets.",
                        "--scope-code",
                        "auth",
                        "--scope-workflow",
                        "credential rotation",
                        "--scope-actor",
                        "agent",
                        "--approved-by",
                        "security owner",
                    ]
                )

            self.assertEqual(exit_code, 0)
            anchors = MemoryStore(tmp).list(type=MemoryType.ANCHOR)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0].approved_by, "security owner")

    def test_cli_add_rejects_unapproved_stable_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--root",
                        tmp,
                        "add",
                        "--type",
                        "anchor",
                        "--title",
                        "Credential rotation lock ordering",
                        "--summary",
                        "Credential rotation must hold the lock before updating active secrets.",
                    ]
                )

            self.assertIn("requires --approved-by", str(raised.exception))
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.ANCHOR), [])

    def test_memory_store_preserves_concurrent_adds(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)

            def add_memory(index: int) -> str:
                memory = Memory.create(
                    type=MemoryType.SITUATION,
                    title=f"Concurrent situation {index}",
                    summary=f"Concurrent memory write {index}",
                    evidence=[f"Evidence {index}"],
                    scope=MemoryScope(code=["cmu"]),
                    challenge_only_if="The concurrent write no longer matters.",
                    use_this_path="Keep every concurrent memory.",
                )
                store.add(memory)
                return memory.id

            with ThreadPoolExecutor(max_workers=8) as executor:
                expected_ids = set(executor.map(add_memory, range(24)))

            loaded_ids = {memory.id for memory in MemoryStore(tmp).list()}
            self.assertEqual(loaded_ids, expected_ids)


class GraphMemoryViewTests(unittest.TestCase):
    def test_cli_graph_traverses_multi_hop_paths_and_marks_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback repeats stale release marker",
                summary="Checkout rollback retried against stale release marker state.",
            )
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Verify release marker before deployment retry",
                summary="Deployment retries must verify release marker state.",
                approved_by="Release owner",
            )
            exception = Memory.create(
                type=MemoryType.EXCEPTION,
                title="Marker check can be skipped for dry-run rollback",
                summary="Dry-run rollback does not write release marker state.",
            )
            anti_pattern = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Blindly rerun deployment rollback",
                summary="Blind rollback retries can hide stale marker state.",
            )
            situation.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                    reason="The failed rollback teaches the marker-check practice.",
                )
            )
            practice.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.SUPPORTS,
                    target_id=situation.id,
                    reason="The practice points back to the incident evidence.",
                )
            )
            exception.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.EXCEPTION_TO,
                    target_id=practice.id,
                    reason="Dry-run rollback does not mutate the marker.",
                )
            )
            anti_pattern.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.CHALLENGES,
                    target_id=practice.id,
                    reason="Blind retry bypasses the required marker inspection.",
                )
            )
            for memory in [situation, practice, exception, anti_pattern]:
                store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "graph", situation.id, "--depth", "4"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Graph Memory View", rendered)
            self.assertIn("Mode: read-only graph path proof", rendered)
            self.assertIn(f"- {situation.id} [situation/active] {situation.title}", rendered)
            self.assertIn(f"-> related_practice: {practice.id} [practice/active] {practice.title}", rendered)
            self.assertIn(f"-> supports: {situation.id} [situation/active] {situation.title} [cycle/reference]", rendered)
            self.assertIn(f"<- exception_to: {exception.id} [exception/active] {exception.title}", rendered)
            self.assertIn(f"<- challenges: {anti_pattern.id} [anti-pattern/active] {anti_pattern.title}", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_graph_global_summary_reports_components_isolates_and_dangling_links(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback marker mismatch",
                summary="Rollback marker state was stale.",
            )
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Inspect release markers",
                summary="Inspect release markers before rollback retries.",
                approved_by="Release owner",
            )
            question = Memory.create(
                type=MemoryType.QUESTION,
                title="Does billing rollback share release marker state?",
                summary="Billing marker ownership remains unknown.",
            )
            anti_pattern = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Retry against a deleted practice",
                summary="A stale relationship should be repaired before use.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.CHALLENGES,
                        target_id="mem_deletedpractice",
                        reason="The original practice was retired outside this fixture.",
                    )
                ],
            )
            situation.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                    reason="The incident teaches marker inspection.",
                )
            )
            for memory in [situation, practice, question, anti_pattern]:
                store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "graph"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("- Memories: 4", rendered)
            self.assertIn("- Relationships: 2", rendered)
            self.assertIn("- Connected Components: 3", rendered)
            self.assertIn("- Connected Memories: 2", rendered)
            self.assertIn("- Isolated Memories: 2", rendered)
            self.assertIn("- Dangling Relationships: 1", rendered)
            self.assertIn(f"- {question.id} [question/active] {question.title}", rendered)
            self.assertIn(f"- {anti_pattern.id} [anti-pattern/active] {anti_pattern.title}", rendered)
            self.assertIn("mem_deletedpractice [missing]", rendered)
            self.assertIn("repair dangling relationships", rendered)

    def test_cli_graph_include_retired_surfaces_resolved_question_history(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            question = Memory.create(
                type=MemoryType.QUESTION,
                title="Does checkout rollback share release marker state?",
                summary="Checkout rollback marker ownership remains unresolved.",
                scope=MemoryScope(ownership=["Release owner"], code=["checkout"], workflow=["deployment"]),
                evidence=["Logs refer to release_marker_id."],
            )
            store.add(question)
            with redirect_stdout(StringIO()):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "resolve-question",
                        question.id,
                        "--outcome",
                        "situation",
                        "--answer",
                        "Checkout rollback shares the deployment release marker.",
                        "--resolved-by",
                        "Release owner",
                        "--evidence",
                        "Code inspection found the same release_marker_id read and write path.",
                    ]
                )
            self.assertEqual(exit_code, 0)
            situation = store.list(type=MemoryType.SITUATION)[0]

            active_only = StringIO()
            with redirect_stdout(active_only):
                active_exit = main(["--root", tmp, "graph", situation.id])
            self.assertEqual(active_exit, 0)
            self.assertIn(f"-> derived_from: {question.id} [missing]", active_only.getvalue())
            self.assertIn("- Dangling Relationships: 1", active_only.getvalue())

            history = StringIO()
            with redirect_stdout(history):
                history_exit = main(["--root", tmp, "graph", situation.id, "--include-retired"])
            history_rendered = history.getvalue()
            self.assertEqual(history_exit, 0)
            self.assertIn("History: active + retired", history_rendered)
            self.assertIn(f"-> derived_from: {question.id} [question/retired] {question.title}", history_rendered)
            self.assertIn("- Dangling Relationships: 0", history_rendered)


class PreflightTests(unittest.TestCase):
    def test_preflight_returns_action_note_for_relevant_memory(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            signals=["migration", "deploy"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            evidence=["Previous deploy failed when service rolled out first."],
            use_this_path="Check migration order before changing deployment code.",
            avoid_this="Do not roll out service code before confirming schema compatibility.",
            challenge_only_if="The deployment path no longer touches the billing schema.",
            liability_score=5,
            confidence=0.9,
        )

        note = preflight(
            [memory],
            PreflightQuery(
                prompt="Fix billing deployment migration failure",
                actor="agent",
                area="billing",
                files=["billing/deploy.py"],
                risk="high",
            ),
        )

        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("Do migration before deploy", note.render())

    def test_preflight_stays_quiet_when_irrelevant(self) -> None:
        memory = Memory.create(
            type=MemoryType.SITUATION,
            title="Auth token rotation",
            summary="Token rotation has a lock ordering constraint.",
            signals=["auth", "token"],
            scope=MemoryScope(code=["auth"]),
            liability_score=4,
        )

        note = preflight(
            [memory],
            PreflightQuery(prompt="Change CSS spacing on settings page", risk="low"),
        )

        self.assertIsNone(note)

    def test_rank_memories_expands_direct_graph_relationship_after_grounded_match(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use retry budget",
            summary="Outbound attempts should respect bounded failure handling.",
            use_this_path="Check the retry budget before changing retry behavior.",
            scope=MemoryScope(code=["billing/reliability"], workflow=["resilience"], actor=["agent"]),
            liability_score=4,
            confidence=0.8,
        )
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeouts came from unbounded retries during dependency failures.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"], workflow=["debugging"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                    reason="Timeout debugging should lead to the retry budget practice.",
                )
            ],
            liability_score=3,
            confidence=0.75,
        )

        matches = rank_memories(
            [situation, practice],
            PreflightQuery(
                prompt="Investigate billing webhook timeout",
                actor="agent",
                area="billing",
                files=["billing/webhook.py"],
                risk="medium",
            ),
        )

        graph_match = next(match for match in matches if match.memory.id == practice.id)
        self.assertIn("graph:related_practice", graph_match.matched_terms)
        self.assertEqual(graph_match.graph_source_id, situation.id)
        self.assertEqual(graph_match.graph_source_title, "Webhook timeout root cause")
        self.assertEqual(graph_match.graph_relation_type, "related_practice")
        self.assertEqual(graph_match.graph_relation_reason, "Timeout debugging should lead to the retry budget practice.")
        self.assertGreaterEqual(graph_match.score, 1.6)

    def test_cli_preflight_explains_graph_expanded_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use retry budget",
                summary="Outbound attempts should respect bounded failure handling.",
                use_this_path="Check the retry budget before changing retry behavior.",
                scope=MemoryScope(code=["billing/reliability"], workflow=["resilience"], actor=["agent"]),
                liability_score=4,
                confidence=0.8,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Webhook timeout root cause",
                summary="Webhook timeouts came from unbounded retries during dependency failures.",
                signals=["webhook", "timeout", "retries"],
                scope=MemoryScope(code=["billing/webhook.py"], workflow=["debugging"]),
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="Timeout debugging should lead to the retry budget practice.",
                    )
                ],
                liability_score=3,
                confidence=0.75,
            )
            store.add(practice)
            store.add(situation)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Investigate billing webhook timeout",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--file",
                        "billing/webhook.py",
                        "--risk",
                        "medium",
                        "--show-matches",
                    ]
                )

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn(f"match", rendered)
            self.assertIn("score:", rendered)
            self.assertIn("text overlap:", rendered)
            self.assertIn("semantic signal: unavailable -> +0.00", rendered)
            self.assertIn("hard scope signals", rendered)
            self.assertIn("graph link: related_practice", rendered)
            self.assertIn(f"via: {situation.id} Webhook timeout root cause", rendered)
            self.assertIn("relation: related_practice", rendered)
            self.assertIn("reason: Timeout debugging should lead to the retry budget practice.", rendered)

    def test_rank_memories_records_explainable_score_breakdown(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            signals=["migration", "deploy"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )

        matches = rank_memories(
            [memory],
            PreflightQuery(
                prompt="Fix billing deployment migration failure",
                actor="agent",
                area="billing",
                files=["billing/deploy.py"],
                workflow=["deployment"],
                risk="high",
            ),
        )

        self.assertEqual(len(matches), 1)
        breakdown = "\n".join(matches[0].score_breakdown)
        self.assertIn("text overlap:", breakdown)
        self.assertIn("semantic signal: unavailable -> +0.00", breakdown)
        self.assertIn("hard scope signals", breakdown)
        self.assertIn("actor signal:", breakdown)
        self.assertIn("liability: 5/5", breakdown)
        self.assertIn("confidence: 0.90", breakdown)
        self.assertIn("type weight: practice", breakdown)

    def test_rank_memories_semantic_stub_does_not_change_score(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            signals=["migration", "deploy"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )

        matches = rank_memories(
            [memory],
            PreflightQuery(
                prompt="Fix billing deployment migration failure",
                actor="agent",
                area="billing",
                files=["billing/deploy.py"],
                workflow=["deployment"],
                risk="high",
            ),
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].score, 7.798)
        self.assertIn("semantic signal: unavailable -> +0.00", matches[0].score_breakdown)

    def test_rank_memories_uses_real_semantic_index_for_grounded_candidate(self) -> None:
        memory = Memory.create(
            type=MemoryType.SITUATION,
            title="Billing deployment migration order",
            summary="Billing deploys should check migration order before rollout.",
            signals=["billing", "deploy"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=4,
            confidence=0.8,
        )
        query = PreflightQuery(
            prompt="Fix billing deployment failure",
            actor="agent",
            area="billing",
            files=["billing/deploy.py"],
            workflow=["deployment"],
            risk="high",
        )

        baseline = rank_memories([memory], query)
        boosted = rank_memories(
            [memory],
            query,
            semantic_index=InMemorySemanticIndex(
                {
                    memory.id: SemanticSignal(
                        label="deterministic semantic match",
                        score=1.25,
                        available=True,
                    )
                }
            ),
        )

        self.assertEqual(len(baseline), 1)
        self.assertEqual(len(boosted), 1)
        self.assertEqual(boosted[0].score, round(baseline[0].score + 1.25, 3))
        self.assertIn("semantic signal: deterministic semantic match -> +1.25", boosted[0].score_breakdown)

    def test_rank_memories_does_not_surface_semantic_only_match(self) -> None:
        memory = Memory.create(
            type=MemoryType.SITUATION,
            title="Auth token rotation",
            summary="Token rotation has a lock ordering constraint.",
            signals=["auth", "token"],
            scope=MemoryScope(code=["auth"], workflow=["credential rotation"]),
            liability_score=4,
            confidence=0.8,
        )

        matches = rank_memories(
            [memory],
            PreflightQuery(
                prompt="Change CSS spacing on settings page",
                actor="agent",
                area="frontend",
                files=["frontend/settings.css"],
                workflow=["styling"],
                risk="low",
            ),
            semantic_index=InMemorySemanticIndex({memory.id: 5.0}),
        )

        self.assertEqual(matches, [])

    def test_persistent_semantic_index_refreshes_scores_and_persists_vectors(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback failure pattern",
                summary="Checkout rollbacks should verify release markers before retrying deployment.",
                signals=["checkout", "rollback", "release"],
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
                liability_score=4,
                confidence=0.8,
            )
            index_path = Path(tmp) / ".cmu" / "semantic_index.json"
            provider = HashingEmbeddingProvider(dimensions=32)
            index = PersistentSemanticIndex.load_or_build(index_path, [memory], provider=provider)

            query = PreflightQuery(
                prompt="Fix checkout rollback release marker deployment failure",
                actor="agent",
                area="checkout",
                files=["checkout/deploy.py"],
                workflow=["deployment"],
                risk="high",
            )
            signal = index.score(memory, query)

            self.assertTrue(index_path.exists())
            self.assertTrue(signal.available)
            self.assertEqual(signal.label, "local hashing embeddings")
            self.assertGreater(signal.score, 0.0)
            reloaded = PersistentSemanticIndex(index_path, provider=provider)
            self.assertIn(memory.id, reloaded.vectors)
            self.assertEqual(reloaded.fingerprints[memory.id], index.fingerprints[memory.id])

    def test_persistent_semantic_index_improves_grounded_ranking_explainably(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback release markers",
                summary="Rollback debugging found stale release markers blocking checkout deployment retries.",
                signals=["checkout", "rollback", "release"],
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
                liability_score=4,
                confidence=0.8,
            )
            query = PreflightQuery(
                prompt="Fix checkout rollback release marker deployment failure",
                actor="agent",
                area="checkout",
                files=["checkout/deploy.py"],
                workflow=["deployment"],
                risk="high",
            )
            baseline = rank_memories([memory], query)
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=32),
            )
            boosted = rank_memories([memory], query, semantic_index=semantic_index)

            self.assertEqual(len(baseline), 1)
            self.assertEqual(len(boosted), 1)
            self.assertGreater(boosted[0].score, baseline[0].score)
            breakdown = "\n".join(boosted[0].score_breakdown)
            self.assertIn("semantic signal: local hashing embeddings -> +", breakdown)
            self.assertNotIn("semantic signal: unavailable", breakdown)

    def test_persistent_semantic_index_cannot_surface_semantic_only_match(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth token rotation",
                summary="Token rotation has a lock ordering constraint.",
                signals=["auth", "token"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"]),
                liability_score=4,
                confidence=0.8,
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=32),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="Change CSS spacing on settings page",
                    actor="agent",
                    area="frontend",
                    files=["frontend/settings.css"],
                    workflow=["styling"],
                    risk="low",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(matches, [])

    def test_persistent_semantic_index_can_propose_candidate_when_grounded(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            query = PreflightQuery(
                prompt="roll back release marker problem",
                actor="agent",
                workflow=["deploy"],
                risk="high",
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories([memory], query, semantic_index=semantic_index)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].memory.id, memory.id)
            self.assertEqual(matches[0].matched_terms, ["semantic:workflow scope", "semantic:evidence"])
            breakdown = "\n".join(matches[0].score_breakdown)
            self.assertIn("semantic signal: local hashing embeddings -> +", breakdown)
            self.assertIn("semantic proposal grounded by workflow scope, evidence", breakdown)
            self.assertNotIn("text overlap:", breakdown)

    def test_persistent_semantic_index_rejects_proposal_without_scope_grounding(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                liability_score=4,
                confidence=0.8,
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="roll back release marker problem",
                    actor="agent",
                    workflow=["styling"],
                    risk="high",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(matches, [])

    def test_persistent_semantic_index_rejects_proposal_without_evidence_or_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                liability_score=4,
                confidence=0.8,
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="roll back release marker problem",
                    actor="agent",
                    workflow=["deploy"],
                    risk="high",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(matches, [])

    def test_persistent_semantic_index_can_propose_approved_practice_by_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Releasemarker cleanup default",
                summary="Stale releasemarkers can block rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                use_this_path="Check releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.9,
                approved_by="release owner",
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="roll back release marker problem",
                    actor="agent",
                    workflow=["deploy"],
                    risk="high",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].memory.id, memory.id)
            self.assertEqual(matches[0].matched_terms, ["semantic:workflow scope", "semantic:authority"])
            breakdown = "\n".join(matches[0].score_breakdown)
            self.assertIn("semantic proposal grounded by workflow scope, authority", breakdown)
            self.assertNotIn("text overlap:", breakdown)

    def test_persistent_semantic_index_rejects_unapproved_stable_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Releasemarker cleanup default",
                summary="Stale releasemarkers can block rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["A previous rollout retry succeeded after releasemarker cleanup."],
                use_this_path="Check releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.9,
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="roll back release marker problem",
                    actor="agent",
                    workflow=["deploy"],
                    risk="high",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(matches, [])

    def test_persistent_semantic_index_can_propose_approved_anchor_by_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.ANCHOR,
                title="Locksequence anchor",
                summary="Protected-value locksequence prevents cycling races.",
                signals=["credential", "locksequence"],
                scope=MemoryScope(workflow=["credential-rotation"], actor=["agent"]),
                use_this_path="Check the locksequence before updating active credentials.",
                liability_score=5,
                confidence=0.9,
                approved_by="security owner",
            )
            semantic_index = PersistentSemanticIndex.load_or_build(
                Path(tmp) / ".cmu" / "semantic_index.json",
                [memory],
                provider=HashingEmbeddingProvider(dimensions=64),
            )

            matches = rank_memories(
                [memory],
                PreflightQuery(
                    prompt="secret lock order cycle race",
                    actor="agent",
                    workflow=["rotation"],
                    risk="high",
                ),
                semantic_index=semantic_index,
            )

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].memory.id, memory.id)
            self.assertEqual(matches[0].matched_terms, ["semantic:workflow scope", "semantic:authority"])
            breakdown = "\n".join(matches[0].score_breakdown)
            self.assertIn("semantic proposal grounded by workflow scope, authority", breakdown)

    def test_cli_preflight_local_semantic_can_surface_approved_anchor_from_add(self) -> None:
        with TemporaryDirectory() as tmp:
            with redirect_stdout(StringIO()):
                main(
                    [
                        "--root",
                        tmp,
                        "add",
                        "--type",
                        "anchor",
                        "--title",
                        "Locksequence anchor",
                        "--summary",
                        "Protected-value locksequence prevents cycling races.",
                        "--signal",
                        "credential",
                        "--signal",
                        "locksequence",
                        "--scope-workflow",
                        "credential-rotation",
                        "--scope-actor",
                        "agent",
                        "--use-path",
                        "Check the locksequence before updating active credentials.",
                        "--liability",
                        "5",
                        "--confidence",
                        "0.9",
                        "--approved-by",
                        "security owner",
                    ]
                )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "secret lock order cycle race",
                        "--actor",
                        "agent",
                        "--workflow",
                        "rotation",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-matches",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("semantic proposal grounded by workflow scope, authority", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertIn("Locksequence anchor", rendered)

    def test_cli_preflight_local_semantic_writes_index_and_explains_score(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback release markers",
                summary="Rollback debugging found stale release markers blocking checkout deployment retries.",
                signals=["checkout", "rollback", "release"],
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
                evidence=["Retry succeeded after clearing stale release marker state."],
                use_this_path="Verify release markers before retrying checkout deployment.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Fix checkout rollback release marker deployment failure",
                        "--actor",
                        "agent",
                        "--area",
                        "checkout",
                        "--file",
                        "checkout/deploy.py",
                        "--workflow",
                        "deployment",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-matches",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertTrue((Path(tmp) / ".cmu" / "semantic_index.json").exists())
            self.assertIn("semantic signal: local hashing embeddings -> +", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertIn("Checkout rollback release markers", rendered)

    def test_cli_semantic_status_inspects_local_index_without_refreshing(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback release markers",
                summary="Checkout rollback deploys should inspect stale release markers before retrying rollout.",
                signals=["checkout", "rollback"],
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
                evidence=["A stale release marker blocked checkout rollout retry."],
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            with redirect_stdout(StringIO()):
                main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Fix checkout rollback release marker deployment failure",
                        "--actor",
                        "agent",
                        "--area",
                        "checkout",
                        "--workflow",
                        "deployment",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                    ]
                )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-status"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Index Status", rendered)
            self.assertIn("Exists: yes", rendered)
            self.assertIn("Provider: local hashing embeddings", rendered)
            self.assertIn("Memories: 1", rendered)
            self.assertIn("Vectors: 1", rendered)
            self.assertIn("Missing Vectors: 0", rendered)

    def test_cli_preflight_local_semantic_can_surface_grounded_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-matches",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("semantic proposal grounded by workflow scope, evidence", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertIn("Rollback releasemarker cleanup", rendered)

    def test_cli_preflight_local_semantic_can_surface_approved_practice_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Releasemarker cleanup default",
                summary="Stale releasemarkers can block rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                use_this_path="Check releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.9,
                approved_by="release owner",
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-matches",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("semantic proposal grounded by workflow scope, authority", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertIn("Releasemarker cleanup default", rendered)

    def test_cli_preflight_semantic_proposal_diagnostics_explain_unapproved_stable_rejection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Releasemarker cleanup default",
                summary="Stale releasemarkers can block rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                use_this_path="Check releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.9,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-semantic-proposals",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Proposal Diagnostics", rendered)
            self.assertIn("status: rejected", rendered)
            self.assertIn("stable Practice/Anchor semantic proposal requires explicit authority", rendered)
            self.assertIn("CMU stayed quiet: no memory crossed the action threshold.", rendered)

    def test_cli_preflight_semantic_proposal_diagnostics_label_grounded_match_as_direct(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle."],
                use_this_path="Run preflight at task start.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "implement CMU preflight behavior",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-semantic-proposals",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("status: direct-match", rendered)
            self.assertIn("text or hard scope already grounds this memory before semantic proposal", rendered)
            self.assertIn("CMU Action Note", rendered)

    def test_cli_retrieval_pipeline_reports_graph_ranking_rejection_and_action_note(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use retry budget",
                summary="Outbound retries should respect bounded failure handling.",
                use_this_path="Check the retry budget before changing retry behavior.",
                scope=MemoryScope(code=["billing/reliability"], workflow=["debugging"], actor=["agent"]),
                evidence=["Prior webhook timeout fix used bounded retries."],
                liability_score=4,
                confidence=0.85,
                approved_by="reliability owner",
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Webhook timeout root cause",
                summary="Webhook timeouts came from unbounded retries during dependency failures.",
                signals=["webhook", "timeout", "retries"],
                scope=MemoryScope(code=["billing/webhook.py"], workflow=["debugging"], actor=["agent"]),
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="Timeout debugging should lead to the retry budget practice.",
                    )
                ],
                evidence=["Debugging found retry behavior as the timeout cause."],
                liability_score=3,
                confidence=0.75,
            )
            thin_candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Webhook timeout note",
                summary="Webhook timeout happened.",
                liability_score=1,
                confidence=0.4,
            )
            rejected = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth token rotation",
                summary="Token rotation has a lock ordering constraint.",
                scope=MemoryScope(code=["auth/tokens.py"], workflow=["credential rotation"], actor=["agent"]),
                liability_score=4,
                confidence=0.8,
            )
            for memory in [practice, situation, thin_candidate, rejected]:
                store.add(memory)
            use_store = MemoryUseStore(tmp)
            receipt = MemoryUseReceipt.create(
                practice,
                PreflightQuery(
                    prompt="Investigate billing webhook timeout",
                    actor="agent",
                    area="billing",
                    files=["billing/webhook.py"],
                    workflow=["debugging"],
                    risk="high",
                ),
                Match(memory=practice, score=3.0, matched_terms=["graph:related_practice"]),
                source_command="start",
            )
            receipt.commit_hash = "3" * 40
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.9
            use_store.add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "retrieval-pipeline",
                        "Investigate billing webhook timeout",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--file",
                        "billing/webhook.py",
                        "--workflow",
                        "debugging",
                        "--risk",
                        "high",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Hybrid Retrieval Pipeline", rendered)
            self.assertIn("Graph Expanded: 1", rendered)
            self.assertIn("Phase: graph expansion", rendered)
            self.assertIn("graph expansion via", rendered)
            self.assertIn("use evidence adjusted score", rendered)
            self.assertIn("Status: below-threshold", rendered)
            self.assertIn(thin_candidate.id, rendered)
            self.assertIn("Status: rejected", rendered)
            self.assertIn("hard grounding rejected: scope conflicts with query", rendered)
            self.assertIn("Selected Action:", rendered)
            self.assertIn("action-note:", rendered)
            self.assertIn("Action Note Preview:", rendered)

    def test_cli_retrieval_pipeline_reports_semantic_admission_and_authority_rejection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            admissible = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            unapproved_stable = Memory.create(
                type=MemoryType.PRACTICE,
                title="Releasemarker cleanup default",
                summary="Stale releasemarkers can block rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Prior rollout retry succeeded after cleanup."],
                use_this_path="Check releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.9,
            )
            store.add(admissible)
            store.add(unapproved_stable)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "retrieval-pipeline",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertTrue((Path(tmp) / ".cmu" / "semantic_index.json").exists())
            self.assertIn("Semantic Admissible: 1", rendered)
            self.assertIn("semantic proposal admitted by grounding", rendered)
            self.assertIn("semantic proposal admissible", rendered)
            self.assertIn("stable Practice/Anchor semantic proposal requires explicit authority", rendered)
            self.assertIn("Action Note Preview:", rendered)
            self.assertIn("Rollback releasemarker cleanup", rendered)

    def test_rank_memories_does_not_expand_graph_from_weak_primary_match(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use retry budget",
            summary="Retries should respect an explicit retry budget.",
        )
        weak_source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook edge case",
            summary="A low-confidence note that should not pull graph context by itself.",
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
            confidence=0.1,
        )

        matches = rank_memories(
            [weak_source, practice],
            PreflightQuery(prompt="webhook", actor="agent", risk="low"),
        )

        self.assertTrue(any(match.memory.id == weak_source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_for_wrong_relation_target_type(self) -> None:
        wrong_target = Memory.create(
            type=MemoryType.SITUATION,
            title="Retry budget incident",
            summary="This is not a Practice Memory.",
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeouts came from unbounded retries during dependency failures.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=wrong_target.id,
                )
            ],
        )

        matches = rank_memories(
            [source, wrong_target],
            PreflightQuery(prompt="Investigate billing webhook timeout", files=["billing/webhook.py"], risk="medium"),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == wrong_target.id for match in matches))

    def test_rank_memories_does_not_expand_graph_when_target_actor_scope_conflicts(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use retry budget",
            summary="Retries should respect an explicit retry budget.",
            scope=MemoryScope(code=["billing"], actor=["developer"]),
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeouts came from unbounded retries during dependency failures.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [source, practice],
            PreflightQuery(prompt="Investigate billing webhook timeout", actor="agent", files=["billing/webhook.py"], risk="medium"),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_when_target_code_scope_conflicts(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use auth retry budget",
            summary="Auth retries should respect an explicit retry budget.",
            scope=MemoryScope(code=["auth/tokens.py"]),
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeouts came from unbounded retries during dependency failures.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [source, practice],
            PreflightQuery(prompt="Investigate billing webhook timeout", files=["billing/webhook.py"], risk="medium"),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_when_target_workflow_scope_conflicts(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Deploy migration first",
            summary="Deploy work should check migration order first.",
            scope=MemoryScope(code=["billing"], workflow=["deployment"]),
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeout debugging revealed retry behavior.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [source, practice],
            PreflightQuery(
                prompt="Investigate billing webhook timeout",
                files=["billing/webhook.py"],
                workflow=["debugging"],
                risk="medium",
            ),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_when_target_environment_scope_conflicts(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use production rollout gate",
            summary="Production rollout changes must use the release gate.",
            scope=MemoryScope(code=["billing"], environment=["production"]),
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Local billing timeout root cause",
            summary="Local billing timeout debugging revealed rollout-sensitive behavior.",
            signals=["billing", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [source, practice],
            PreflightQuery(
                prompt="Investigate local billing webhook timeout",
                files=["billing/webhook.py"],
                environment=["local"],
                risk="medium",
            ),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_to_unscoped_stable_memory(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use retry budget",
            summary="Retries should respect an explicit retry budget.",
        )
        source = Memory.create(
            type=MemoryType.SITUATION,
            title="Webhook timeout root cause",
            summary="Webhook timeouts came from unbounded retries during dependency failures.",
            signals=["webhook", "timeout", "retries"],
            scope=MemoryScope(code=["billing/webhook.py"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [source, practice],
            PreflightQuery(prompt="Investigate billing webhook timeout", files=["billing/webhook.py"], risk="medium"),
        )

        self.assertTrue(any(match.memory.id == source.id for match in matches))
        self.assertFalse(any(match.memory.id == practice.id for match in matches))

    def test_rank_memories_does_not_expand_graph_from_actor_only_match(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Use retry budget",
            summary="Retries should respect an explicit retry budget.",
        )
        actor_only = Memory.create(
            type=MemoryType.SITUATION,
            title="Agent-only guidance",
            summary="This memory only shares actor scope with the query.",
            scope=MemoryScope(actor=["agent"]),
            relationships=[
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                )
            ],
        )

        matches = rank_memories(
            [actor_only, practice],
            PreflightQuery(prompt="Change CSS spacing", actor="agent", area="frontend", risk="low"),
        )

        self.assertEqual(matches, [])

    def test_cli_preflight_creates_use_receipt_when_memory_surfaces(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                signals=["migration", "deploy"],
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                evidence=["Previous deploy failed when service rolled out first."],
                use_this_path="Check migration order before changing deployment code.",
                avoid_this="Do not roll out service code before confirming schema compatibility.",
                challenge_only_if="The deployment path no longer touches the billing schema.",
                liability_score=5,
                confidence=0.9,
            )
            store.add(memory)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Fix billing deployment migration failure",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--file",
                        "billing/deploy.py",
                        "--risk",
                        "high",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Use Receipt: use_", output.getvalue())
            receipts = MemoryUseStore(tmp).list()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].memory_id, memory.id)
            self.assertEqual(receipts[0].files, ["billing/deploy.py"])
            self.assertEqual(receipts[0].source_command, "preflight")

    def test_cli_preflight_does_not_create_use_receipt_when_quiet(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.add(
                Memory.create(
                    type=MemoryType.SITUATION,
                    title="Auth token rotation",
                    summary="Token rotation has a lock ordering constraint.",
                    signals=["auth", "token"],
                    scope=MemoryScope(code=["auth"]),
                    liability_score=4,
                )
            )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Change CSS spacing on settings page",
                        "--risk",
                        "low",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU stayed quiet", output.getvalue())
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_preflight_uses_commit_receipts_to_adjust_ranking(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            first = Memory.create(
                type=MemoryType.PRACTICE,
                title="Billing deploy checks service order",
                summary="Billing deploy work should check service rollout order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                use_this_path="Check service rollout order.",
                liability_score=4,
                confidence=0.8,
            )
            second = Memory.create(
                type=MemoryType.PRACTICE,
                title="Billing deploy checks migration order",
                summary="Billing deploy work should check migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                use_this_path="Check migration order.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(first)
            store.add(second)
            receipt = MemoryUseReceipt.create(
                second,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 3.0})(),
            )
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.85
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Fix billing deploy",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--file",
                        "billing/deploy.py",
                        "--risk",
                        "high",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Billing deploy checks migration order", output.getvalue())

    def test_cli_preflight_does_not_use_receipts_to_surface_irrelevant_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Billing deploy checks migration order",
                summary="Billing deploy work should check migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                use_this_path="Check migration order.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)
            for _ in range(10):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                    match=type("MatchStub", (), {"score": 3.0})(),
                )
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.95
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "Change CSS spacing on settings page",
                        "--actor",
                        "agent",
                        "--area",
                        "frontend",
                        "--file",
                        "ui/settings.css",
                        "--risk",
                        "low",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU stayed quiet", output.getvalue())
            self.assertNotIn("Use Receipt:", output.getvalue())

class OnboardingSeedTests(unittest.TestCase):
    def test_onboarding_seed_uses_matching_memory_without_dumping_context(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["preflight", "quiet"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
            approved_by="CMU core owner",
        )

        seed = build_onboarding_seed(
            [memory],
            PreflightQuery(
                prompt="implement CMU preflight behavior",
                actor="agent",
                area="cmu",
                workflow=["implementation"],
                risk="high",
            ),
        )

        rendered = seed.render()
        self.assertIn("CMU Onboarding Seed", rendered)
        self.assertIn("Where Working: cmu, implementation, agent", rendered)
        self.assertIn("Must Not Violate: Respect approved practice memory within scope: cmu, implementation, agent.", rendered)
        self.assertNotIn("Must Not Violate: The task is small", rendered)
        self.assertIn("Default Path: Run preflight at task start", rendered)
        self.assertIn("Trap To Avoid: Do not dump memory into context", rendered)
        self.assertIn(f"Source Memory: {memory.id}", rendered)
        self.assertLess(len(rendered.split()), 120)

    def test_onboarding_seed_falls_back_when_memory_stays_quiet(self) -> None:
        memory = Memory.create(
            type=MemoryType.SITUATION,
            title="Auth token rotation",
            summary="Token rotation has a lock ordering constraint.",
            signals=["auth"],
            scope=MemoryScope(code=["auth"]),
            liability_score=4,
        )

        seed = build_onboarding_seed(
            [memory],
            PreflightQuery(
                prompt="Change CSS spacing on settings page",
                actor="agent",
                area="frontend",
                files=["frontend/settings.css"],
                workflow=["styling"],
                risk="low",
            ),
        )

        rendered = seed.render()
        self.assertIn("Where Working: frontend, frontend/settings.css, styling", rendered)
        self.assertIn("Must Not Violate: Do not invent project rules", rendered)
        self.assertIn("Confidence: no matching memory", rendered)
        self.assertNotIn(memory.id, rendered)

    def test_cli_onboard_renders_matching_onboarding_seed(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["preflight", "quiet"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "onboard",
                        "implement CMU preflight behavior",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("Must Not Violate: Respect approved practice memory within scope: cmu, implementation, agent.", rendered)
            self.assertNotIn("Must Not Violate: The task is small", rendered)
            self.assertIn("Default Path: Run preflight at task start", rendered)
            self.assertIn(f"Source Memory: {memory.id}", rendered)

    def test_cli_onboard_can_use_local_semantic_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                avoid_this="Do not retry rollout before checking release marker state.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "onboard",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("Default Path: Check stale releasemarkers", rendered)
            self.assertIn(f"Source Memory: {memory.id}", rendered)

    def test_cli_onboard_can_show_semantic_proposal_diagnostics_without_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "onboard",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-semantic-proposals",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("CMU Semantic Proposal Diagnostics", rendered)
            self.assertIn("status: admissible", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_onboarding_seed_enforces_compact_normal_budget(self) -> None:
        long_text = " ".join(f"step{index}" for index in range(80))
        memory = Memory.create(
            type=MemoryType.SITUATION,
            title="Verbose implementation situation",
            summary=long_text,
            signals=["verbose", "implementation"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["Long evidence should not make onboarding verbose."],
            use_this_path=long_text,
            avoid_this=long_text,
            challenge_only_if=long_text,
            liability_score=4,
            confidence=0.8,
        )

        seed = build_onboarding_seed(
            [memory],
            PreflightQuery(
                prompt="verbose implementation work",
                actor="agent",
                area="cmu",
                workflow=["implementation"],
                risk="medium",
            ),
        )

        rendered = seed.render()
        self.assertLessEqual(word_count(rendered), NORMAL_SEED_WORD_LIMIT)
        self.assertIn("Default Path: step0 step1", rendered)
        self.assertIn("...", rendered)


class TriggerDecisionTests(unittest.TestCase):
    def test_trigger_decision_must_call_for_high_risk_domain(self) -> None:
        decision = decide_trigger(
            PreflightQuery(
                prompt="rotate production credentials",
                actor="agent",
                area="auth",
                workflow=["deployment"],
                risk="high",
            ),
            irreversible=True,
        )

        self.assertEqual(decision.level, "must-call")
        self.assertIn("high risk task", decision.reasons)
        self.assertIn("hard-to-rollback change", decision.reasons)
        self.assertTrue(any(reason.startswith("high-risk area") for reason in decision.reasons))

    def test_trigger_decision_should_call_for_uncertain_medium_task(self) -> None:
        decision = decide_trigger(
            PreflightQuery(
                prompt="refactor settings flow",
                actor="agent",
                area="frontend",
                files=["settings/a.py", "settings/b.py", "settings/c.py"],
                risk="medium",
            ),
            uncertainty=True,
        )

        self.assertEqual(decision.level, "should-call")
        self.assertIn("medium risk task", decision.reasons)
        self.assertIn("requirements or implementation uncertainty", decision.reasons)
        self.assertIn("multi-file task", decision.reasons)

    def test_trigger_decision_silent_skip_for_low_risk_local_task(self) -> None:
        decision = decide_trigger(
            PreflightQuery(
                prompt="adjust button spacing",
                actor="agent",
                area="frontend",
                files=["settings.css"],
                workflow=["styling"],
                risk="low",
            )
        )

        self.assertEqual(decision.level, "silent-skip")
        self.assertEqual(decision.reasons, ["small/local/low-risk with no trigger signals"])

    def test_trigger_decision_does_not_match_high_risk_terms_inside_words(self) -> None:
        decision = decide_trigger(
            PreflightQuery(
                prompt="document approved anchor authoring",
                actor="agent",
                area="cmu",
                files=["cmu/cli.py"],
                risk="low",
            )
        )

        self.assertEqual(decision.level, "silent-skip")
        self.assertNotIn("high-risk area: auth", decision.reasons)

    def test_trigger_decision_matches_high_risk_terms_in_file_paths(self) -> None:
        decision = decide_trigger(
            PreflightQuery(
                prompt="change token rotation helper",
                actor="agent",
                files=["auth/tokens.py"],
                risk="low",
            )
        )

        self.assertEqual(decision.level, "must-call")
        self.assertIn("high-risk area: auth", decision.reasons)

    def test_cli_trigger_renders_must_call_decision(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "trigger",
                    "rotate production credentials",
                    "--actor",
                    "agent",
                    "--area",
                    "auth",
                    "--workflow",
                    "deployment",
                    "--risk",
                    "high",
                    "--irreversible",
                ]
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("CMU Trigger Decision", rendered)
        self.assertIn("Level: must-call", rendered)
        self.assertIn("high risk task", rendered)
        self.assertIn("hard-to-rollback change", rendered)

    def test_cli_trigger_renders_silent_skip_decision(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "trigger",
                    "adjust button spacing",
                    "--actor",
                    "agent",
                    "--area",
                    "frontend",
                    "--file",
                    "settings.css",
                    "--workflow",
                    "styling",
                    "--risk",
                    "low",
                ]
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Level: silent-skip", rendered)
        self.assertIn("small/local/low-risk", rendered)


class WorkCycleStartTests(unittest.TestCase):
    def test_cli_start_silent_skip_does_not_run_memory_or_create_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "start",
                        "adjust button spacing",
                        "--actor",
                        "agent",
                        "--area",
                        "frontend",
                        "--file",
                        "settings.css",
                        "--workflow",
                        "styling",
                        "--risk",
                        "low",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Start", rendered)
            self.assertIn("Level: silent-skip", rendered)
            self.assertIn("Work Cycle: silent-skip", rendered)
            self.assertNotIn("CMU Onboarding Seed", rendered)
            self.assertNotIn("CMU Action Note", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_start_should_call_returns_fallback_seed_without_receipt_when_no_memory_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "start",
                        "refactor settings flow",
                        "--actor",
                        "agent",
                        "--area",
                        "frontend",
                        "--file",
                        "settings/a.py",
                        "--file",
                        "settings/b.py",
                        "--file",
                        "settings/c.py",
                        "--risk",
                        "medium",
                        "--uncertainty",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Level: should-call", rendered)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("Confidence: no matching memory", rendered)
            self.assertIn("CMU stayed quiet: no memory crossed the action threshold.", rendered)
            self.assertNotIn("CMU Action Note", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_start_must_call_surfaces_action_note_and_creates_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["preflight", "quiet"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "start",
                        "implement CMU preflight behavior",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                    ]
                )

            rendered = output.getvalue()
            receipts = MemoryUseStore(tmp).list()
            self.assertEqual(exit_code, 0)
            self.assertIn("Level: must-call", rendered)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("Must Not Violate: Respect approved practice memory within scope: cmu, implementation, agent.", rendered)
            self.assertNotIn("Must Not Violate: The task is small", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertIn("Challenge Only If: The task is small, local, low-risk, and follows an obvious existing pattern.", rendered)
            self.assertIn("Use Receipt: use_", rendered)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].memory_id, memory.id)
            self.assertEqual(receipts[0].prompt, "implement CMU preflight behavior")
            self.assertEqual(receipts[0].source_command, "start")

            use_list = StringIO()
            with redirect_stdout(use_list):
                list_exit = main(["--root", tmp, "use-list"])

            self.assertEqual(list_exit, 0)
            self.assertIn(f"{receipts[0].id} start surfaced unlinked", use_list.getvalue())

    def test_cli_start_can_show_semantic_proposal_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "start",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                        "--show-semantic-proposals",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Start", rendered)
            self.assertIn("CMU Semantic Proposal Diagnostics", rendered)
            self.assertIn("status: admissible", rendered)
            self.assertIn("semantic proposal has grounded action scope plus evidence or authority", rendered)
            self.assertIn("CMU Action Note", rendered)
            self.assertEqual(len(MemoryUseStore(tmp).list()), 1)


class FullWorkCycleIntegrationTests(unittest.TestCase):
    def test_cli_work_cycle_connects_trigger_preflight_receipt_after_work_and_analytics(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["preflight", "quiet"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)
            add_strong_receipts(tmp, memory, count=2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "work-cycle",
                        "implement CMU work-cycle integration report",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                        "--learning-signal",
                        "new convention",
                        "--outcome",
                        "The report connected trigger, preflight, receipt planning, and after-work memory review.",
                        "--worked",
                        "Use one read-only report to inspect the whole Work Cycle before automating more.",
                        "--future-use",
                        "Use when validating CMU task loop integration across start, receipts, and review.",
                        "--evidence",
                        "Full Work Cycle integration test exercises real retrieval and remember gates.",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Full Work Cycle", rendered)
            self.assertIn("Mode: read-only integration proof", rendered)
            self.assertIn("Step 1 - Trigger:", rendered)
            self.assertIn("Level: must-call", rendered)
            self.assertIn("Step 2 - Onboarding:", rendered)
            self.assertIn("CMU Onboarding Seed", rendered)
            self.assertIn("Step 3 - Preflight:", rendered)
            self.assertIn("Action: action-note", rendered)
            self.assertIn(f"Matched Memory: {memory.id}", rendered)
            self.assertIn("Step 4 - Receipt:", rendered)
            self.assertIn(f"Would create Memory Use Receipt for {memory.id} from work-cycle.", rendered)
            self.assertIn("Step 5 - After-Work Memory Decision:", rendered)
            self.assertIn("Status: candidate-ready", rendered)
            self.assertIn("Suggested Next Type: situation", rendered)
            self.assertIn("Step 6 - Review Signal:", rendered)
            self.assertIn("useful; 2/2 linked, 2 strong, 0 drag", rendered)
            self.assertIn("governance ready: strengthen evidence", rendered)
            self.assertIn("link the resulting receipt/checkpoint", rendered)
            self.assertEqual(len(MemoryUseStore(tmp).list()), 2)
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 0)

    def test_cli_work_cycle_silent_skip_can_still_identify_after_work_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "work-cycle",
                        "adjust local typo in settings label",
                        "--actor",
                        "agent",
                        "--area",
                        "frontend",
                        "--file",
                        "settings.css",
                        "--workflow",
                        "styling",
                        "--risk",
                        "low",
                        "--learning-signal",
                        "human correction",
                        "--outcome",
                        "The label typo revealed a hidden naming convention for settings copy.",
                        "--worked",
                        "Match settings labels to the existing sidebar terminology.",
                        "--future-use",
                        "Use when editing settings copy or onboarding agents to settings UI conventions.",
                        "--evidence",
                        "Human correction identified the naming convention.",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Full Work Cycle", rendered)
            self.assertIn("Level: silent-skip", rendered)
            self.assertIn("Skipped: trigger selected silent-skip.", rendered)
            self.assertIn("Action: quiet", rendered)
            self.assertIn("No receipt planned: no Action Note surfaced.", rendered)
            self.assertIn("Status: candidate-ready", rendered)
            self.assertIn("No matched memory analytics available.", rendered)
            self.assertIn("save/review the Candidate Memory even though no prior memory guided the task", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE), [])


class AntiPatternWorkflowTests(unittest.TestCase):
    def test_cli_anti_pattern_reports_active_warning_relationships_and_usefulness(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Check dependency versions before rerunning failing tests",
                summary="Repeated test failures around dependencies should verify package versions before retrying blindly.",
                scope=MemoryScope(code=["tests"], workflow=["debugging"], actor=["agent"]),
                evidence=["A dependency mismatch caused repeated failures until version state was inspected."],
                use_this_path="Inspect package versions before rerunning the same failing test loop.",
                avoid_this="Do not keep rerunning tests without checking dependency versions.",
                challenge_only_if="The failure is deterministic and unrelated to dependency state.",
                liability_score=4,
                confidence=0.85,
                approved_by="QA owner",
            )
            anti = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Blindly rerun dependency failures",
                summary="Repeatedly rerunning dependency-related test failures can hide the version mismatch root cause.",
                scope=MemoryScope(code=["tests"], workflow=["debugging"], actor=["agent"]),
                evidence=["A version mismatch was only found after stopping the retry loop."],
                use_this_path="Check dependency versions and lockfile state before another retry.",
                avoid_this="Do not keep rerunning dependency failures hoping they pass.",
                challenge_only_if="Review when dependency state is already proven clean.",
                liability_score=4,
                confidence=0.8,
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.CHALLENGES,
                        target_id=practice.id,
                        reason="This anti-pattern protects the dependency-debugging practice from the tempting retry loop.",
                    )
                ],
            )
            store.add(practice)
            store.add(anti)
            add_strong_receipts(tmp, anti, count=2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "anti-pattern",
                        "rerun dependency failing tests again",
                        "--actor",
                        "agent",
                        "--area",
                        "tests",
                        "--workflow",
                        "debugging",
                        "--risk",
                        "high",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Anti-Pattern Workflow", rendered)
            self.assertIn("Mode: read-only anti-pattern proof", rendered)
            self.assertIn("Creation Path:", rendered)
            self.assertIn("Anti-Patterns: 1", rendered)
            self.assertIn("Active Warnings: 1", rendered)
            self.assertIn(f"{anti.id} [anti-pattern/active] Blindly rerun dependency failures", rendered)
            self.assertIn("Trap: Repeatedly rerunning dependency-related test failures", rendered)
            self.assertIn("Avoid: Do not keep rerunning dependency failures hoping they pass.", rendered)
            self.assertIn("Safer Path: Check dependency versions and lockfile state before another retry.", rendered)
            self.assertIn("Retrieval: active warning", rendered)
            self.assertIn("Relationships: challenges->Check dependency versions before rerunning failing tests", rendered)
            self.assertIn("Evidence: 1 memory evidence; 2/2 linked uses; 0 unresolved", rendered)
            self.assertIn("Usefulness: useful; 2 strong, 0 drag", rendered)
            self.assertIn("State: active warning", rendered)
            self.assertIn("surface the avoidance warning", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_anti_pattern_filter_reports_evidence_gap_and_review_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            thin = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Skip rollback verification",
                summary="Skipping rollback verification can hide deployment failure.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                avoid_this="Do not skip rollback verification.",
                use_this_path="Verify rollback before marking deployment recovered.",
                challenge_only_if="Rollback verification is automated elsewhere.",
                liability_score=4,
                confidence=0.55,
            )
            noisy = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Avoid all broad commits",
                summary="Broad commits can make evidence attribution hard.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Mixed commits made early receipt evidence weak."],
                avoid_this="Do not use broad commits.",
                use_this_path="Prefer focused commits when linking receipt evidence.",
                challenge_only_if="Review when broad checkpoint commits are intentionally required.",
                liability_score=3,
                confidence=0.7,
            )
            store.add(thin)
            store.add(noisy)
            add_drag_receipts(tmp, noisy, count=2)

            all_output = StringIO()
            with redirect_stdout(all_output):
                all_exit = main(["--root", tmp, "anti-pattern"])

            all_rendered = all_output.getvalue()
            self.assertEqual(all_exit, 0)
            self.assertIn("Evidence Gaps: 1", all_rendered)
            self.assertIn("Review Ready: 1", all_rendered)
            self.assertIn(f"{thin.id} [anti-pattern/active] Skip rollback verification", all_rendered)
            self.assertIn("State: evidence gap", all_rendered)
            self.assertIn("add concrete failure, incident, or review evidence", all_rendered)
            self.assertIn(f"{noisy.id} [anti-pattern/active] Avoid all broad commits", all_rendered)
            self.assertIn("State: review warning", all_rendered)
            self.assertIn("inspect use receipts; narrow, retire, or rewrite", all_rendered)

            filtered = StringIO()
            with redirect_stdout(filtered):
                filter_exit = main(["--root", tmp, "anti-pattern", "--memory", noisy.id])

            filtered_rendered = filtered.getvalue()
            self.assertEqual(filter_exit, 0)
            self.assertIn(f"Memory Filter: {noisy.id}", filtered_rendered)
            self.assertIn("Anti-Patterns: 1", filtered_rendered)
            self.assertIn("Usefulness: drag; 0 strong, 2 drag", filtered_rendered)


class QuestionWorkflowTests(unittest.TestCase):
    def test_cli_question_surfaces_relevant_owned_question_with_relationship(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Verify release markers before deployment retry",
                summary="Deployment retries should confirm release marker state before another rollout attempt.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Rollback recovery required release marker verification."],
                use_this_path="Check release marker state before retrying rollout.",
                approved_by="Release owner",
            )
            question = Memory.create(
                type=MemoryType.QUESTION,
                title="Does checkout rollback share the release marker?",
                summary="Does checkout rollback use the same release marker state as the deployment retry path?",
                scope=MemoryScope(
                    ownership=["Release owner"],
                    code=["checkout", "deploy"],
                    workflow=["deployment"],
                    actor=["agent"],
                ),
                evidence=["Checkout rollback logs mention the same marker key, but ownership is not yet confirmed."],
                use_this_path="Inspect checkout rollback marker reads before changing retry behavior.",
                avoid_this="Do not assume checkout rollback and deploy retry own separate marker state.",
                challenge_only_if="Resolve when marker ownership and read/write paths are verified.",
                liability_score=4,
                confidence=0.75,
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="The answer may change the deployment retry practice.",
                    )
                ],
            )
            store.add(practice)
            store.add(question)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "question",
                        "debug checkout rollback release marker retry",
                        "--actor",
                        "agent",
                        "--area",
                        "checkout",
                        "--workflow",
                        "deployment",
                        "--risk",
                        "high",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Question Workflow", rendered)
            self.assertIn("Mode: read-only question tracking proof", rendered)
            self.assertIn("Questions: 1", rendered)
            self.assertIn("Active Questions: 1", rendered)
            self.assertIn(f"{question.id} [question/active] Does checkout rollback share the release marker?", rendered)
            self.assertIn("Owner: Release owner", rendered)
            self.assertIn("Question: Does checkout rollback use the same release marker state", rendered)
            self.assertIn("Investigation Path: Inspect checkout rollback marker reads", rendered)
            self.assertIn("Avoid Assuming: Do not assume checkout rollback and deploy retry own separate marker state.", rendered)
            self.assertIn("Retrieval: surface question", rendered)
            self.assertIn("Relationships: related_practice->Verify release markers before deployment retry", rendered)
            self.assertIn("State: active question", rendered)
            self.assertIn(f"resolve with `cmu resolve-question {question.id} ...`", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_question_reports_ownership_and_evidence_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            no_owner = Memory.create(
                type=MemoryType.QUESTION,
                title="Who owns billing migration rollback approval?",
                summary="Billing rollback approval ownership is unclear.",
                scope=MemoryScope(code=["billing"], workflow=["migration"]),
                evidence=["Rollback runbook names two teams without a final owner."],
                liability_score=5,
                confidence=0.65,
            )
            no_evidence = Memory.create(
                type=MemoryType.QUESTION,
                title="Does auth rotation require staging soak?",
                summary="Credential rotation may require staging soak before production.",
                scope=MemoryScope(ownership=["Security owner"], code=["auth"], workflow=["credential rotation"]),
                liability_score=4,
                confidence=0.55,
            )
            store.add(no_owner)
            store.add(no_evidence)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "question"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Questions: 2", rendered)
            self.assertIn("Ownership Gaps: 1", rendered)
            self.assertIn("Evidence Gaps: 1", rendered)
            self.assertIn(f"{no_owner.id} [question/active] Who owns billing migration rollback approval?", rendered)
            self.assertIn("Owner: missing explicit owner", rendered)
            self.assertIn("State: ownership gap", rendered)
            self.assertIn("add ownership scope", rendered)
            self.assertIn(f"{no_evidence.id} [question/active] Does auth rotation require staging soak?", rendered)
            self.assertIn("State: evidence gap", rendered)
            self.assertIn("add evidence showing why forgetting this uncertainty is costly", rendered)

    def test_cli_resolve_question_creates_derived_situation_and_retires_question(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            question = Memory.create(
                type=MemoryType.QUESTION,
                title="Does checkout rollback share the release marker?",
                summary="Does checkout rollback use the same release marker state as deployment retry?",
                scope=MemoryScope(
                    ownership=["Release owner"],
                    code=["checkout", "deploy"],
                    workflow=["deployment"],
                    actor=["agent"],
                ),
                evidence=["Logs mention the same marker key."],
                use_this_path="Inspect both marker read/write paths.",
                avoid_this="Do not assume separate marker ownership.",
                challenge_only_if="Review if marker ownership changes.",
                liability_score=4,
                confidence=0.7,
            )
            store.add(question)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "resolve-question",
                        question.id,
                        "--outcome",
                        "situation",
                        "--answer",
                        "Checkout rollback and deployment retry share the same release marker record.",
                        "--resolved-by",
                        "Release owner",
                        "--evidence",
                        "Code inspection confirmed both paths read and write release_marker_id.",
                        "--title",
                        "Checkout rollback shares deployment release marker",
                        "--use-path",
                        "Verify shared marker state before retrying checkout rollback.",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Question Resolution Applied", rendered)
            self.assertIn(f"Question: {question.id} [retired]", rendered)
            self.assertIn("Outcome Memory:", rendered)
            retired = store.list(type=MemoryType.QUESTION, status=MemoryStatus.RETIRED)
            situations = store.list(type=MemoryType.SITUATION)
            self.assertEqual(len(retired), 1)
            self.assertEqual(len(situations), 1)
            self.assertIn("Answer: Checkout rollback and deployment retry share the same release marker record.", retired[0].evidence)
            self.assertEqual(situations[0].title, "Checkout rollback shares deployment release marker")
            self.assertEqual(situations[0].approved_by, "Release owner")
            self.assertEqual(situations[0].relationships[0].type, MemoryRelationType.DERIVED_FROM)
            self.assertEqual(situations[0].relationships[0].target_id, question.id)

            history = StringIO()
            with redirect_stdout(history):
                history_exit = main(["--root", tmp, "question", "--include-retired", "--memory", question.id])

            self.assertEqual(history_exit, 0)
            self.assertIn(f"{question.id} [question/retired]", history.getvalue())
            self.assertIn("State: retired", history.getvalue())


class ScenarioEvaluationTests(unittest.TestCase):
    def test_cli_evaluate_scenario_proves_expected_action_note_without_mutating_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["preflight", "quiet"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "evaluate-scenario",
                        "implement CMU preflight behavior",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                        "--expect-trigger",
                        "must-call",
                        "--expect-action",
                        "action-note",
                        "--expect-memory",
                        memory.id,
                        "--expect-candidate",
                        "draft-recommended",
                        "--learning-signal",
                        "structural proof",
                        "--worked",
                        "The harness reused the real Work Cycle path.",
                        "--future-use",
                        "Use this scenario to verify CMU can prove task-start memory behavior.",
                        "--evidence",
                        "The scenario surfaced the expected Practice memory.",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Scenario Evaluation", rendered)
            self.assertIn("Mode: read-only structural proof", rendered)
            self.assertIn("Level: must-call", rendered)
            self.assertIn("Action: action-note", rendered)
            self.assertIn(f"Matched Memory: {memory.id}", rendered)
            self.assertIn("Receipt Signal: would-create-use-receipt", rendered)
            self.assertIn("- trigger: pass", rendered)
            self.assertIn("- action: pass", rendered)
            self.assertIn("- memory: pass", rendered)
            self.assertIn("- candidate: pass", rendered)
            self.assertIn("Candidate Memory: draft-recommended", rendered)
            self.assertIn("Verdict: supports-cmu-assumption", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_evaluate_scenario_proves_expected_quiet_silent_skip(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "evaluate-scenario",
                        "adjust button spacing",
                        "--actor",
                        "agent",
                        "--area",
                        "frontend",
                        "--file",
                        "settings.css",
                        "--workflow",
                        "styling",
                        "--risk",
                        "low",
                        "--expect-trigger",
                        "silent-skip",
                        "--expect-action",
                        "quiet",
                        "--expect-memory",
                        "none",
                        "--expect-candidate",
                        "not-recommended",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Level: silent-skip", rendered)
            self.assertIn("Onboarding Seed: skipped by silent-skip trigger", rendered)
            self.assertIn("Action: quiet", rendered)
            self.assertIn("Receipt Signal: none", rendered)
            self.assertIn("- trigger: pass", rendered)
            self.assertIn("- action: pass", rendered)
            self.assertIn("- memory: pass", rendered)
            self.assertIn("- candidate: pass", rendered)
            self.assertIn("Verdict: supports-cmu-assumption", rendered)

    def test_cli_evaluate_scenario_reports_gap_when_expected_memory_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "evaluate-scenario",
                        "debug unknown billing migration failure",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--workflow",
                        "debugging",
                        "--risk",
                        "high",
                        "--expect-trigger",
                        "must-call",
                        "--expect-action",
                        "action-note",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Level: must-call", rendered)
            self.assertIn("Action: quiet", rendered)
            self.assertIn("- trigger: pass", rendered)
            self.assertIn("- action: fail", rendered)
            self.assertIn("Verdict: cmu-gap-found", rendered)

    def test_cli_scenario_library_saves_lists_and_runs_repeatable_cases(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["preflight", "quiet"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            save_output = StringIO()
            with redirect_stdout(save_output):
                save_exit = main(
                    [
                        "--root",
                        tmp,
                        "scenario-add",
                        "implement CMU preflight behavior",
                        "--name",
                        "cmu preflight practice surfaces",
                        "--tag",
                        "regression",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--workflow",
                        "implementation",
                        "--risk",
                        "high",
                        "--expect-trigger",
                        "must-call",
                        "--expect-action",
                        "action-note",
                        "--expect-memory",
                        memory.id,
                        "--expect-candidate",
                        "draft-recommended",
                        "--learning-signal",
                        "structural proof",
                        "--worked",
                        "The library reused the evaluator.",
                        "--future-use",
                        "Use this scenario to keep task-start memory behavior stable.",
                        "--evidence",
                        "Expected Practice memory surfaced.",
                    ]
                )

            self.assertEqual(save_exit, 0)
            self.assertIn("CMU Scenario Saved", save_output.getvalue())

            list_output = StringIO()
            with redirect_stdout(list_output):
                list_exit = main(["--root", tmp, "scenario-list", "--tag", "regression"])

            self.assertEqual(list_exit, 0)
            self.assertIn("CMU Scenario Library", list_output.getvalue())
            self.assertIn("cmu preflight practice surfaces", list_output.getvalue())
            self.assertIn(f"memory={memory.id}", list_output.getvalue())

            run_output = StringIO()
            with redirect_stdout(run_output):
                run_exit = main(["--root", tmp, "scenario-run", "--tag", "regression", "--strict"])

            rendered = run_output.getvalue()
            self.assertEqual(run_exit, 0)
            self.assertIn("CMU Scenario Library Run", rendered)
            self.assertIn("Summary: total=1 pass=1 review=0", rendered)
            self.assertIn("pass:", rendered)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_scenario_run_strict_returns_review_when_saved_expectation_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            add_output = StringIO()
            with redirect_stdout(add_output):
                add_exit = main(
                    [
                        "--root",
                        tmp,
                        "scenario-add",
                        "debug unknown billing migration failure",
                        "--name",
                        "billing migration gap",
                        "--tag",
                        "gaps",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--workflow",
                        "debugging",
                        "--risk",
                        "high",
                        "--expect-trigger",
                        "must-call",
                        "--expect-action",
                        "action-note",
                    ]
                )

            self.assertEqual(add_exit, 0)

            run_output = StringIO()
            with redirect_stdout(run_output):
                run_exit = main(["--root", tmp, "scenario-run", "--tag", "gaps", "--strict"])

            rendered = run_output.getvalue()
            self.assertEqual(run_exit, 1)
            self.assertIn("Summary: total=1 pass=0 review=1", rendered)
            self.assertIn("review:", rendered)
            self.assertIn("failed=action", rendered)

    def test_scenario_comparison_reports_improvement_from_real_baseline_and_current_stores(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_root = root / "baseline"
            current_root = root / "current"
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Compare scenarios before trusting retrieval changes",
                summary="Scenario comparison should prove whether a memory-base change preserves expected task-start behavior.",
                signals=["scenario comparison", "retrieval"],
                scope=MemoryScope(code=["cmu/scenarios.py"], workflow=["hardening"], actor=["agent"]),
                evidence=["Before/after scenario proof should use real persisted stores."],
                use_this_path="Run scenario comparison before trusting retrieval or memory-base changes.",
                avoid_this="Do not rely on a single current-run scenario when a baseline store exists.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(current_root).add(memory)
            scenario = ScenarioDefinition.create(
                name="scenario comparison surfaces practice",
                prompt="implement CMU scenario comparison",
                actor="agent",
                area="cmu",
                files=["cmu/scenarios.py"],
                workflow=["hardening"],
                risk="high",
                expect_trigger="must-call",
                expect_action="action-note",
                expect_memory=memory.id,
            )

            report = compare_scenario_library(
                [scenario],
                baseline_memories=MemoryStore(baseline_root).list(),
                baseline_receipts=MemoryUseStore(baseline_root).list(),
                current_memories=MemoryStore(current_root).list(),
                current_receipts=MemoryUseStore(current_root).list(),
                baseline_root=str(baseline_root),
                current_root=str(current_root),
            )

            rendered = report.render()
            self.assertEqual(report.items[0].classification, "improved")
            self.assertFalse(report.has_regressions())
            self.assertIn("CMU Scenario Comparison", rendered)
            self.assertIn("Summary: total=1 regressed=0 improved=1", rendered)
            self.assertIn(f"current=supports-cmu-assumption/action-note/{memory.id}", rendered)
            self.assertEqual(MemoryUseStore(baseline_root).list(), [])
            self.assertEqual(MemoryUseStore(current_root).list(), [])

    def test_cli_scenario_compare_strict_fails_when_current_store_regresses_from_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_root = root / "baseline"
            current_root = root / "current"
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Keep scenario regression proof anchored",
                summary="Scenario comparison should fail strict mode when current memory behavior loses a passing baseline.",
                signals=["scenario compare", "regression"],
                scope=MemoryScope(code=["cmu/scenarios.py"], workflow=["hardening"], actor=["agent"]),
                evidence=["Regression comparison must execute the same saved scenario against both stores."],
                use_this_path="Treat baseline-pass to current-review as a regression before accepting the change.",
                avoid_this="Do not treat changed memory behavior as harmless when saved expectations fail.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(baseline_root).add(memory)
            scenario = ScenarioDefinition.create(
                name="scenario comparison catches lost memory",
                prompt="implement CMU scenario comparison regression check",
                actor="agent",
                area="cmu",
                files=["cmu/scenarios.py"],
                workflow=["hardening"],
                risk="high",
                expect_trigger="must-call",
                expect_action="action-note",
                expect_memory=memory.id,
                tags=["comparison"],
            )
            ScenarioLibraryStore(current_root).add(scenario)
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        str(current_root),
                        "scenario-compare",
                        "--baseline-root",
                        str(baseline_root),
                        "--tag",
                        "comparison",
                        "--strict",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 1, rendered)
            self.assertIn("CMU Scenario Comparison", rendered)
            self.assertIn("Summary: total=1 regressed=1 improved=0", rendered)
            self.assertIn("regressed:", rendered)
            self.assertIn(f"baseline=supports-cmu-assumption/action-note/{memory.id}", rendered)
            self.assertIn("current=cmu-gap-found/quiet/none", rendered)
            self.assertEqual(MemoryUseStore(baseline_root).list(), [])
            self.assertEqual(MemoryUseStore(current_root).list(), [])

    def test_fixture_repo_create_builds_real_repo_memory_and_passing_scenario(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "checkout-release"
            report = create_fixture_repo("checkout-release", fixture_root)
            rendered = report.render()

            self.assertIn("CMU Fixture Repository", rendered)
            self.assertEqual(report.kind, "checkout-release")
            self.assertTrue((fixture_root / ".git").exists())
            self.assertTrue((fixture_root / "src" / "checkout" / "release.py").exists())
            self.assertTrue((fixture_root / "tests" / "test_checkout_release.py").exists())
            memories = MemoryStore(fixture_root).list()
            scenarios = ScenarioLibraryStore(fixture_root).list(tag="fixture")
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0].id, report.memory_id)
            self.assertEqual(len(scenarios), 1)
            self.assertEqual(scenarios[0].expect_memory, report.memory_id)
            self.assertIn("runner-host-path", scenarios[0].tags)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", str(fixture_root), "scenario-run", "--tag", "fixture", "--strict"])

            scenario_rendered = output.getvalue()
            self.assertEqual(exit_code, 0, scenario_rendered)
            self.assertIn("CMU Scenario Library Run", scenario_rendered)
            self.assertIn("Summary: total=1 pass=1 review=0", scenario_rendered)
            self.assertEqual(MemoryUseStore(fixture_root).list(), [])

    def test_cli_fixture_repo_create_writes_fixture_and_refuses_non_empty_output(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "fixture"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "fixture-repo-create", "--kind", "checkout-release", "--output", str(fixture_root)])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Fixture Repository", rendered)
            self.assertIn("Kind: checkout-release", rendered)
            self.assertTrue((fixture_root / ".cmu" / "memories.json").exists())
            self.assertTrue((fixture_root / ".cmu" / "scenarios.json").exists())

            with self.assertRaises(SystemExit) as raised:
                main(["--root", tmp, "fixture-repo-create", "--kind", "checkout-release", "--output", str(fixture_root)])
            self.assertIn("fixture output directory already exists", str(raised.exception))

    def test_fixture_repo_catalog_includes_billing_incident_fixture_with_passing_scenario(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "billing-incident"
            report = create_fixture_repo("billing-incident", fixture_root)
            rendered = report.render()

            self.assertEqual(report.kind, "billing-incident")
            self.assertIn("Billing Incident Fixture", (fixture_root / "README.md").read_text(encoding="utf-8"))
            self.assertTrue((fixture_root / "src" / "billing" / "reconcile.py").exists())
            self.assertTrue((fixture_root / "tests" / "test_billing_reconcile.py").exists())
            self.assertIn("src/billing/reconcile.py", rendered)
            memories = MemoryStore(fixture_root).list()
            scenarios = ScenarioLibraryStore(fixture_root).list(tag="owner-review")
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0].authority_consequence, "critical")
            self.assertEqual(len(scenarios), 1)
            self.assertEqual(scenarios[0].expect_memory, report.memory_id)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", str(fixture_root), "scenario-run", "--tag", "fixture", "--strict"])

            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("Summary: total=1 pass=1 review=0", output.getvalue())

    def test_hardening_cycle_passes_five_real_operator_checks_without_source_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True, check=True)
            record = TeamScopeRecord.create(
                repo="billing-service",
                team="Billing",
                owner="Billing owner",
                code=["billing"],
                workflow=["incident"],
                environment=["prod"],
                authority_role="owner",
                consequence="critical",
            )
            TeamDirectoryStore(root).add(record)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Billing incident replay needs idempotency",
                summary="Billing incident replay should confirm idempotency keys before retrying reconciliation.",
                scope=MemoryScope(ownership=["Billing owner"], code=["billing"], workflow=["incident"], environment=["prod"]),
                evidence=["Incident replay duplicated invoice events until idempotency was checked."],
                use_this_path="Check idempotency keys before replaying reconciliation.",
                avoid_this="Do not replay billing events from logs without idempotency evidence.",
                challenge_only_if="Billing no longer supports replayed reconciliation.",
                liability_score=5,
                confidence=0.9,
                approved_by="Billing owner",
            )
            MemoryStore(root).add(memory)
            MemoryUseStore(root).init()
            fixture_dir = root / "portable-fixtures"
            fixture_dir.mkdir()
            bundle = export_bundle_from_root(root).to_dict()
            (fixture_dir / "valid-current-v1.json").write_text(json.dumps(bundle), encoding="utf-8")
            invalid = json.loads(json.dumps(bundle))
            invalid["integrity"]["memory_count"] = 99
            (fixture_dir / "invalid-bad-count.json").write_text(json.dumps(invalid), encoding="utf-8")
            future = json.loads(json.dumps(bundle))
            future["schema"] = "cmu-portable-bundle/v2"
            (fixture_dir / "future-v2.json").write_text(json.dumps(future), encoding="utf-8")
            before_memories = (root / ".cmu" / "memories.json").read_text(encoding="utf-8")
            before_team_scopes = (root / ".cmu" / "team_scopes.json").read_text(encoding="utf-8")
            before_uses = (root / ".cmu" / "uses.json").read_text(encoding="utf-8")

            report = hardening_cycle_report(
                root,
                MemoryStore(root).list(),
                MemoryUseStore(root).list(),
                team_scopes=TeamDirectoryStore(root).list(),
                portable_fixture_dir=fixture_dir,
            )
            rendered = report.render()

            self.assertTrue(report.passed, rendered)
            self.assertIn("CMU Hardening Cycle", rendered)
            self.assertIn("[pass] team-owner-review", rendered)
            self.assertIn("[pass] evidence-session-monitor", rendered)
            self.assertIn("[pass] fixture-host-path-catalog", rendered)
            self.assertIn("[pass] portable-migration-fixtures", rendered)
            self.assertIn("[pass] review-reminder-delivery", rendered)
            self.assertIn("cmu review-reminders --json", rendered)
            self.assertEqual((root / ".cmu" / "memories.json").read_text(encoding="utf-8"), before_memories)
            self.assertEqual((root / ".cmu" / "team_scopes.json").read_text(encoding="utf-8"), before_team_scopes)
            self.assertEqual((root / ".cmu" / "uses.json").read_text(encoding="utf-8"), before_uses)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", str(root), "hardening-cycle", "--portable-fixture-dir", str(fixture_dir), "--strict"])

            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("Status: pass", output.getvalue())

    def test_hardening_cycle_strict_fails_when_portable_fixtures_are_not_provided(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True, check=True)
            TeamDirectoryStore(root).add(
                TeamScopeRecord.create(
                    repo="checkout-service",
                    team="Checkout",
                    owner="Checkout owner",
                    code=["checkout"],
                    workflow=["rollback"],
                    authority_role="owner",
                    consequence="high",
                )
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", str(root), "hardening-cycle", "--strict"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 1, rendered)
            self.assertIn("Status: review", rendered)
            self.assertIn("[review] portable-migration-fixtures", rendered)


class MemoryUseTests(unittest.TestCase):
    def test_memory_use_receipt_defaults_old_records_to_preflight_source(self) -> None:
        receipt = MemoryUseReceipt.from_dict(
            {
                "id": "use_old",
                "memory_id": "mem_old",
                "memory_title": "Old receipt",
                "prompt": "Fix billing deploy",
                "actor": "agent",
                "area": "billing",
                "files": ["billing/deploy.py"],
                "risk": "high",
                "match_score": 2.5,
            }
        )

        self.assertEqual(receipt.source_command, "preflight")
        self.assertEqual(receipt.semantic_mode, "off")
        self.assertEqual(receipt.semantic_label, "unavailable")
        self.assertEqual(receipt.semantic_score, 0.0)

    def test_cli_preflight_records_semantic_receipt_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                signals=["rollback", "releasemarker"],
                scope=MemoryScope(workflow=["deployment"], actor=["agent"]),
                evidence=["Clearing the stale releasemarker let the rollout retry finish."],
                use_this_path="Check stale releasemarkers before retrying rollout.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "preflight",
                        "roll back release marker problem",
                        "--actor",
                        "agent",
                        "--workflow",
                        "deploy",
                        "--risk",
                        "high",
                        "--semantic",
                        "local",
                    ]
                )

            receipts = MemoryUseStore(tmp).list()
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].semantic_mode, "local")
            self.assertEqual(receipts[0].semantic_label, "local hashing embeddings")
            self.assertGreater(receipts[0].semantic_score, 0.0)
            self.assertEqual(receipts[0].semantic_proposal_status, "admissible")

            use_list = StringIO()
            with redirect_stdout(use_list):
                list_exit = main(["--root", tmp, "use-list"])

            self.assertEqual(list_exit, 0)
            self.assertIn("semantic=local", use_list.getvalue())

    def test_memory_use_store_preserves_concurrent_adds(self) -> None:
        with TemporaryDirectory() as tmp:
            use_store = MemoryUseStore(tmp)

            def add_receipt(index: int) -> str:
                receipt = MemoryUseReceipt(
                    id=f"use_concurrent_{index}",
                    memory_id=f"mem_{index}",
                    memory_title=f"Memory {index}",
                    prompt=f"Concurrent task {index}",
                    actor="agent",
                    area="cmu",
                    files=["cmu/cli.py"],
                    risk="high",
                    match_score=2.5,
                    source_command="start",
                )
                use_store.add(receipt)
                return receipt.id

            with ThreadPoolExecutor(max_workers=8) as executor:
                expected_ids = set(executor.map(add_receipt, range(24)))

            loaded_ids = {receipt.id for receipt in MemoryUseStore(tmp).list()}
            self.assertEqual(loaded_ids, expected_ids)

    def test_link_commit_marks_mixed_commit_with_lower_confidence(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(
                prompt="Fix billing deployment migration failure",
                actor="agent",
                area="billing",
                files=["billing/deploy.py"],
                risk="high",
            ),
            match=type("MatchStub", (), {"score": 4.2})(),
        )

        decision = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=receipt.id,
                commit_hash="abc123",
                message="Fix billing deploy",
                files=["billing/deploy.py", "auth/tokens.py", "ui/settings.css", "README.md"],
            ),
        )

        self.assertTrue(decision.linked)
        self.assertEqual(receipt.outcome_signal, "committed")
        self.assertIn("mixed_commit", receipt.flags)
        self.assertLess(receipt.link_confidence, 0.85)

    def test_link_commit_marks_no_file_context_and_no_overlap(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
            match=type("MatchStub", (), {"score": 4.2})(),
        )

        no_context = link_commit(
            receipt,
            CommitLinkRequest(use_id=receipt.id, commit_hash="abc123", message="Fix billing deploy"),
        )

        self.assertTrue(no_context.linked)
        self.assertIn("no_commit_file_context", receipt.flags)
        self.assertEqual(receipt.outcome_signal, "committed")
        self.assertLess(receipt.link_confidence, 0.5)

        no_overlap = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=receipt.id,
                commit_hash="def456",
                message="Fix unrelated frontend settings",
                files=["ui/settings.css"],
            ),
        )

        self.assertTrue(no_overlap.linked)
        self.assertIn("no_file_overlap", receipt.flags)
        self.assertEqual(receipt.outcome_signal, "committed_low_confidence")
        self.assertLess(receipt.link_confidence, 0.4)

    def test_link_commit_detects_wip_and_revert_signals(self) -> None:
        memory = Memory.create(
            type=MemoryType.ANCHOR,
            title="Auth token rotation ordering",
            summary="Token rotation must acquire the lock before updating active credentials.",
            scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
            liability_score=5,
            confidence=0.85,
        )
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Change auth token rotation", actor="agent", area="auth", files=["auth/tokens.py"], risk="high"),
            match=type("MatchStub", (), {"score": 5.0})(),
        )

        wip = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=receipt.id,
                commit_hash="abc123",
                message="WIP auth token rotation checkpoint",
                files=["auth/tokens.py"],
            ),
        )

        self.assertTrue(wip.linked)
        self.assertEqual(receipt.outcome_signal, "checkpoint")
        self.assertIn("wip_commit", receipt.flags)
        self.assertLessEqual(receipt.link_confidence, 0.55)

        reverted = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=receipt.id,
                commit_hash="def456",
                message="Revert auth token rotation checkpoint",
                files=["auth/tokens.py"],
            ),
        )

        self.assertTrue(reverted.linked)
        self.assertEqual(receipt.outcome_signal, "reverted")
        self.assertIn("reverted_after_use", receipt.flags)
        self.assertLessEqual(receipt.link_confidence, 0.2)

    def test_link_commit_detects_delayed_commit(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
            match=type("MatchStub", (), {"score": 4.2})(),
        )
        receipt.surfaced_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")

        decision = link_commit(
            receipt,
            CommitLinkRequest(
                use_id=receipt.id,
                commit_hash="abc123",
                message="Fix billing deploy",
                files=["billing/deploy.py"],
                commit_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

        self.assertTrue(decision.linked)
        self.assertIn("delayed_commit", receipt.flags)
        self.assertLess(receipt.link_confidence, 0.85)

    def test_cli_use_link_persists_commit_signal(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-link",
                        receipt.id,
                        "--commit",
                        "abc123",
                        "--manual",
                        "--message",
                        "Fix billing deploy",
                        "--file",
                        "billing/deploy.py",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Link Applied", output.getvalue())
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.commit_hash, "abc123")
            self.assertEqual(linked.outcome_signal, "committed")
            self.assertGreaterEqual(linked.link_confidence, 0.8)

    def test_memory_use_store_round_trips_git_metadata_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", files=["billing/deploy.py"]),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.commit_hash = "abc123"
            receipt.commit_message = "Fix billing deploy"
            receipt.commit_files = ["billing/deploy.py"]
            receipt.commit_time = "2026-05-18T12:00:00+00:00"
            receipt.metadata_source = "git"
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.85
            receipt.flags = ["mixed_commit"]
            MemoryUseStore(tmp).add(receipt)

            [loaded] = MemoryUseStore(tmp).list()

            self.assertEqual(loaded.commit_hash, "abc123")
            self.assertEqual(loaded.commit_time, "2026-05-18T12:00:00+00:00")
            self.assertEqual(loaded.metadata_source, "git")
            self.assertEqual(loaded.flags, ["mixed_commit"])

    def test_inspect_git_commit_reads_message_time_and_all_changed_files(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_files_and_commit(
                tmp,
                {
                    "billing/deploy.py": "deploy = true\n",
                    "billing/schema.sql": "select 1;\n",
                },
                ["Fix billing deploy", "Body line for checkpoint context."],
            )

            metadata = inspect_git_commit(tmp, "HEAD")

            self.assertRegex(metadata.commit_hash, r"^[0-9a-f]{40}$")
            self.assertIn("Fix billing deploy", metadata.message)
            self.assertIn("Body line for checkpoint context.", metadata.message)
            self.assertRegex(metadata.commit_time, r"^\d{4}-\d{2}-\d{2}T")
            self.assertEqual(metadata.files, ["billing/deploy.py", "billing/schema.sql"])

    def test_cli_use_link_reads_git_metadata_for_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link", receipt.id, "--commit", "HEAD"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Link Applied", output.getvalue())
            [linked] = MemoryUseStore(tmp).list()
            self.assertNotEqual(linked.commit_hash, "HEAD")
            self.assertEqual(linked.commit_message, "Fix billing deploy")
            self.assertEqual(linked.commit_files, ["billing/deploy.py"])
            self.assertEqual(linked.metadata_source, "git")
            self.assertEqual(linked.outcome_signal, "committed")
            self.assertGreaterEqual(linked.link_confidence, 0.8)

    def test_cli_use_link_git_failure_does_not_mutate_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", files=["billing/deploy.py"]),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link", receipt.id, "--commit", "HEAD"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Link Not Applied", output.getvalue())
            self.assertIn("git metadata unavailable", output.getvalue())
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "")
            self.assertEqual(loaded.outcome_signal, "")
            self.assertEqual(loaded.flags, [])

    def test_git_metadata_error_explains_inaccessible_parent_repo(self) -> None:
        rendered = format_git_metadata_error(
            "C:/project",
            "fatal: cannot change to 'C:/Users/chait'",
        )

        self.assertIn("git metadata unavailable", rendered)
        self.assertIn("root=", rendered)
        self.assertIn("project", rendered)
        self.assertIn("cannot access from the CMU root", rendered)
        self.assertIn("manual commit metadata", rendered)

    def test_cli_use_link_git_metadata_can_be_overridden_for_known_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "generated/bundle.js", "bundle\n", "Fix billing deploy")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-link",
                        receipt.id,
                        "--commit",
                        "HEAD",
                        "--file",
                        "billing/deploy.py",
                        "--message",
                        "Fix billing deploy with generated output",
                    ]
                )

            self.assertEqual(exit_code, 0)
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.metadata_source, "git")
            self.assertEqual(linked.commit_files, ["billing/deploy.py"])
            self.assertEqual(linked.commit_message, "Fix billing deploy with generated output")
            self.assertEqual(linked.outcome_signal, "committed")
            self.assertGreaterEqual(linked.link_confidence, 0.8)

    def test_cli_use_link_latest_reads_head_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "auth/tokens.py", "locked = true\n", "WIP auth token checkpoint")
            memory = Memory.create(
                type=MemoryType.ANCHOR,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                liability_score=5,
                confidence=0.85,
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Change auth token rotation", actor="agent", area="auth", files=["auth/tokens.py"], risk="high"),
                match=type("MatchStub", (), {"score": 5.0})(),
            )
            MemoryUseStore(tmp).add(receipt)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-latest", receipt.id])

            self.assertEqual(exit_code, 0)
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.metadata_source, "git")
            self.assertEqual(linked.outcome_signal, "checkpoint")
            self.assertIn("wip_commit", linked.flags)

    def test_cli_use_link_auto_dry_run_does_not_mutate_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-auto"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Link Auto Dry Run", output.getvalue())
            self.assertIn("would link", output.getvalue())
            self.assertIn("link score", output.getvalue())
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "")

    def test_cli_use_link_auto_apply_links_confident_match(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-auto", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Link Auto Applied", output.getvalue())
            self.assertIn("link score", output.getvalue())
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.commit_hash, metadata.commit_hash)
            self.assertEqual(linked.metadata_source, "git-auto")
            self.assertIn("auto_linked", linked.flags)
            self.assertGreaterEqual(linked.link_confidence, 0.8)

    def test_cli_use_link_auto_leaves_ambiguous_receipt_unlinked(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            first = inspect_git_commit(tmp, "HEAD")
            write_and_commit(tmp, "billing/deploy.py", "deploy = false\n", "Fix billing deploy")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(first, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-auto", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("multiple commits were plausible", output.getvalue())
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "")

    def test_cli_use_link_auto_applies_split_credit_for_shared_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            use_store = MemoryUseStore(tmp)
            for prompt in ["Fix billing deploy", "Repair billing deployment"]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt=prompt, actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.surfaced_at = before_commit(metadata, minutes=30)
                use_store.add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-auto", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("split-credit", output.getvalue())
            linked = MemoryUseStore(tmp).list()
            self.assertEqual(len(linked), 2)
            self.assertTrue(all(receipt.commit_hash == metadata.commit_hash for receipt in linked))
            self.assertTrue(all("split_credit" in receipt.flags for receipt in linked))

    def test_cli_use_link_auto_surfaces_drag_review_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            MemoryStore(tmp).add(memory)
            for signal, flags in [
                ("reverted", ["reverted_after_use"]),
                ("committed_low_confidence", ["no_file_overlap"]),
            ]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"{signal}123"
                receipt.outcome_signal = signal
                receipt.flags = flags
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-link-auto"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Review Prompts", output.getvalue())
            self.assertIn("drag signals", output.getvalue())

    def test_evidence_monitor_applies_only_clean_high_confidence_checkpoint_match(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                signals=["billing", "deploy"],
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            dry = monitor_checkpoints(tmp, MemoryStore(tmp).list(), MemoryUseStore(tmp).list())

            self.assertEqual(dry.linked_count, 1, dry.render())
            self.assertFalse(dry.applied)
            [loaded_after_dry] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded_after_dry.commit_hash, "")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-monitor", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0, rendered)
            self.assertIn("CMU Evidence Monitor Applied", rendered)
            self.assertIn("linked=1", rendered)
            self.assertIn("high-confidence clean checkpoint match", rendered)
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.commit_hash, metadata.commit_hash)
            self.assertEqual(linked.metadata_source, "git-monitor")
            self.assertEqual(linked.outcome_signal, "committed")
            self.assertGreaterEqual(linked.link_confidence, 0.75)
            self.assertEqual(linked.flags, [])

    def test_cli_evidence_monitor_leaves_wip_checkpoint_for_review_without_mutating_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "auth/tokens.py", "locked = true\n", "WIP auth token checkpoint")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.ANCHOR,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                signals=["auth", "token"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                liability_score=5,
                confidence=0.85,
                approved_by="Security owner",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Change auth token rotation", actor="agent", area="auth", files=["auth/tokens.py"], risk="high"),
                match=type("MatchStub", (), {"score": 5.0})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-monitor", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0, rendered)
            self.assertIn("CMU Evidence Monitor Applied", rendered)
            self.assertIn("linked=0", rendered)
            self.assertIn("needs_review=1", rendered)
            self.assertIn("wip_commit", rendered)
            self.assertIn("outcome:checkpoint", rendered)
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "")
            self.assertEqual(loaded.outcome_signal, "")
            self.assertEqual(loaded.flags, [])

    def test_cli_use_resolve_marks_receipt_without_commit_and_surfaces_resolution(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Update docs after semantic audit", actor="agent", area="cmu", files=["CMU_Implementation_Progress.md"]),
                match=Match(
                    memory=memory,
                    score=4.8,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.66,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-resolve",
                        receipt.id,
                        "--outcome",
                        "no-checkpoint",
                        "--note",
                        "Strategic markdown is intentionally ignored by Git.",
                        "--resolved-by",
                        "CMU Test",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Receipt Resolution Applied", rendered)
            self.assertIn("Outcome: no_checkpoint", rendered)
            self.assertIn("No Commit Linked", rendered)
            [resolved] = MemoryUseStore(tmp).list()
            self.assertEqual(resolved.commit_hash, "")
            self.assertEqual(resolved.outcome_signal, "no_checkpoint")
            self.assertEqual(resolved.metadata_source, "CMU Test")
            self.assertIn("resolved_without_commit", resolved.flags)

            list_output = StringIO()
            with redirect_stdout(list_output):
                self.assertEqual(main(["--root", tmp, "use-list"]), 0)
            self.assertIn(f"{receipt.id} start no_checkpoint unlinked", list_output.getvalue())

            summary_output = StringIO()
            with redirect_stdout(summary_output):
                self.assertEqual(main(["--root", tmp, "use-summary", memory.id]), 0)
            self.assertIn("Resolved Without Commit: 1", summary_output.getvalue())

            review_output = StringIO()
            with redirect_stdout(review_output):
                self.assertEqual(main(["--root", tmp, "use-review", memory.id]), 0)
            self.assertIn("1 resolved-without-commit", review_output.getvalue())
            self.assertIn("0 drag signals", review_output.getvalue())

            audit_output = StringIO()
            with redirect_stdout(audit_output):
                self.assertEqual(main(["--root", tmp, "semantic-audit", "--memory", memory.id]), 0)
            audit_rendered = audit_output.getvalue()
            self.assertIn("Semantic-Assisted Linked: 1", audit_rendered)
            self.assertIn("Semantic-Assisted Resolved Without Commit: 1", audit_rendered)
            self.assertIn("Semantic-Assisted Unresolved: 0", audit_rendered)
            self.assertIn("resolved without commit evidence", audit_rendered)

            recommendations_output = StringIO()
            with redirect_stdout(recommendations_output):
                self.assertEqual(main(["--root", tmp, "semantic-audit", "--recommendations", "--details"]), 0)
            recommendations_rendered = recommendations_output.getvalue()
            self.assertIn("Neutral Linked Semantic Evidence", recommendations_rendered)
            self.assertIn(f"{memory.id} Task-start preflight stays quiet unless useful: keep observing; semantic receipts were resolved without commit evidence", recommendations_rendered)
            self.assertIn(f"{receipt.id} start resolved-without-commit; semantic=local/direct-match score=0.66", recommendations_rendered)
            self.assertIn("Resolution: no_checkpoint; resolved-by=CMU Test", recommendations_rendered)
            self.assertNotIn("Link Receipts First\n- " + memory.id, recommendations_rendered)

    def test_cli_use_resolve_refuses_receipt_that_already_has_commit_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Committed receipt memory",
                summary="Receipt already has commit evidence.",
            )
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix committed thing"),
                match=Match(memory=memory, score=3.2, matched_terms=["fix"]),
            )
            receipt.commit_hash = "abc123"
            receipt.outcome_signal = "committed"
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-resolve",
                        receipt.id,
                        "--outcome",
                        "not-applicable",
                        "--note",
                        "Should not replace commit evidence.",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Receipt Resolution Not Applied", output.getvalue())
            self.assertIn("already has a linked commit", output.getvalue())
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "abc123")
            self.assertEqual(loaded.outcome_signal, "committed")

    def test_usage_adjustment_is_capped_for_repeated_positive_and_negative_signals(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
        )
        positive_receipts = []
        for _ in range(10):
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.95
            positive_receipts.append(receipt)
        negative_receipts = []
        for _ in range(10):
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.outcome_signal = "reverted"
            receipt.link_confidence = 0.2
            receipt.flags = ["reverted_after_use", "no_file_overlap"]
            negative_receipts.append(receipt)

        self.assertEqual(usage_adjustment(positive_receipts), 0.8)
        self.assertEqual(usage_adjustment(negative_receipts), -0.8)

    def test_cli_use_summary_renders_persisted_signal_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            committed = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            committed.outcome_signal = "committed"
            committed.link_confidence = 0.85
            reverted = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            reverted.outcome_signal = "reverted"
            reverted.link_confidence = 0.2
            reverted.flags = ["reverted_after_use"]
            MemoryUseStore(tmp).add(committed)
            MemoryUseStore(tmp).add(reverted)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-summary", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Total Uses: 2", rendered)
            self.assertIn("Committed: 1", rendered)
            self.assertIn("Reverted: 1", rendered)

    def test_cli_use_summary_renders_persisted_source_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            for source in ["preflight", "start", "start"]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                    source_command=source,
                )
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-summary", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Total Uses: 3", rendered)
            self.assertIn("Sources: preflight=1, start=2", rendered)

    def test_cli_use_summary_renders_semantic_provenance_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
            )
            off_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix rollout"),
                match=type("MatchStub", (), {"score": 3.1})(),
            )
            semantic_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="roll back release marker problem"),
                match=type(
                    "MatchStub",
                    (),
                    {
                        "score": 3.8,
                        "semantic_label": "local hashing embeddings",
                        "semantic_score": 0.72,
                        "semantic_proposal_status": "admissible",
                    },
                )(),
                semantic_mode="local",
            )
            MemoryUseStore(tmp).add(off_receipt)
            MemoryUseStore(tmp).add(semantic_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-summary", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic Modes: local=1, off=1", rendered)
            self.assertIn("Semantic Matches: admissible=1, off=1", rendered)

    def test_cli_use_review_surfaces_strong_usefulness_card(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"abc12{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review"])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("CMU Use Review", rendered)
            self.assertIn("Strengthen evidence suggested", rendered)
            self.assertIn("2 high-confidence committed uses", rendered)

    def test_cli_use_review_shows_receipt_source_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should surface memory only when it changes action.",
            )
            MemoryStore(tmp).add(memory)
            for source in ["preflight", "start", "start"]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Implement CMU start", files=["cmu/cli.py"]),
                    match=type("MatchStub", (), {"score": 4.2})(),
                    source_command=source,
                )
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Sources: preflight=1, start=2", rendered)
            self.assertIn("Needs linked evidence", rendered)

    def test_cli_use_review_interprets_semantic_strong_usefulness(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=type(
                        "MatchStub",
                        (),
                        {
                            "score": 3.8,
                            "semantic_label": "local hashing embeddings",
                            "semantic_score": 0.7,
                            "semantic_proposal_status": "admissible",
                        },
                    )(),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semantic{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic Modes: local=2", rendered)
            self.assertIn("Semantic Matches: admissible=2", rendered)
            self.assertIn("2 strong committed uses came from semantic-assisted receipts", rendered)

    def test_cli_use_review_surfaces_drag_card_for_stable_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            MemoryStore(tmp).add(memory)
            for signal, flags in [
                ("reverted", ["reverted_after_use"]),
                ("committed_low_confidence", ["no_file_overlap"]),
            ]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"{signal}123"
                receipt.outcome_signal = signal
                receipt.flags = flags
                receipt.link_confidence = 0.2
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review"])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Review suggested", rendered)
            self.assertIn("2 drag signals across 2 linked uses", rendered)
            self.assertIn("challenge or retirement", rendered)
            self.assertIn("Do Not Auto-Mutate", rendered)

    def test_cli_use_review_interprets_semantic_drag_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker cleanup practice.",
            )
            MemoryStore(tmp).add(memory)
            for index, signal in enumerate(["reverted", "committed_low_confidence"]):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=type(
                        "MatchStub",
                        (),
                        {
                            "score": 3.8,
                            "semantic_label": "local hashing embeddings",
                            "semantic_score": 0.72,
                            "semantic_proposal_status": "admissible",
                        },
                    )(),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticdrag{index}"
                receipt.outcome_signal = signal
                receipt.flags = ["reverted_after_use"] if signal == "reverted" else ["no_file_overlap"]
                receipt.link_confidence = 0.2
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic Modes: local=2", rendered)
            self.assertIn("Semantic Matches: admissible=2", rendered)
            self.assertIn("2 drag signals came from semantic-assisted receipts", rendered)
            self.assertIn("inspect semantic grounding before changing trust", rendered)

    def test_cli_use_review_explains_mixed_commit_drag(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should surface memory only when it changes action.",
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Improve use-review wording", files=["cmu/usage.py"]),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"mixed{index}"
                receipt.outcome_signal = "committed"
                receipt.commit_files = ["cmu/usage.py", "cmu/cli.py", "tests/test_cmu_spine.py", "pyproject.toml"]
                receipt.flags = ["mixed_commit"]
                receipt.link_confidence = 0.65
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("2 drag signals across 2 linked uses", rendered)
            self.assertIn("Mixed commits are weak evidence", rendered)
            self.assertIn("inspect scope before tuning thresholds", rendered)

    def test_cli_use_review_separates_mixed_drag_from_strong_focused_uses(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should surface memory only when it changes action.",
            )
            MemoryStore(tmp).add(memory)
            for index in range(3):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Initialize CMU", files=["cmu/usage.py"]),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"mixed{index}"
                receipt.outcome_signal = "committed"
                receipt.flags = ["mixed_commit"]
                receipt.link_confidence = 0.65
                MemoryUseStore(tmp).add(receipt)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Clarify use-review output", files=["cmu/usage.py"]),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"strong{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("all from mixed commits", rendered)
            self.assertIn("2 strong focused uses", rendered)
            self.assertIn("Inspect broad mixed commits before challenging this stable memory", rendered)

    def test_cli_use_review_specific_memory_shows_unlinked_state(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy debugging",
                summary="Billing deploy failures often need migration checks.",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Needs linked evidence", rendered)
            self.assertIn("Run use-link-auto or use-link", rendered)

    def test_cli_use_review_stays_quiet_when_no_memory_has_review_signal(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy debugging",
                summary="Billing deploy failures often need migration checks.",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.commit_hash = "abc123"
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.85
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review"])

            self.assertEqual(exit_code, 0)
            self.assertIn("No memory use review cards found", output.getvalue())

    def test_cli_use_review_thresholds_reports_empty_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", "--thresholds"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Threshold Report", rendered)
            self.assertIn("Mode: diagnostic only", rendered)
            self.assertIn("Auto-Link Apply Candidate: score >= 0.55", rendered)
            self.assertIn("Functionality: No use receipts yet", rendered)
            self.assertIn("Accuracy: Not enough evidence", rendered)
            self.assertIn("No Memory Use Receipts found", rendered)

    def test_cli_use_review_thresholds_reports_real_receipt_behavior(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            useful = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy checks migration order",
                summary="Billing deploy work should check migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            )
            noisy = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            )
            store.add(useful)
            store.add(noisy)
            add_strong_receipts(tmp, useful, count=2)
            add_drag_receipts(tmp, noisy, count=2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", "--thresholds"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Threshold Report", rendered)
            self.assertIn("Strengthen Review: 2+ strong committed uses and 0 drag signals", rendered)
            self.assertIn("Drag Review: 2+ drag signals", rendered)
            self.assertIn("Functionality: Use receipts and linked commit/checkpoint signals exist", rendered)
            self.assertIn("Accuracy: Enough linked evidence for a first-pass threshold review", rendered)
            self.assertIn("Billing deploy checks migration order: Strengthen evidence suggested", rendered)
            self.assertIn("Do migration before deploy: Review suggested", rendered)
            self.assertIn("Sources: preflight=4", rendered)
            self.assertIn("sources preflight=2", rendered)
            self.assertIn("Do Not Auto-Mutate", rendered)

    def test_cli_use_review_thresholds_shows_mixed_source_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="CMU start work cycle",
                summary="Start coordinates trigger, onboarding, and preflight.",
            )
            MemoryStore(tmp).add(memory)
            for source in ["preflight", "start", "start"]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Implement CMU start", files=["cmu/cli.py"]),
                    match=type("MatchStub", (), {"score": 4.2})(),
                    source_command=source,
                )
                receipt.commit_hash = f"{source}{len(MemoryUseStore(tmp).list())}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", "--thresholds"])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("- Sources: preflight=1, start=2", rendered)
            self.assertIn("sources preflight=1, start=2", rendered)

    def test_cli_use_review_thresholds_shows_semantic_provenance_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="roll back release marker problem"),
                match=type(
                    "MatchStub",
                    (),
                    {
                        "score": 3.8,
                        "semantic_label": "local hashing embeddings",
                        "semantic_score": 0.72,
                        "semantic_proposal_status": "admissible",
                    },
                )(),
                semantic_mode="local",
            )
            receipt.commit_hash = "semanticstrong"
            receipt.outcome_signal = "committed"
            receipt.link_confidence = 0.85
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", "--thresholds"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("- Semantic Modes: local=1", rendered)
            self.assertIn("- Semantic Matches: admissible=1", rendered)
            self.assertIn("semantic local=1 / admissible=1", rendered)

    def test_cli_use_review_thresholds_rejects_mutating_options(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as apply_context:
                main(["--root", tmp, "use-review", "--thresholds", "--apply"])
            self.assertIn("diagnostic only", str(apply_context.exception))

            with self.assertRaises(SystemExit) as prepare_context:
                main(["--root", tmp, "use-review", "--thresholds", "--prepare", "strengthen"])
            self.assertIn("diagnostic only", str(prepare_context.exception))

            with self.assertRaises(SystemExit) as memory_context:
                main(["--root", tmp, "use-review", "mem_123", "--thresholds"])
            self.assertIn("does not accept a memory id", str(memory_context.exception))

    def test_cli_semantic_audit_reports_no_semantic_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Audit", rendered)
            self.assertIn("Mode: read-only", rendered)
            self.assertIn("Semantic-Assisted Receipts: 0", rendered)
            self.assertIn("Recommended Action: No semantic-assisted receipts yet", rendered)

    def test_cli_semantic_audit_reports_strong_and_drag_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            strong_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
            )
            drag_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Retry marker cleanup default",
                summary="A stale marker cleanup default.",
            )
            store.add(strong_memory)
            store.add(drag_memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    strong_memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=Match(
                        memory=strong_memory,
                        score=3.8,
                        matched_terms=["semantic:workflow scope"],
                        semantic_label="local hashing embeddings",
                        semantic_score=0.72,
                        semantic_proposal_status="admissible",
                    ),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticstrong{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)
            drag_receipt = MemoryUseReceipt.create(
                drag_memory,
                PreflightQuery(prompt="roll back release marker problem"),
                match=Match(
                    memory=drag_memory,
                    score=3.6,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.68,
                    semantic_proposal_status="admissible",
                ),
                semantic_mode="local",
            )
            drag_receipt.commit_hash = "semanticdrag"
            drag_receipt.outcome_signal = "committed_low_confidence"
            drag_receipt.flags = ["no_file_overlap"]
            drag_receipt.link_confidence = 0.2
            MemoryUseStore(tmp).add(drag_receipt)
            off_receipt = MemoryUseReceipt.create(
                strong_memory,
                PreflightQuery(prompt="direct match"),
                match=Match(memory=strong_memory, score=2.5, matched_terms=["direct"]),
            )
            MemoryUseStore(tmp).add(off_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Total Receipts: 4", rendered)
            self.assertIn("Semantic-Assisted Receipts: 3", rendered)
            self.assertIn("Semantic-Assisted Linked: 3", rendered)
            self.assertIn("Semantic-Assisted Strong Committed: 2", rendered)
            self.assertIn("Semantic-Assisted Drag Signals: 1", rendered)
            self.assertIn("Semantic Modes: local=3", rendered)
            self.assertIn("Semantic Matches: admissible=3", rendered)
            self.assertIn(f"{strong_memory.id} Rollback releasemarker cleanup: 2 semantic-assisted strong committed uses", rendered)
            self.assertIn(f"{drag_memory.id} Retry marker cleanup default: 1 semantic-assisted drag signals", rendered)
            self.assertIn("Recommended Action: Semantic retrieval has positive linked evidence", rendered)

    def test_cli_semantic_audit_memory_filters_to_one_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            target_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Credential rotation lock order",
                summary="Credential rotation needs lock ordering before secret updates.",
            )
            other_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Release marker cleanup",
                summary="Release retries should clean stale markers.",
            )
            store.add(target_memory)
            store.add(other_memory)
            strong_receipt = MemoryUseReceipt.create(
                target_memory,
                PreflightQuery(prompt="fix credential rotation race"),
                match=Match(
                    memory=target_memory,
                    score=3.9,
                    matched_terms=["semantic:scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.74,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            strong_receipt.commit_hash = "targetstrong"
            strong_receipt.outcome_signal = "committed"
            strong_receipt.link_confidence = 0.9
            MemoryUseStore(tmp).add(strong_receipt)
            direct_receipt = MemoryUseReceipt.create(
                target_memory,
                PreflightQuery(prompt="direct credential check"),
                match=Match(memory=target_memory, score=2.5, matched_terms=["credential"]),
            )
            MemoryUseStore(tmp).add(direct_receipt)
            other_receipt = MemoryUseReceipt.create(
                other_memory,
                PreflightQuery(prompt="fix release marker retry"),
                match=Match(
                    memory=other_memory,
                    score=3.8,
                    matched_terms=["semantic:scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.71,
                    semantic_proposal_status="admissible",
                ),
                semantic_mode="local",
            )
            other_receipt.commit_hash = "otherdrag"
            other_receipt.outcome_signal = "committed_low_confidence"
            other_receipt.flags = ["no_file_overlap"]
            other_receipt.link_confidence = 0.2
            MemoryUseStore(tmp).add(other_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--memory", target_memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory: {target_memory.id} Credential rotation lock order", rendered)
            self.assertIn("Total Receipts: 2", rendered)
            self.assertIn("Semantic-Assisted Receipts: 1", rendered)
            self.assertIn("Semantic-Assisted Linked: 1", rendered)
            self.assertIn("Semantic-Assisted Strong Committed: 1", rendered)
            self.assertIn("Semantic-Assisted Drag Signals: 0", rendered)
            self.assertIn("Semantic Matches: direct-match=1", rendered)
            self.assertIn(f"{target_memory.id} Credential rotation lock order: 1 semantic-assisted strong committed uses", rendered)
            self.assertNotIn(other_memory.id, rendered)

    def test_cli_semantic_audit_memory_reports_no_semantic_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Direct-only deploy memory",
                summary="Deploy memory with only direct receipt evidence.",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="fix deploy"),
                match=Match(memory=memory, score=2.8, matched_terms=["deploy"]),
            )
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--memory", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory: {memory.id} Direct-only deploy memory", rendered)
            self.assertIn("Total Receipts: 1", rendered)
            self.assertIn("Semantic-Assisted Receipts: 0", rendered)
            self.assertIn("Recommended Action: No semantic-assisted receipts for this memory yet", rendered)

    def test_cli_semantic_audit_recommendations_groups_memory_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            unlinked_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Unlinked semantic memory",
                summary="Semantic receipts exist but are not linked.",
            )
            strong_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Strong semantic memory",
                summary="Semantic receipts reached strong committed evidence.",
            )
            drag_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Drag semantic memory",
                summary="Semantic receipts produced drag.",
            )
            neutral_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Neutral semantic memory",
                summary="Semantic receipts are linked but not strong or drag.",
            )
            quiet_memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Quiet semantic memory",
                summary="No semantic receipt evidence exists.",
            )
            for memory in [unlinked_memory, strong_memory, drag_memory, neutral_memory, quiet_memory]:
                store.add(memory)

            unlinked_receipt = MemoryUseReceipt.create(
                unlinked_memory,
                PreflightQuery(prompt="semantic unlinked"),
                match=Match(
                    memory=unlinked_memory,
                    score=3.2,
                    matched_terms=["semantic"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="admissible",
                ),
                semantic_mode="local",
            )
            MemoryUseStore(tmp).add(unlinked_receipt)
            strong_receipt = MemoryUseReceipt.create(
                strong_memory,
                PreflightQuery(prompt="semantic strong"),
                match=Match(
                    memory=strong_memory,
                    score=3.4,
                    matched_terms=["semantic"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.72,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            strong_receipt.commit_hash = "strongsemantic"
            strong_receipt.outcome_signal = "committed"
            strong_receipt.link_confidence = 0.86
            MemoryUseStore(tmp).add(strong_receipt)
            drag_receipt = MemoryUseReceipt.create(
                drag_memory,
                PreflightQuery(prompt="semantic drag"),
                match=Match(
                    memory=drag_memory,
                    score=3.1,
                    matched_terms=["semantic"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.67,
                    semantic_proposal_status="admissible",
                ),
                semantic_mode="local",
            )
            drag_receipt.commit_hash = "dragsemantic"
            drag_receipt.outcome_signal = "committed_low_confidence"
            drag_receipt.flags = ["no_file_overlap"]
            drag_receipt.link_confidence = 0.2
            MemoryUseStore(tmp).add(drag_receipt)
            neutral_receipt = MemoryUseReceipt.create(
                neutral_memory,
                PreflightQuery(prompt="semantic neutral"),
                match=Match(
                    memory=neutral_memory,
                    score=3.0,
                    matched_terms=["semantic"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.65,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            neutral_receipt.commit_hash = "neutralsemantic"
            neutral_receipt.outcome_signal = "checkpoint"
            neutral_receipt.link_confidence = 0.5
            MemoryUseStore(tmp).add(neutral_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--recommendations"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Audit Recommendations", rendered)
            self.assertIn("Mode: read-only", rendered)
            self.assertIn("Link Receipts First", rendered)
            self.assertIn(f"{unlinked_memory.id} Unlinked semantic memory: link receipts first", rendered)
            self.assertIn("Inspect Semantic Drag", rendered)
            self.assertIn(f"{drag_memory.id} Drag semantic memory: inspect semantic grounding", rendered)
            self.assertIn("Positive Semantic Signal", rendered)
            self.assertIn(f"{strong_memory.id} Strong semantic memory: keep collecting focused evidence", rendered)
            self.assertIn("Neutral Linked Semantic Evidence", rendered)
            self.assertIn(f"{neutral_memory.id} Neutral semantic memory: keep observing", rendered)
            self.assertIn("No Semantic Evidence", rendered)
            self.assertIn(f"{quiet_memory.id} Quiet semantic memory: stay quiet", rendered)
            self.assertIn("Do not tune thresholds or broaden semantic proposal behavior", rendered)

    def test_cli_semantic_audit_recommendations_details_explain_unlinked_semantic_receipt_candidates(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "detail = 1\n", "Add semantic audit details")
            first = inspect_git_commit(tmp, "HEAD")
            write_and_commit(tmp, "cmu/usage.py", "detail = 2\n", "Add semantic audit details")
            second = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(
                    prompt="implement semantic audit details",
                    actor="agent",
                    area="cmu",
                    files=["cmu/usage.py"],
                    workflow=["implementation"],
                    risk="medium",
                ),
                match=Match(
                    memory=memory,
                    score=5.0,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.72,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            receipt.surfaced_at = before_commit(first, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--recommendations", "--details"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Audit Recommendations", rendered)
            self.assertIn(f"{memory.id} Task-start preflight stays quiet unless useful: link receipts first", rendered)
            self.assertIn(f"{receipt.id} start unlinked; semantic=local/direct-match score=0.72", rendered)
            self.assertIn("Auto-Link: multiple commits were plausible; leaving receipt unlinked", rendered)
            self.assertIn(f"Manual Link: cmu use-link {receipt.id} --commit <hash>", rendered)
            self.assertIn("Candidate Commits", rendered)
            self.assertIn(short_test_hash(first.commit_hash), rendered)
            self.assertIn(short_test_hash(second.commit_hash), rendered)
            self.assertIn(f"command: cmu use-link {receipt.id} --commit {first.commit_hash}", rendered)
            self.assertIn(f"command: cmu use-link {receipt.id} --commit {second.commit_hash}", rendered)
            self.assertIn("message: Add semantic audit details", rendered)
            self.assertIn("overlap: cmu/usage.py", rendered)
            self.assertIn("reasons: time window, file overlap: cmu/usage.py", rendered)
            self.assertIn("No-Commit Resolution Options", rendered)
            self.assertIn(f"command: cmu use-resolve {receipt.id} --outcome no-checkpoint", rendered)
            self.assertIn(f"command: cmu use-resolve {receipt.id} --outcome not-applicable", rendered)
            self.assertIn(f"command: cmu use-resolve {receipt.id} --outcome superseded", rendered)
            [loaded] = MemoryUseStore(tmp).list()
            self.assertEqual(loaded.commit_hash, "")

    def test_cli_semantic_audit_recommendations_details_separates_partial_semantic_evidence_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add semantic audit positive evidence")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            linked_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic audit positive evidence", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=5.0,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.74,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            linked_receipt.commit_hash = metadata.commit_hash
            linked_receipt.outcome_signal = "committed"
            linked_receipt.link_confidence = 0.9
            MemoryUseStore(tmp).add(linked_receipt)
            unresolved_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic audit follow up", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.8,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.7,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            unresolved_receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(unresolved_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--recommendations", "--details"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Resolve Remaining Semantic Evidence", rendered)
            self.assertIn(f"{memory.id} Task-start preflight stays quiet unless useful: resolve remaining semantic receipts before judging semantic fit", rendered)
            self.assertIn("(2 semantic receipts, 1 linked, 0 resolved, 1 unresolved, 1 strong, 0 drag)", rendered)
            self.assertIn(f"{linked_receipt.id} preflight linked; semantic=local/direct-match score=0.74", rendered)
            self.assertIn(f"{unresolved_receipt.id} start unlinked; semantic=local/direct-match score=0.70", rendered)
            self.assertIn(f"command: cmu use-resolve {unresolved_receipt.id} --outcome no-checkpoint", rendered)
            self.assertNotIn(f"Positive Semantic Signal\n- {memory.id}", rendered)

    def test_cli_semantic_audit_recommendations_open_only_shows_only_unresolved_details(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add semantic evidence closure")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            linked_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic linked", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=5.0,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.74,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            linked_receipt.commit_hash = metadata.commit_hash
            linked_receipt.outcome_signal = "committed"
            linked_receipt.link_confidence = 0.9
            MemoryUseStore(tmp).add(linked_receipt)
            resolved_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic docs only", actor="agent", area="cmu", files=["CMU_Implementation_Progress.md"]),
                match=Match(
                    memory=memory,
                    score=4.8,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.7,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            resolved_receipt.outcome_signal = "no_checkpoint"
            resolved_receipt.flags = ["resolved_without_commit"]
            resolved_receipt.metadata_source = "CMU Test"
            MemoryUseStore(tmp).add(resolved_receipt)
            unresolved_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic unresolved", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            unresolved_receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(unresolved_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "semantic-audit", "--recommendations", "--details", "--open-only"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Detail Filter: open semantic receipts only.", rendered)
            self.assertIn("(3 semantic receipts, 2 linked, 1 resolved, 1 unresolved, 1 strong, 0 drag)", rendered)
            self.assertIn(f"{unresolved_receipt.id} start unlinked; semantic=local/direct-match score=0.69", rendered)
            self.assertIn(f"command: cmu use-resolve {unresolved_receipt.id} --outcome no-checkpoint", rendered)
            self.assertNotIn(f"{linked_receipt.id} preflight linked", rendered)
            self.assertNotIn(f"{resolved_receipt.id} preflight resolved-without-commit", rendered)

    def test_cli_semantic_audit_commands_only_renders_closure_commands_for_unresolved_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add semantic command closure")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            linked_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic linked", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=5.0,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.74,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            linked_receipt.commit_hash = metadata.commit_hash
            linked_receipt.outcome_signal = "committed"
            linked_receipt.link_confidence = 0.9
            MemoryUseStore(tmp).add(linked_receipt)
            unresolved_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic command closure", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            unresolved_receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(unresolved_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Semantic Audit Closure Commands", rendered)
            self.assertIn(f"# {unresolved_receipt.id} start semantic=local/direct-match", rendered)
            self.assertIn(f"cmu use-link {unresolved_receipt.id} --commit {metadata.commit_hash}", rendered)
            self.assertIn(f"cmu use-resolve {unresolved_receipt.id} --outcome no-checkpoint", rendered)
            self.assertIn("Review before running", rendered)
            self.assertNotIn("Resolve Remaining Semantic Evidence", rendered)
            self.assertNotIn(linked_receipt.id, rendered)

    def test_cli_semantic_audit_commands_only_honors_candidate_tuning(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add semantic candidate tuning")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic command closure", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--min-score",
                        "99",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Candidate Window: limit=20, hours=72, min-score=99.00", rendered)
            self.assertIn(f"# {receipt.id} start semantic=local/direct-match", rendered)
            self.assertNotIn(f"cmu use-link {receipt.id}", rendered)
            self.assertIn(f"cmu use-resolve {receipt.id} --outcome no-checkpoint", rendered)

    def test_cli_semantic_audit_receipt_filter_focuses_closure_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add focused semantic closure")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            first_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic first closure", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            first_receipt.surfaced_at = before_commit(metadata, minutes=30)
            second_receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic second closure", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.68,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            second_receipt.surfaced_at = before_commit(metadata, minutes=20)
            use_store = MemoryUseStore(tmp)
            use_store.add(first_receipt)
            use_store.add(second_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--receipt",
                        second_receipt.id,
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Receipt Filter: {second_receipt.id}", rendered)
            self.assertIn(f"# {second_receipt.id} start semantic=local/direct-match", rendered)
            self.assertIn(f"cmu use-link {second_receipt.id} --commit {metadata.commit_hash}", rendered)
            self.assertNotIn(first_receipt.id, rendered)

    def test_cli_semantic_audit_candidate_limit_caps_plausible_commits(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = 1\n", "Add first semantic candidate")
            write_and_commit(tmp, "cmu/usage.py", "semantic = 2\n", "Add second semantic candidate")
            write_and_commit(tmp, "cmu/usage.py", "semantic = 3\n", "Add third semantic candidate")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic command closure", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--candidate-limit",
                        "1",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("candidate-limit=1", rendered)
            self.assertEqual(rendered.count(f"cmu use-link {receipt.id} --commit"), 1)
            self.assertIn(f"cmu use-resolve {receipt.id} --outcome no-checkpoint", rendered)

    def test_cli_semantic_audit_action_filter_focuses_recommendation_bucket(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add semantic partial evidence")
            metadata = inspect_git_commit(tmp, "HEAD")
            no_link_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Unlinked semantic practice",
                summary="A semantic practice with only open receipts.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            partial_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Partial semantic practice",
                summary="A semantic practice with linked and open receipts.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            store = MemoryStore(tmp)
            store.add(no_link_memory)
            store.add(partial_memory)
            no_link_receipt = MemoryUseReceipt.create(
                no_link_memory,
                PreflightQuery(prompt="semantic unlinked", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=no_link_memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            no_link_receipt.surfaced_at = before_commit(metadata, minutes=30)
            linked_partial_receipt = MemoryUseReceipt.create(
                partial_memory,
                PreflightQuery(prompt="semantic linked partial", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=partial_memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.72,
                    semantic_proposal_status="direct-match",
                ),
                semantic_mode="local",
            )
            linked_partial_receipt.commit_hash = metadata.commit_hash
            linked_partial_receipt.outcome_signal = "committed"
            linked_partial_receipt.link_confidence = 0.9
            unresolved_partial_receipt = MemoryUseReceipt.create(
                partial_memory,
                PreflightQuery(prompt="semantic unresolved partial", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=partial_memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.68,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            unresolved_partial_receipt.surfaced_at = before_commit(metadata, minutes=20)
            use_store = MemoryUseStore(tmp)
            use_store.add(no_link_receipt)
            use_store.add(linked_partial_receipt)
            use_store.add(unresolved_partial_receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--action",
                        "partial",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Action Filter: partial", rendered)
            self.assertIn(unresolved_partial_receipt.id, rendered)
            self.assertNotIn(no_link_receipt.id, rendered)

    def test_cli_semantic_audit_command_type_filters_closure_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add command type filter")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic command filter", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--command-type",
                        "link",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Command Filter: link", rendered)
            self.assertIn(f"cmu use-link {receipt.id} --commit {metadata.commit_hash}", rendered)
            self.assertNotIn(f"cmu use-resolve {receipt.id}", rendered)

    def test_cli_semantic_audit_link_command_filter_hides_receipts_without_link_candidates(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic command filter", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--command-type",
                        "link",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("No unresolved semantic receipt commands found.", rendered)
            self.assertNotIn(receipt.id, rendered)

    def test_cli_semantic_audit_resolve_outcome_filters_resolution_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "cmu/usage.py", "semantic = true\n", "Add resolve outcome filter")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="Preflight should surface compact memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                liability_score=4,
                confidence=0.9,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="semantic resolve filter", actor="agent", area="cmu", files=["cmu/usage.py"]),
                match=Match(
                    memory=memory,
                    score=4.7,
                    matched_terms=["semantic:workflow scope"],
                    semantic_label="local hashing embeddings",
                    semantic_score=0.69,
                    semantic_proposal_status="direct-match",
                ),
                source_command="start",
                semantic_mode="local",
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--open-only",
                        "--commands-only",
                        "--command-type",
                        "resolve",
                        "--resolve-outcome",
                        "superseded",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Command Filter: resolve", rendered)
            self.assertIn("Resolve Outcome Filter: superseded", rendered)
            self.assertIn(f"cmu use-resolve {receipt.id} --outcome superseded", rendered)
            self.assertNotIn(f"cmu use-link {receipt.id}", rendered)
            self.assertNotIn(f"cmu use-resolve {receipt.id} --outcome no-checkpoint", rendered)
            self.assertNotIn(f"cmu use-resolve {receipt.id} --outcome not-applicable", rendered)

    def test_cli_semantic_audit_recommendations_rejects_memory_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--memory", "mem_123"])

            self.assertIn("does not accept --memory", str(context.exception))

    def test_cli_semantic_audit_details_requires_recommendations(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--details"])

            self.assertIn("--details is only available with --recommendations", str(context.exception))

    def test_cli_semantic_audit_open_only_requires_details(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--open-only"])

            self.assertIn("--open-only is only available with --recommendations --details", str(context.exception))

    def test_cli_semantic_audit_commands_only_requires_open_details(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--details", "--commands-only"])

            self.assertIn("--commands-only requires --recommendations --details --open-only", str(context.exception))

    def test_cli_semantic_audit_receipt_filter_requires_details(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--receipt", "use_123"])

            self.assertIn("--receipt requires --recommendations --details", str(context.exception))

    def test_cli_semantic_audit_candidate_tuning_requires_details(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--min-score", "0.1"])

            self.assertIn("candidate tuning requires --recommendations --details", str(context.exception))

    def test_cli_semantic_audit_action_filter_requires_recommendations(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--action", "link"])

            self.assertIn("--action requires --recommendations", str(context.exception))

    def test_cli_semantic_audit_candidate_limit_rejects_negative_values(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(
                    [
                        "--root",
                        tmp,
                        "semantic-audit",
                        "--recommendations",
                        "--details",
                        "--candidate-limit",
                        "-1",
                    ]
                )

            self.assertIn("--candidate-limit must be zero or greater", str(context.exception))

    def test_cli_semantic_audit_command_type_requires_commands_only(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--details", "--command-type", "link"])

            self.assertIn("--command-type requires --commands-only", str(context.exception))

    def test_cli_semantic_audit_resolve_outcome_requires_commands_only(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as context:
                main(["--root", tmp, "semantic-audit", "--recommendations", "--details", "--resolve-outcome", "superseded"])

            self.assertIn("--resolve-outcome requires --commands-only", str(context.exception))

    def test_cli_use_review_prepare_strengthen_proposal_does_not_mutate(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy debugging",
                summary="Billing deploy failures often need migration checks.",
                confidence=0.7,
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"abc12{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "strengthen"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Proposal", output.getvalue())
            self.assertIn("Use-review strengthened evidence", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.evidence, [])
            self.assertEqual(loaded.confidence, 0.7)

    def test_cli_use_review_prepare_strengthen_apply_requires_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy debugging",
                summary="Billing deploy failures often need migration checks.",
                confidence=0.7,
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"abc12{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "strengthen", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Missing: approved_by", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.evidence, [])

    def test_cli_use_review_prepare_strengthen_apply_adds_approved_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                confidence=0.75,
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"abc12{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-review",
                        memory.id,
                        "--prepare",
                        "strengthen",
                        "--apply",
                        "--approved-by",
                        "CMU owner",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Applied", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertIn("Use-review evidence approved by: CMU owner", loaded.evidence)
            self.assertGreater(loaded.confidence, 0.75)

    def test_cli_use_review_prepare_strengthen_includes_semantic_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                confidence=0.7,
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=Match(
                        memory=memory,
                        score=3.8,
                        matched_terms=["semantic:workflow scope"],
                        semantic_label="local hashing embeddings",
                        semantic_score=0.72,
                        semantic_proposal_status="admissible",
                    ),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticstrong{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "strengthen"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", rendered)
            self.assertIn("Semantic-assisted strong committed uses: 2.", rendered)
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.evidence, [])

    def test_cli_use_review_prepare_strengthen_apply_persists_semantic_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker blocked rollout retries.",
                confidence=0.72,
            )
            MemoryStore(tmp).add(memory)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=Match(
                        memory=memory,
                        score=3.8,
                        matched_terms=["semantic:workflow scope"],
                        semantic_label="local hashing embeddings",
                        semantic_score=0.72,
                        semantic_proposal_status="admissible",
                    ),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticstrong{index}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.85
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-review",
                        memory.id,
                        "--prepare",
                        "strengthen",
                        "--apply",
                        "--approved-by",
                        "CMU owner",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Applied", rendered)
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", rendered)
            [loaded] = MemoryStore(tmp).list()
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", loaded.evidence)
            self.assertIn("Semantic-assisted strong committed uses: 2.", loaded.evidence)
            self.assertIn("Use-review evidence approved by: CMU owner", loaded.evidence)
            self.assertGreater(loaded.confidence, 0.72)

    def test_cli_use_review_prepare_challenge_apply_saves_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
            )
            MemoryStore(tmp).add(memory)
            for signal, flags in [
                ("reverted", ["reverted_after_use"]),
                ("committed_low_confidence", ["no_file_overlap"]),
            ]:
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="Fix billing deploy"),
                    match=type("MatchStub", (), {"score": 4.2})(),
                )
                receipt.commit_hash = f"{signal}123"
                receipt.outcome_signal = signal
                receipt.flags = flags
                receipt.link_confidence = 0.2
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "challenge", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Applied", output.getvalue())
            candidates = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(candidates), 1)
            self.assertIn("practice challenge", candidates[0].signals)
            self.assertIn(f"Challenges stable memory: {memory.id}", candidates[0].evidence)
            practices = MemoryStore(tmp).list(type=MemoryType.PRACTICE)
            self.assertEqual(len(practices), 1)

    def test_cli_use_review_prepare_challenge_includes_semantic_provenance_in_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker cleanup practice.",
            )
            MemoryStore(tmp).add(memory)
            for index, signal in enumerate(["reverted", "committed_low_confidence"]):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=Match(
                        memory=memory,
                        score=3.8,
                        matched_terms=["semantic:workflow scope"],
                        semantic_label="local hashing embeddings",
                        semantic_score=0.72,
                        semantic_proposal_status="admissible",
                    ),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticdrag{index}"
                receipt.outcome_signal = signal
                receipt.flags = ["reverted_after_use"] if signal == "reverted" else ["no_file_overlap"]
                receipt.link_confidence = 0.2
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "challenge", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", rendered)
            self.assertIn("Semantic-assisted drag signals: 2.", rendered)
            candidates = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(candidates), 1)
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", candidates[0].evidence)
            self.assertIn("Semantic-assisted drag signals: 2.", candidates[0].evidence)

    def test_cli_use_review_prepare_scope_review_is_proposal_only(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            )
            MemoryStore(tmp).add(memory)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "scope-review", "--apply"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Proposal", output.getvalue())
            self.assertIn("Current scope: billing, deployment", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.scope.code, ["billing"])

    def test_cli_use_review_prepare_scope_review_includes_semantic_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Rollback releasemarker cleanup",
                summary="A stale releasemarker cleanup practice.",
                scope=MemoryScope(workflow=["deployment"]),
            )
            MemoryStore(tmp).add(memory)
            for index, signal in enumerate(["reverted", "committed_low_confidence"]):
                receipt = MemoryUseReceipt.create(
                    memory,
                    PreflightQuery(prompt="roll back release marker problem"),
                    match=Match(
                        memory=memory,
                        score=3.8,
                        matched_terms=["semantic:workflow scope"],
                        semantic_label="local hashing embeddings",
                        semantic_score=0.72,
                        semantic_proposal_status="admissible",
                    ),
                    semantic_mode="local",
                )
                receipt.commit_hash = f"semanticscope{index}"
                receipt.outcome_signal = signal
                receipt.flags = ["reverted_after_use"] if signal == "reverted" else ["no_file_overlap"]
                receipt.link_confidence = 0.2
                MemoryUseStore(tmp).add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "use-review", memory.id, "--prepare", "scope-review"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Semantic provenance: modes local=2; matches admissible=2.", rendered)
            self.assertIn("Semantic-assisted drag signals: 2.", rendered)
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.scope.workflow, ["deployment"])

    def test_cli_use_review_prepare_scope_review_apply_requires_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            )
            MemoryStore(tmp).add(memory)
            add_drag_receipts(tmp, memory, count=2)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-review",
                        memory.id,
                        "--prepare",
                        "scope-review",
                        "--apply",
                        "--scope-code",
                        "billing/deploy.py",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("approved scope review requires explicit owner/team approval", output.getvalue())
            self.assertIn("Missing: approved_by", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.scope.code, ["billing"])

    def test_cli_use_review_prepare_scope_review_apply_narrows_stable_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            )
            MemoryStore(tmp).add(memory)
            add_drag_receipts(tmp, memory, count=2)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-review",
                        memory.id,
                        "--prepare",
                        "scope-review",
                        "--apply",
                        "--approved-by",
                        "CMU core owner",
                        "--scope-code",
                        "billing/deploy.py",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Use Review Follow-Up Applied", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.scope.code, ["billing/deploy.py"])
            self.assertEqual(loaded.scope.workflow, ["deployment"])
            self.assertIn("Scope adjusted from use-review by: CMU core owner", loaded.evidence)

    def test_cli_use_review_prepare_scope_review_refuses_stable_broadening(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.ANCHOR,
                title="Billing deploy ordering",
                summary="Billing deploys should respect migration order.",
                scope=MemoryScope(code=["billing/deploy.py"], workflow=["deployment"]),
            )
            MemoryStore(tmp).add(memory)
            add_drag_receipts(tmp, memory, count=2)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "use-review",
                        memory.id,
                        "--prepare",
                        "scope-review",
                        "--apply",
                        "--approved-by",
                        "CMU core owner",
                        "--scope-code",
                        "billing",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("stable memory scope changes that broaden or shift scope require the challenge or split path", output.getvalue())
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.scope.code, ["billing/deploy.py"])

    def test_use_summary_counts_commit_signals(self) -> None:
        memory = Memory.create(
            type=MemoryType.PRACTICE,
            title="Do migration before deploy",
            summary="Deploys should respect database migration order.",
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            liability_score=5,
            confidence=0.9,
        )
        committed = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", files=["billing/deploy.py"]),
            match=type("MatchStub", (), {"score": 4.2})(),
        )
        committed.outcome_signal = "committed"
        committed.link_confidence = 0.85
        mixed = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", files=["billing/deploy.py"]),
            match=type("MatchStub", (), {"score": 4.2})(),
        )
        mixed.outcome_signal = "reverted"
        mixed.link_confidence = 0.2
        mixed.flags = ["reverted_after_use", "mixed_commit"]

        summary = use_summary([committed, mixed], memory.id)

        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.committed, 1)
        self.assertEqual(summary.reverted, 1)
        self.assertEqual(summary.mixed, 1)
        self.assertEqual(summary.source_counts, {"preflight": 2})
        self.assertLess(summary.retrieval_adjustment, 0.1)


class RememberingTests(unittest.TestCase):
    def test_remember_candidate_for_reusable_situational_intelligence(self) -> None:
        decision = remember_candidate(
            [],
            RememberRequest(
                title="Store init should create missing root",
                situation="The smoke test failed because init created .cmu but not the missing root directory.",
                signals=["explained failure"],
                outcome="Creating the root before .cmu fixed the CLI init path.",
                worked="Create the configured root directory before creating the .cmu store.",
                failed="Assuming the caller already created the root caused a setup failure.",
                future_use="Use when adding commands that accept custom local store roots.",
                evidence=["Smoke test failed before the root creation fix."],
                liability_score=3,
                suggested_next_type=MemoryType.SITUATION,
                scope=MemoryScope(code=["cmu/store.py"], workflow=["local setup"], actor=["agent"]),
                confidence=0.75,
            ),
        )

        self.assertTrue(decision.saved)
        self.assertIsNotNone(decision.memory)
        assert decision.memory is not None
        self.assertEqual(decision.memory.type, MemoryType.CANDIDATE)
        self.assertGreaterEqual(decision.liability_score, 3)

    def test_remember_rejects_low_liability_without_trigger(self) -> None:
        decision = remember_candidate(
            [],
            RememberRequest(
                situation="Routine formatting typo fix in a local comment.",
                future_use="Use only if this exact comment typo appears again.",
                liability_score=1,
                scope=MemoryScope(code=["README.md"]),
            ),
        )

        self.assertFalse(decision.saved)
        self.assertIn("worked_or_failed", decision.render())

    def test_remember_rejects_likely_duplicate(self) -> None:
        existing = Memory.create(
            type=MemoryType.SITUATION,
            title="Migration order matters",
            summary="Deploy failed because service code ran before billing migration.",
            signals=["migration", "deploy", "explained failure"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            liability_score=4,
            confidence=0.8,
        )

        decision = remember_candidate(
            [existing],
            RememberRequest(
                situation="Billing deploy failed because service code ran before migration.",
                signals=["explained failure"],
                future_use="Use when billing deploy order fails again.",
                evidence=["Deploy log showed service code before migration."],
                liability_score=4,
                scope=MemoryScope(code=["billing"], workflow=["deployment"]),
            ),
        )

        self.assertFalse(decision.saved)
        self.assertIn("duplicate", decision.render().lower())

    def test_remember_allows_distinct_memory_with_common_cmu_terms(self) -> None:
        existing = Memory.create(
            type=MemoryType.CANDIDATE,
            title="Stable Practice and Anchor memory mutation must happen",
            summary="Stable Practice and Anchor memory mutation must happen only through approved challenge resolution details, not from the original challenge text alone.",
            signals=[],
            scope=MemoryScope(code=["cmu/challenges.py", "cmu/cli.py"], workflow=["stable-memory-resolution"], actor=["agent"]),
            evidence=["Unit tests cover update, retire, split, missing mutating details, and CLI split persistence."],
            use_this_path="Require update, retire, and split outcomes to provide explicit approved resolution details plus resolution evidence.",
            avoid_this="Letting planned mutating outcomes infer too much from the challenge would make stable memory too easy to rewrite.",
            challenge_only_if="Use when adding or changing CMU stable-memory mutation paths.",
            liability_score=4,
            confidence=0.85,
        )

        decision = remember_candidate(
            [existing],
            RememberRequest(
                situation="Threshold diagnostics had use receipts but no linked commit evidence, while automatic Git linking could not inspect the repository root.",
                signals=["tooling quirk"],
                worked="Report functionality readiness separately from accuracy readiness and keep automatic linking non-mutating on Git metadata failure.",
                failed="Treating receipt count alone as accuracy evidence would overstate confidence and lead to premature threshold tuning.",
                future_use="Use when CMU has receipt history but cannot judge matching or review accuracy because checkpoint links are missing.",
                evidence=["use-review --thresholds reported receipts with zero linked checkpoint signals."],
                liability_score=4,
                scope=MemoryScope(code=["cmu/usage.py"], workflow=["threshold-diagnostics"], actor=["agent"]),
                confidence=0.85,
            ),
        )

        self.assertTrue(decision.saved)


class CliRememberTests(unittest.TestCase):
    def test_cli_remember_adds_candidate_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "remember",
                        "--situation",
                        "A dependency version quirk caused repeated test errors after failed attempts.",
                        "--signal",
                        "repeated error",
                        "--signal",
                        "tooling quirk",
                        "--worked",
                        "Pin the tool version before rerunning tests.",
                        "--future-use",
                        "Use when the same dependency version mismatch appears.",
                        "--evidence",
                        "Tests passed after pinning the version.",
                        "--liability",
                        "4",
                        "--scope-code",
                        "tools",
                        "--scope-workflow",
                        "testing",
                        "--scope-actor",
                        "agent",
                        "--confidence",
                        "0.8",
                    ]
                )

            self.assertEqual(exit_code, 0)
            memories = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(memories), 1)


class RawTraceDistillationTests(unittest.TestCase):
    def test_cli_trace_distill_apply_saves_candidate_from_reusable_raw_trace(self) -> None:
        with TemporaryDirectory() as tmp:
            capture_output = StringIO()
            with redirect_stdout(capture_output):
                capture_code = main(
                    [
                        "--root",
                        tmp,
                        "trace-add",
                        "Billing deploy failed because service rollout ran before schema migration.",
                        "--actor",
                        "agent",
                        "--area",
                        "billing",
                        "--file",
                        "billing/deploy.py",
                        "--workflow",
                        "deployment",
                        "--risk",
                        "high",
                        "--learning-signal",
                        "explained failure",
                        "--outcome",
                        "Deployment passed after migration order was corrected.",
                        "--worked",
                        "Run billing schema migration before service rollout.",
                        "--failed",
                        "Rolling out service code first caused the deploy failure.",
                        "--future-use",
                        "Use when billing deployment or migration ordering changes.",
                        "--evidence",
                        "Deploy log showed service code started before schema compatibility.",
                    ]
                )

            self.assertEqual(capture_code, 0)
            capture_rendered = capture_output.getvalue()
            self.assertIn("CMU Raw Trace Captured", capture_rendered)
            self.assertIn("candidate-ready", capture_rendered)
            traces = RawTraceStore(tmp).list()
            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0].status, "raw")

            distill_output = StringIO()
            with redirect_stdout(distill_output):
                distill_code = main(["--root", tmp, "trace-distill", "--apply"])

            self.assertEqual(distill_code, 0)
            distill_rendered = distill_output.getvalue()
            self.assertIn("CMU Raw Trace Distillation", distill_rendered)
            self.assertIn("Mode: apply", distill_rendered)
            self.assertIn("Candidate Ready: 1", distill_rendered)
            memories = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(memories), 1)
            self.assertIn("Billing deploy failed", memories[0].summary)
            self.assertIn("explained failure", memories[0].signals)
            self.assertTrue(any(item.startswith("Distilled from raw trace:") for item in memories[0].evidence))
            updated_trace = RawTraceStore(tmp).get(traces[0].id)
            self.assertEqual(updated_trace.status, "candidate-saved")
            self.assertEqual(updated_trace.distilled_memory_id, memories[0].id)

            with redirect_stdout(StringIO()):
                rerun_code = main(["--root", tmp, "trace-distill", traces[0].id, "--apply"])

            self.assertEqual(rerun_code, 0)
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 1)
            self.assertEqual(RawTraceStore(tmp).get(traces[0].id).distilled_memory_id, memories[0].id)

    def test_cli_trace_distill_apply_rejects_routine_noise_without_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            with redirect_stdout(StringIO()):
                capture_code = main(
                    [
                        "--root",
                        tmp,
                        "trace-add",
                        "Fixed a local formatting typo in a comment.",
                        "--actor",
                        "agent",
                        "--area",
                        "docs",
                        "--file",
                        "README.md",
                        "--workflow",
                        "cleanup",
                        "--risk",
                        "low",
                    ]
                )

            self.assertEqual(capture_code, 0)
            trace = RawTraceStore(tmp).list()[0]
            output = StringIO()
            with redirect_stdout(output):
                distill_code = main(["--root", tmp, "trace-distill", trace.id, "--apply"])

            rendered = output.getvalue()
            self.assertEqual(distill_code, 0)
            self.assertIn("Noise Rejected: 1", rendered)
            self.assertIn("Missing required Candidate Memory fields", rendered)
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE), [])
            updated_trace = RawTraceStore(tmp).get(trace.id)
            self.assertEqual(updated_trace.status, "rejected")
            self.assertEqual(updated_trace.distilled_memory_id, "")


class DocumentCurationTests(unittest.TestCase):
    def test_cli_doc_curate_preview_is_read_only_and_apply_saves_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "CMU_Implementation_Progress.md"
            skipped_path = Path(tmp) / "CMU_Product_Spec_Outline.md"
            doc_path.write_text(
                "\n".join(
                    [
                        "# CMU Implementation Progress",
                        "",
                        "Decision: markdown curation should treat docs as evidence, not authority.",
                        "Known gap: stale docs can create retrieval drag if imported blindly.",
                        "Next best implementation slice: build a curation gate before seeding memory.",
                    ]
                ),
                encoding="utf-8",
            )
            skipped_path.write_text(
                "\n".join(
                    [
                        "# CMU Product Spec Outline",
                        "",
                        "Decision: product integration should keep authority and governance visible.",
                        "Readiness and lifecycle evidence should shape the next best implementation slice.",
                    ]
                ),
                encoding="utf-8",
            )

            preview_output = StringIO()
            with redirect_stdout(preview_output):
                preview_code = main(["--root", tmp, "doc-curate", str(doc_path), str(skipped_path)])

            self.assertEqual(preview_code, 0)
            preview_rendered = preview_output.getvalue()
            self.assertIn("CMU Document History Curation", preview_rendered)
            self.assertIn("Mode: preview", preview_rendered)
            self.assertIn("Candidate Ready: 2", preview_rendered)
            self.assertIn("Applied Candidates: 0", preview_rendered)
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE), [])

            apply_output = StringIO()
            with redirect_stdout(apply_output):
                apply_code = main(
                    [
                        "--root",
                        tmp,
                        "doc-curate",
                        str(doc_path),
                        str(skipped_path),
                        "--select",
                        "CMU_Implementation_Progress.md",
                        "--apply",
                    ]
                )

            self.assertEqual(apply_code, 0)
            apply_rendered = apply_output.getvalue()
            self.assertIn("Mode: apply", apply_rendered)
            self.assertIn("Selection Filter: CMU_Implementation_Progress.md", apply_rendered)
            self.assertIn("Applied Candidates: 1", apply_rendered)
            memories = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(memories), 1)
            self.assertIn("Curated doc evidence", memories[0].title)
            self.assertIn("CMU_Implementation_Progress.md", memories[0].scope.code)
            self.assertNotIn("CMU_Product_Spec_Outline.md", memories[0].scope.code)
            self.assertIn("documentation-curation", memories[0].scope.workflow)
            self.assertTrue(any("Curated from markdown document" in item for item in memories[0].evidence))

    def test_cli_doc_curate_rejects_stale_and_superseded_markdown(self) -> None:
        with TemporaryDirectory() as tmp:
            stale_path = Path(tmp) / "old_plan.md"
            stale_path.write_text(
                "# Historical Notes\n\nDecision and practice notes about retrieval governance and readiness.",
                encoding="utf-8",
            )
            old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
            os.utime(stale_path, (old_time, old_time))

            superseded_path = Path(tmp) / "superseded.md"
            superseded_path.write_text(
                "# Superseded Plan\n\nThis decision is superseded by newer implementation progress and should not guide practice.",
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "doc-curate",
                        str(stale_path),
                        str(superseded_path),
                        "--stale-days",
                        "7",
                        "--apply",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Stale Rejected: 1", rendered)
            self.assertIn("Superseded Rejected: 1", rendered)
            self.assertIn("stale-rejected", rendered)
            self.assertIn("superseded-rejected", rendered)
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE), [])


class SeedPlanTests(unittest.TestCase):
    def test_cli_seed_plan_reports_real_candidate_coverage_and_graph_suggestions(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Curated doc evidence needs review",
                summary="Markdown curation found current evidence for memory-base cleanup.",
                signals=["new convention"],
                scope=MemoryScope(code=["CMU_Implementation_Progress.md"], workflow=["memory-base-cleanup"], actor=["agent"]),
                evidence=["Curated from markdown document: CMU_Implementation_Progress.md"],
                use_this_path="Review curated docs before promotion.",
                avoid_this="Do not promote stale markdown directly.",
                challenge_only_if="Use when seeding memory from project docs.",
                liability_score=4,
                confidence=0.7,
            )
            anti_pattern = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Blind markdown import",
                summary="Importing old markdown as stable memory can create context drag.",
                scope=MemoryScope(code=["CMU_Major_Unfinished_Work.md"], workflow=["memory-base-cleanup"], actor=["agent"]),
                evidence=["Stale docs can become drag."],
                use_this_path="Curate markdown against current implementation evidence.",
                avoid_this="Do not bulk-import markdown into stable memory.",
                challenge_only_if="Only use when a doc is current and reviewed.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(candidate)
            store.add(anti_pattern)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "seed-plan"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Memory Seeding Plan", rendered)
            self.assertIn("Mode: read-only workbench", rendered)
            self.assertIn("promotion", rendered)
            self.assertIn(f"cmu review {candidate.id} --to situation", rendered)
            self.assertIn("coverage: question", rendered)
            self.assertIn("cmu add --type question", rendered)
            self.assertIn("graph", rendered)
            self.assertIn(f"cmu relate {anti_pattern.id} --type related_practice --target {candidate.id}", rendered)
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)[0].type, MemoryType.CANDIDATE)

    def test_cli_seed_plan_uses_doc_curation_rejections_for_manual_draft_suggestions(self) -> None:
        with TemporaryDirectory() as tmp:
            stale_drag_doc = Path(tmp) / "stale_drag.md"
            stale_drag_doc.write_text(
                "\n".join(
                    [
                        "# Historical Drag Notes",
                        "",
                        "Anti-pattern: stale markdown can create retrieval drag if treated as authority.",
                        "Known gap: unresolved memory seeding workflow needs a question before promotion.",
                    ]
                ),
                encoding="utf-8",
            )
            old_time = (datetime.now(timezone.utc) - timedelta(days=20)).timestamp()
            os.utime(stale_drag_doc, (old_time, old_time))

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "seed-plan", "--doc", str(stale_drag_doc), "--stale-days", "1"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Document Curation Decisions Reviewed: 1", rendered)
            self.assertIn("anti-pattern-draft", rendered)
            self.assertIn("Rejected doc-curate source: stale_drag.md", rendered)
            self.assertIn("question-draft", rendered)
            self.assertIn("Doc-curate source: stale_drag.md", rendered)
            self.assertEqual(MemoryStore(tmp).list(), [])


class LifecycleTests(unittest.TestCase):
    def test_cli_lifecycle_connects_candidate_situation_stable_challenge_and_exception(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Billing migration order matters",
                summary="Billing deploy failed because service code ran before migration.",
                signals=["explained failure"],
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                evidence=["Deploy passed after migration order was corrected."],
                use_this_path="Run billing migration before service rollout.",
                avoid_this="Do not roll out service code before schema compatibility.",
                challenge_only_if="Use when billing deploy or migration order fails again.",
                liability_score=4,
                confidence=0.75,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["practice discovery", "agent behavior"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
            )
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Start command coordinates Work Cycle",
                summary="The start command should coordinate trigger, onboarding, preflight, and receipts.",
                signals=["work cycle"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Manual start run confirmed trigger to seed to Action Note handoff."],
                use_this_path="Use start for meaningful implementation tasks.",
                avoid_this="Do not create receipts when CMU stays quiet.",
                challenge_only_if="The task is low-risk and follows an obvious local pattern.",
                liability_score=4,
                confidence=0.85,
                approved_by="CMU owner",
            )
            exception = Memory.create(
                type=MemoryType.EXCEPTION,
                title="Docs-only receipts can be resolved without commit",
                summary="Strategic markdown-only work may not map to a Git checkpoint.",
                scope=MemoryScope(code=["CMU_Implementation_Progress.md"], workflow=["documentation"], actor=["agent"]),
                evidence=["Docs are intentionally local development memory."],
                liability_score=3,
                confidence=0.8,
                approved_by="CMU owner",
            )
            for memory in [candidate, situation, practice, exception]:
                store.add(memory)
            challenge = challenge_stable_memory(
                store.list(),
                ChallengeRequest(
                    memory_id=practice.id,
                    mismatch="Start command guidance may not fit silent low-risk tasks.",
                    benefit="Clarify when start should stay quiet.",
                    risk="Overusing start can create workflow drag.",
                    rollback="Keep using direct preflight for low-risk obvious work.",
                    challenged_by="agent",
                ),
            )
            assert challenge.challenge_memory is not None
            store.add(challenge.challenge_memory)
            use_store = MemoryUseStore(tmp)
            for index in range(2):
                receipt = MemoryUseReceipt.create(
                    practice,
                    PreflightQuery(
                        prompt=f"implement work cycle slice {index}",
                        actor="agent",
                        area="cmu",
                        workflow=["implementation"],
                    ),
                    Match(memory=practice, score=5.0, matched_terms=["cmu", "implementation"]),
                    source_command="start",
                )
                receipt.commit_hash = f"{index + 1:040x}"
                receipt.outcome_signal = "committed"
                receipt.link_confidence = 0.9
                use_store.add(receipt)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Core Memory Lifecycle", rendered)
            self.assertIn("Mode: read-only structural lifecycle proof", rendered)
            self.assertIn(f"{candidate.id} [candidate/active]", rendered)
            self.assertIn("Gate: ready: promote to situation", rendered)
            self.assertIn(f"{situation.id} [situation/active]", rendered)
            self.assertIn("ready: authority review for practice, anchor", rendered)
            self.assertIn(f"{practice.id} [practice/active]", rendered)
            self.assertIn("Stage: stable", rendered)
            self.assertIn("ready: 1 active challenge(s)", rendered)
            self.assertIn("challenge path active", rendered)
            self.assertIn(challenge.challenge_memory.id, rendered)
            self.assertIn("Stage: challenge-candidate", rendered)
            self.assertIn("ready: resolve challenge", rendered)
            self.assertIn(f"{exception.id} [exception/active]", rendered)
            self.assertIn("Stage: exception", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_lifecycle_memory_filter_and_blocked_candidate_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Thin candidate",
                summary="Something happened.",
                liability_score=2,
            )
            store.add(candidate)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle", "--memory", candidate.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory Filter: {candidate.id}", rendered)
            self.assertIn(f"{candidate.id} [candidate/active]", rendered)
            self.assertIn("Stage: candidate", rendered)
            self.assertIn("blocked: missing", rendered)
            self.assertIn("evidence_or_outcome", rendered)
            self.assertIn("add missing reusable scenario evidence/scope/future-use lesson", rendered)

    def test_cli_lifecycle_apply_dry_run_does_not_mutate_ready_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Billing rollback marker lesson",
                summary="Billing rollback retries should inspect release markers before retrying.",
                signals=["rollback", "marker"],
                scope=MemoryScope(code=["billing"], workflow=["rollback"], actor=["agent"]),
                evidence=["Rollback succeeded after stale marker cleanup."],
                use_this_path="Inspect the release marker before retrying rollback.",
                avoid_this="Do not retry rollback blindly.",
                challenge_only_if="The rollback path has no shared marker.",
                liability_score=4,
                confidence=0.72,
            )
            MemoryStore(tmp).add(candidate)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-apply", "--candidate-ready"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Lifecycle Apply Dry Run", rendered)
            self.assertIn("would-promote", rendered)
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.type, MemoryType.CANDIDATE)

    def test_cli_lifecycle_apply_promotes_only_candidates_that_pass_existing_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            ready = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Auth rollout lock ordering",
                summary="Auth rollout failed when lock ordering changed before token rotation.",
                signals=["auth", "lock"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Token rotation passed after restoring lock ordering."],
                use_this_path="Verify lock ordering before rotating active credentials.",
                avoid_this="Do not update active credentials before acquiring the lock.",
                challenge_only_if="The credential path no longer uses shared locks.",
                liability_score=4,
                confidence=0.74,
            )
            blocked = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Thin memory draft",
                summary="Something happened.",
                liability_score=2,
            )
            store = MemoryStore(tmp)
            store.add(ready)
            store.add(blocked)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-apply", "--candidate-ready", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Lifecycle Apply Applied", rendered)
            self.assertIn("promoted:", rendered)
            self.assertIn("blocked:", rendered)
            loaded = {memory.id: memory for memory in MemoryStore(tmp).list()}
            self.assertEqual(loaded[ready.id].type, MemoryType.SITUATION)
            self.assertGreaterEqual(loaded[ready.id].confidence, 0.7)
            self.assertEqual(loaded[blocked.id].type, MemoryType.CANDIDATE)
            self.assertIn("evidence_or_outcome", rendered)

    def test_cli_lifecycle_proposals_generates_stable_review_cards(self) -> None:
        with TemporaryDirectory() as tmp:
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing incident replay needs reconciliation guard",
                summary="Incident replay should verify reconciliation before closing billing repair work.",
                signals=["billing incident", "reconciliation"],
                scope=MemoryScope(code=["billing"], workflow=["incident"], actor=["agent"]),
                evidence=["Two incident replays passed after reconciliation verification."],
                use_this_path="Run reconciliation verification before closing billing incident replay.",
                avoid_this="Do not close replay from log inspection alone.",
                challenge_only_if="The incident path no longer changes ledger state.",
                liability_score=4,
                confidence=0.82,
            )
            MemoryStore(tmp).add(situation)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-proposals", "--target", "practice"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Lifecycle Stable Proposal Workbench", rendered)
            self.assertIn("ready:", rendered)
            self.assertIn(f"cmu review {situation.id} --to practice", rendered)
            [loaded] = MemoryStore(tmp).list()
            self.assertEqual(loaded.type, MemoryType.SITUATION)

    def test_cli_lifecycle_merge_retires_source_and_combines_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback checks release marker",
                summary="Checkout rollback should inspect release marker state before retry.",
                signals=["rollback"],
                scope=MemoryScope(code=["checkout"], workflow=["rollback"]),
                evidence=["Rollback succeeded after marker inspection."],
                use_this_path="Inspect release marker before retrying rollback.",
                challenge_only_if="No release marker participates in rollback.",
                confidence=0.75,
            )
            source = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback checks deploy flag",
                summary="Checkout rollback should inspect deploy flag state before retry.",
                signals=["deploy flag"],
                scope=MemoryScope(code=["checkout"], workflow=["rollback"]),
                evidence=["Retry stopped after stale deploy flag was found."],
                use_this_path="Inspect deploy flag before retrying rollback.",
                challenge_only_if="No deploy flag participates in rollback.",
                confidence=0.8,
            )
            store = MemoryStore(tmp)
            store.add(target)
            store.add(source)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "lifecycle-merge",
                        "--target",
                        target.id,
                        "--source",
                        source.id,
                        "--reason",
                        "same rollback lesson with duplicate operational signals",
                        "--approved-by",
                        "Release owner",
                        "--apply",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Lifecycle Merge", rendered)
            self.assertIn("merged:", rendered)
            active = {memory.id: memory for memory in MemoryStore(tmp).list()}
            retired = {memory.id: memory for memory in MemoryStore(tmp).list(status=MemoryStatus.RETIRED)}
            self.assertIn(target.id, active)
            self.assertIn(source.id, retired)
            self.assertIn("Retry stopped after stale deploy flag was found.", active[target.id].evidence)
            self.assertTrue(any(rel.target_id == source.id for rel in active[target.id].relationships))

    def test_cli_lifecycle_demote_requires_stable_authority_and_applies(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use billing replay checklist",
                summary="Billing incident replay should use the checklist before closing.",
                signals=["billing incident"],
                scope=MemoryScope(code=["billing"], workflow=["incident"]),
                evidence=["Checklist prevented a missed reconciliation."],
                use_this_path="Use the billing replay checklist.",
                challenge_only_if="The replay path no longer touches ledger state.",
                liability_score=4,
                confidence=0.86,
                approved_by="Billing owner",
                authority_owner="Billing",
                authority_role="owner",
                authority_consequence="high",
            )
            MemoryStore(tmp).add(practice)

            blocked = StringIO()
            with redirect_stdout(blocked):
                blocked_exit = main(
                    ["--root", tmp, "lifecycle-demote", practice.id, "--reason", "scope evidence is weaker than expected", "--apply"]
                )
            self.assertEqual(blocked_exit, 1)
            self.assertIn("sufficient_high_authority", blocked.getvalue())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "lifecycle-demote",
                        practice.id,
                        "--reason",
                        "scope evidence is weaker than expected",
                        "--approved-by",
                        "Billing owner",
                        "--approver-role",
                        "owner",
                        "--apply",
                    ]
                )

            self.assertEqual(exit_code, 0)
            loaded = MemoryStore(tmp).list()[0]
            self.assertEqual(loaded.type, MemoryType.SITUATION)
            self.assertEqual(loaded.approved_by, "")
            self.assertIn("Lifecycle demotion: practice -> situation", loaded.evidence)

    def test_cli_lifecycle_archive_writes_retired_memory_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            retired = Memory.create(
                type=MemoryType.SITUATION,
                title="Retired checkout staging lesson",
                summary="Old staging-only checkout lesson no longer applies.",
                scope=MemoryScope(code=["checkout"], workflow=["staging"]),
                evidence=["Retired after staging pipeline removal."],
            )
            retired.status = MemoryStatus.RETIRED
            MemoryStore(tmp).add(retired)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-archive", "--memory", retired.id, "--apply"])

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("CMU Lifecycle Archive", rendered)
            self.assertIn("archived:", rendered)
            archive = json.loads((Path(tmp) / ".cmu" / "memory_archive.json").read_text(encoding="utf-8"))
            self.assertEqual(archive["archived_memories"][0]["id"], retired.id)

    def test_cli_lifecycle_scope_record_creates_candidate_for_broad_scope_change(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Checkout rollback marker practice",
                summary="Checkout rollback should inspect markers before retry.",
                signals=["rollback"],
                scope=MemoryScope(code=["checkout/service.py"], workflow=["rollback"], actor=["agent"]),
                evidence=["Marker inspection prevented duplicate rollback."],
                use_this_path="Inspect checkout marker before retry.",
                challenge_only_if="No checkout marker participates in rollback.",
                liability_score=4,
                confidence=0.84,
                approved_by="Release owner",
            )
            MemoryStore(tmp).add(practice)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "lifecycle-scope-record",
                        practice.id,
                        "--reason",
                        "similar rollback failures now appear across checkout and billing",
                        "--requested-by",
                        "Release owner",
                        "--scope-code",
                        "checkout",
                        "--scope-code",
                        "billing",
                        "--scope-workflow",
                        "rollback",
                        "--apply",
                    ]
                )

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("CMU Lifecycle Scope Change Record", rendered)
            self.assertIn("recorded:", rendered)
            candidates = [memory for memory in MemoryStore(tmp).list() if memory.type == MemoryType.CANDIDATE]
            self.assertEqual(len(candidates), 1)
            self.assertIn("scope change proposal", candidates[0].signals)
            self.assertIn(f"Scope change target: {practice.id}", candidates[0].evidence)
            loaded_practice = [memory for memory in MemoryStore(tmp).list() if memory.id == practice.id][0]
            self.assertEqual(loaded_practice.scope.code, ["checkout/service.py"])


class MemoryGravityTests(unittest.TestCase):
    def test_cli_gravity_reports_promotion_governance_graph_and_use_pressures(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Billing schema compatibility rollout",
                summary="Billing service startup required schema compatibility validation.",
                signals=["explained failure"],
                scope=MemoryScope(code=["billing/deploy.py"], workflow=["deployment"], actor=["agent"]),
                evidence=["Validation allowed safe billing startup."],
                use_this_path="Verify billing schema compatibility during startup planning.",
                avoid_this="Do not start billing services with unknown schema compatibility.",
                challenge_only_if="Use when billing schema compatibility rules change.",
                liability_score=4,
                confidence=0.8,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth credential lock sequencing",
                summary="Credential rotation failed when token lock ordering changed.",
                signals=["explained failure"],
                scope=MemoryScope(code=["auth/tokens.py"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Rotation passed after lock sequencing was restored."],
                use_this_path="Verify token lock sequencing before credential rotation.",
                avoid_this="Do not rotate credentials before lock ownership is confirmed.",
                challenge_only_if="Use when auth credential rotation or token lock order changes.",
                liability_score=5,
                confidence=0.85,
            )
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Deployment retries need marker checks",
                summary="Deployment retry flows must verify release markers before retrying.",
                signals=["deployment"],
                scope=MemoryScope(code=["checkout", "billing", "deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Multiple rollback fixes required marker checks."],
                use_this_path="Verify release markers before retrying deployment.",
                avoid_this="Do not retry deployment blindly.",
                challenge_only_if="The deployment path has no release marker concept.",
                liability_score=5,
                confidence=0.8,
            )
            candidate.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.RELATED_PRACTICE,
                    target_id=practice.id,
                    reason="Candidate may teach the deployment retry practice.",
                )
            )
            situation.relationships.append(
                MemoryRelationship(
                    type=MemoryRelationType.SUPPORTS,
                    target_id=practice.id,
                    reason="Auth sequencing evidence supports deployment-style ordering checks.",
                )
            )
            for memory in [candidate, situation, practice]:
                store.add(memory)
            use_store = MemoryUseStore(tmp)
            strong = MemoryUseReceipt.create(
                practice,
                PreflightQuery(
                    prompt="Fix checkout deployment retry marker failure",
                    actor="agent",
                    area="checkout",
                    files=["checkout/deploy.py"],
                    workflow=["deployment"],
                ),
                Match(memory=practice, score=5.0, matched_terms=["checkout", "deployment"]),
                source_command="start",
            )
            strong.commit_hash = "1" * 40
            strong.outcome_signal = "committed"
            strong.link_confidence = 0.9
            use_store.add(strong)
            mixed = MemoryUseReceipt.create(
                practice,
                PreflightQuery(
                    prompt="Touch broad deployment cleanup",
                    actor="agent",
                    area="deployment",
                    files=["scripts/deploy.py"],
                    workflow=["deployment"],
                ),
                Match(memory=practice, score=4.0, matched_terms=["deployment"]),
                source_command="preflight",
            )
            mixed.commit_hash = "2" * 40
            mixed.outcome_signal = "committed"
            mixed.link_confidence = 0.3
            mixed.flags = ["mixed_commit", "low_confidence"]
            use_store.add(mixed)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "gravity"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Memory Gravity", rendered)
            self.assertIn("Mode: read-only placement/settling proof", rendered)
            self.assertIn(candidate.id, rendered)
            self.assertIn("promotion pressure", rendered)
            self.assertIn("promote candidate to situation", rendered)
            self.assertIn(situation.id, rendered)
            self.assertIn("stable promotion pressure", rendered)
            self.assertIn(practice.id, rendered)
            self.assertIn("graph", rendered)
            self.assertIn("governance review pressure", rendered)
            self.assertIn("split pressure", rendered)
            self.assertIn("use evidence", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_gravity_filter_surfaces_unsettled_merge_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            first = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Auth token rotation lock order",
                summary="Auth token rotation failed because lock order changed during credential update.",
                evidence=["Incident note showed changed lock order."],
                liability_score=3,
                confidence=0.6,
            )
            second = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth credential rotation lock order",
                summary="Credential rotation failed because auth token lock order changed during update.",
                evidence=["Debugging found the auth lock order issue."],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"]),
                liability_score=3,
                confidence=0.7,
            )
            store = MemoryStore(tmp)
            store.add(first)
            store.add(second)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "gravity", "--memory", first.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory Filter: {first.id}", rendered)
            self.assertIn("unsettled: no scope center", rendered)
            self.assertIn("scope gap", rendered)
            self.assertIn("merge pressure", rendered)
            self.assertIn("review duplicate/related memories", rendered)


class PracticeAnchorGovernanceTests(unittest.TestCase):
    def test_cli_governance_connects_authority_use_review_and_active_challenge(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            approved = Memory.create(
                type=MemoryType.PRACTICE,
                title="Run CMU start for structural work",
                summary="Meaningful CMU implementation should enter through the Work Cycle.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Start command coordinates trigger, onboarding, preflight, and receipts."],
                use_this_path="Use start for large implementation tasks.",
                avoid_this="Do not create receipts when CMU stays quiet.",
                challenge_only_if="The task is low-risk and follows an obvious local pattern.",
                liability_score=4,
                confidence=0.85,
                approved_by="CMU owner",
            )
            missing_authority = Memory.create(
                type=MemoryType.ANCHOR,
                title="Do not trust broad unapproved stable memory",
                summary="Stable memory needs explicit owner/team authority before broader trust.",
                scope=MemoryScope(code=["cmu"], workflow=["governance"], actor=["agent"]),
                evidence=["Planning docs require authority for Practice and Anchor memory."],
                use_this_path="Inspect approval before expanding trust.",
                avoid_this="Do not treat unapproved stable memory as settled precedent.",
                challenge_only_if="Authority has already been recorded elsewhere.",
                liability_score=5,
                confidence=0.8,
            )
            challenged = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use preflight before agent implementation",
                summary="Agents should check CMU memory before meaningful implementation work.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Manual preflight validation surfaced the expected Action Note."],
                use_this_path="Run preflight before implementation.",
                avoid_this="Do not dump all memory into context.",
                challenge_only_if="The task is tiny, local, and obvious.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU owner",
            )
            for memory in [approved, missing_authority, challenged]:
                store.add(memory)
            add_strong_receipts(tmp, approved, count=2)
            challenge = challenge_stable_memory(
                store.list(),
                ChallengeRequest(
                    memory_id=challenged.id,
                    mismatch="Full start may be better than raw preflight for large implementation.",
                    benefit="Governance can distinguish task-start surfaces.",
                    risk="Agents may overuse the wrong entrypoint.",
                    rollback="Keep direct preflight available for focused checks.",
                    challenged_by="agent",
                ),
            )
            assert challenge.challenge_memory is not None
            store.add(challenge.challenge_memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "governance"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Practice/Anchor Governance", rendered)
            self.assertIn("Mode: read-only stable-memory governance proof", rendered)
            self.assertIn("Stable Memories: 3", rendered)
            self.assertIn("Approved: 2", rendered)
            self.assertIn("Missing Authority: 1", rendered)
            self.assertIn("Active Challenges: 1", rendered)
            self.assertIn(f"{approved.id} [practice/active]", rendered)
            self.assertIn("Authority: approved by CMU owner", rendered)
            self.assertIn("State: ready: strengthen evidence", rendered)
            self.assertIn("2 linked uses; 2 committed (2 strong)", rendered)
            self.assertIn(f"{missing_authority.id} [anchor/active]", rendered)
            self.assertIn("Authority: missing explicit approval", rendered)
            self.assertIn("State: blocked: missing authority", rendered)
            self.assertIn(f"{challenged.id} [practice/active]", rendered)
            self.assertIn("State: blocked: active challenge", rendered)
            self.assertIn(challenge.challenge_memory.id, rendered)
            self.assertIn("Allowed Paths: resolve exception, resolve strengthen, resolve update, resolve retire, resolve split", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_governance_memory_filter_reports_drag_review_path(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Deployment retries need marker checks",
                summary="Deployment retry flows must verify release markers before retrying.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Rollback passed after marker verification was restored."],
                use_this_path="Verify release markers before retrying deployment.",
                avoid_this="Do not retry deployment blindly.",
                challenge_only_if="The deployment path has no release marker concept.",
                liability_score=5,
                confidence=0.85,
                approved_by="Release owner",
            )
            MemoryStore(tmp).add(memory)
            add_drag_receipts(tmp, memory, count=2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "governance", "--memory", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory Filter: {memory.id}", rendered)
            self.assertIn(f"{memory.id} [practice/active]", rendered)
            self.assertIn("Authority: approved by Release owner", rendered)
            self.assertIn("Scope: code=deploy; workflow=deployment; actor=agent", rendered)
            self.assertIn("State: ready: governance review", rendered)
            self.assertIn("2 linked uses; 0 committed (0 strong)", rendered)
            self.assertIn("2 drag signals", rendered)
            self.assertIn("Challenge State: none", rendered)
            self.assertIn("Allowed Paths: follow within scope, strengthen, challenge, scope-review, split, retire", rendered)
            self.assertIn("challenge, narrow, split, retire, or strengthen", rendered)

    def test_review_queue_surfaces_promotion_and_authority_cards_from_real_store(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Checkout rollback marker lesson",
                summary="Checkout rollback retries should inspect release markers before retrying.",
                scope=MemoryScope(code=["checkout"], workflow=["rollback"], actor=["agent"]),
                evidence=["Rollback succeeded after stale marker cleanup."],
                use_this_path="Check the release marker before retrying rollback.",
                avoid_this="Do not blindly retry rollback.",
                challenge_only_if="The rollback path has no shared marker.",
                liability_score=4,
                confidence=0.72,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy migration order",
                summary="Billing deployment requires migration-order checks before rollout.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                evidence=["A deploy passed after migration order was corrected."],
                use_this_path="Check migration order before rollout.",
                avoid_this="Do not deploy service code before schema compatibility is known.",
                challenge_only_if="The change has no schema dependency.",
                liability_score=4,
                confidence=0.82,
            )
            unapproved = Memory.create(
                type=MemoryType.PRACTICE,
                title="Legacy stable memory needs authority",
                summary="Stable memory imported from history needs explicit authority metadata.",
                scope=MemoryScope(code=["cmu"], workflow=["governance"], actor=["agent"]),
                evidence=["Legacy stable memory predates authority metadata."],
                use_this_path="Assign accountable authority before treating it as settled.",
                avoid_this="Do not broaden trust from legacy approval alone.",
                challenge_only_if="Authority is recorded elsewhere.",
                liability_score=4,
                confidence=0.8,
            )
            for memory in [candidate, situation, unapproved]:
                store.add(memory)

            report = review_queue(store.list(), MemoryUseStore(tmp).list())
            rendered = report.render()

            self.assertIn("CMU Review Queue", rendered)
            self.assertIn("candidate-promotion", rendered)
            self.assertIn(f"cmu promote {candidate.id}", rendered)
            self.assertIn("practice-approval", rendered)
            self.assertIn(f"cmu promote {situation.id} --to practice --approved-by <owner-or-team>", rendered)
            self.assertIn("anchor-approval", rendered)
            self.assertIn(f"cmu promote {situation.id} --to anchor --approved-by <owner-or-team>", rendered)
            self.assertIn("authority-approval", rendered)
            self.assertIn(f"cmu authority-set {unapproved.id}", rendered)
            self.assertTrue(any(card.priority == "P0" for card in report.cards))

    def test_review_queue_surfaces_uncovered_team_scope_from_real_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            record = TeamScopeRecord.create(
                repo="checkout-service",
                team="Release",
                owner="Release owner",
                code=["checkout"],
                workflow=["rollback"],
                environment=["prod"],
                authority_role="owner",
                consequence="high",
            )
            TeamDirectoryStore(tmp).add(record)

            report = review_queue(
                MemoryStore(tmp).list(),
                MemoryUseStore(tmp).list(),
                TeamDirectoryStore(tmp).list(),
            )
            rendered = report.render()

            self.assertIn("team-scope-coverage", rendered)
            self.assertIn(record.id, rendered)
            self.assertIn("checkout-service/Release", rendered)
            self.assertIn("Team scope has no active matching memory", rendered)
            self.assertIn("missing_metadata=none", rendered)
            self.assertTrue(any(card.category == "team-scope-coverage" and card.priority == "P1" for card in report.cards))

    def test_cli_review_queue_surfaces_challenge_strengthen_and_decay_review_cards(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            strengthened = Memory.create(
                type=MemoryType.PRACTICE,
                title="Run start before structural CMU work",
                summary="Structural CMU work should start through the full Work Cycle.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The Work Cycle coordinates trigger, onboarding, preflight, and receipts."],
                use_this_path="Run cmu start before structural implementation.",
                avoid_this="Do not create context dumps.",
                challenge_only_if="The task is tiny and obvious.",
                liability_score=4,
                confidence=0.86,
                approved_by="CMU owner",
            )
            challenged = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use preflight before large changes",
                summary="Large changes should check memory before implementation.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Preflight caught a prior implementation risk."],
                use_this_path="Run preflight before large changes.",
                avoid_this="Do not skip memory when risk is high.",
                challenge_only_if="The change is low-risk.",
                liability_score=4,
                confidence=0.85,
                approved_by="CMU owner",
            )
            decaying = Memory.create(
                type=MemoryType.SITUATION,
                title="Old deploy workaround no longer fits",
                summary="A stale workaround should be reviewed before reuse.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Two later uses showed the workaround was noisy."],
                liability_score=3,
                confidence=0.4,
            )
            for memory in [strengthened, challenged, decaying]:
                store.add(memory)
            add_strong_receipts(tmp, strengthened, count=2)
            add_drag_receipts(tmp, decaying, count=3)
            challenge = challenge_stable_memory(
                store.list(),
                ChallengeRequest(
                    memory_id=challenged.id,
                    mismatch="Full start may be safer than raw preflight for large changes.",
                    benefit="Review can preserve the right entrypoint.",
                    risk="Future agents may follow the wrong surface.",
                    rollback="Keep preflight available until resolved.",
                    challenged_by="agent",
                ),
            )
            assert challenge.challenge_memory is not None
            store.add(challenge.challenge_memory)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "review-queue"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Review Queue", rendered)
            self.assertIn("Mode: compact human approval/review queue", rendered)
            self.assertIn("strengthen-approval", rendered)
            self.assertIn(f"cmu use-review {strengthened.id} --prepare strengthen --apply --approved-by <owner-or-team>", rendered)
            self.assertIn("challenge-resolution", rendered)
            self.assertIn(f"cmu resolve-challenge {challenge.challenge_memory.id}", rendered)
            self.assertIn("decay-review", rendered)
            self.assertIn(f"cmu decay-apply {decaying.id}", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_review_queue_reads_team_scopes_without_creating_empty_directory_file(self) -> None:
        with TemporaryDirectory() as tmp:
            team_file = Path(tmp) / ".cmu" / "team_scopes.json"

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "review-queue"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Review Queue", output.getvalue())
            self.assertFalse(team_file.exists())

    def test_review_reminders_surface_expired_due_soon_unscheduled_and_open_cards(self) -> None:
        now = datetime(2026, 6, 9, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            expired = Memory.create(
                type=MemoryType.PRACTICE,
                title="Expired deploy authority",
                summary="Deployment Practice needs renewed authority.",
                approved_by="Release owner",
                authority_owner="Release team",
                authority_role="owner",
                authority_consequence="high",
                authority_review_due_at="2026-06-01T00:00:00+00:00",
            )
            due_soon = Memory.create(
                type=MemoryType.ANCHOR,
                title="Due soon security authority",
                summary="Security Anchor needs near-term review.",
                approved_by="Security council",
                authority_owner="Security team",
                authority_role="org",
                authority_consequence="critical",
                authority_review_due_at="2026-06-15T00:00:00+00:00",
            )
            unscheduled = Memory.create(
                type=MemoryType.PRACTICE,
                title="Approved but unscheduled",
                summary="Approved Practice should get a lightweight review date.",
                approved_by="CMU owner",
                authority_owner="CMU team",
                authority_role="owner",
                authority_consequence="high",
            )
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Candidate ready for reminder queue",
                summary="Candidate has enough detail to become Situation.",
                scope=MemoryScope(code=["cmu"], workflow=["governance"]),
                evidence=["Reminder tests exercise real review queue."],
                use_this_path="Promote when the gate passes.",
                avoid_this="Do not ignore ready Candidates.",
                challenge_only_if="The lesson is no longer reusable.",
                liability_score=3,
                confidence=0.75,
            )
            for memory in [expired, due_soon, unscheduled, candidate]:
                store.add(memory)

            report = review_reminders(store.list(), MemoryUseStore(tmp).list(), days=7, now=now)
            rendered = report.render()
            payload = report.to_delivery_payload()

            self.assertIn("CMU Review Reminders", rendered)
            self.assertIn("- Delivery Ready: yes", rendered)
            self.assertIn("authority-review-expired", rendered)
            self.assertIn(expired.id, rendered)
            self.assertIn("authority-review-due-soon", rendered)
            self.assertIn(due_soon.id, rendered)
            self.assertIn("authority-review-not-scheduled", rendered)
            self.assertIn(unscheduled.id, rendered)
            self.assertIn("open-candidate-promotion", rendered)
            self.assertIn(f"cmu promote {candidate.id}", rendered)
            self.assertTrue(any(reminder.priority == "P0" for reminder in report.reminders))
            self.assertTrue(payload["delivery_ready"])
            self.assertEqual(payload["schema"], "cmu-review-reminders/v1")
            self.assertEqual(payload["mode"], "read-only-reminder-delivery")
            self.assertEqual(payload["summary"]["total"], len(payload["reminders"]))
            self.assertGreaterEqual(payload["summary"]["total"], 4)
            self.assertGreaterEqual(payload["summary"]["urgent"], 3)
            self.assertIn(f"cmu promote {candidate.id}", payload["commands"])
            self.assertIn("authority-review-expired", {item["category"] for item in payload["reminders"]})
            self.assertIn("open-decay-review", {item["category"] for item in payload["reminders"]})
            self.assertTrue(all(item["command"] for item in payload["reminders"]))

    def test_cli_review_reminders_json_is_read_only_and_uses_real_store(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Expired checkout authority",
                summary="Checkout Practice needs authority renewal.",
                approved_by="Checkout owner",
                authority_owner="Checkout team",
                authority_role="owner",
                authority_consequence="high",
                authority_review_due_at="2020-01-01T00:00:00+00:00",
            )
            store.add(memory)
            before = (Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "review-reminders", "--days", "30", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], "cmu-review-reminders/v1")
            self.assertTrue(payload["delivery_ready"])
            self.assertEqual(payload["summary"]["total"], len(payload["reminders"]))
            self.assertGreaterEqual(payload["summary"]["total"], 1)
            self.assertGreaterEqual(payload["summary"]["p0"], 1)
            self.assertIn("authority-review-expired", {item["category"] for item in payload["reminders"]})
            self.assertIn(memory.id, {item["subject_id"] for item in payload["reminders"]})
            self.assertTrue(any("cmu authority-set" in command for command in payload["commands"]))
            after = (Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8")
            self.assertEqual(after, before)


class UsefulnessDragAnalyticsTests(unittest.TestCase):
    def test_cli_analytics_classifies_useful_drag_mixed_and_evidence_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            useful = Memory.create(
                type=MemoryType.SITUATION,
                title="Billing deploy checks migration order",
                summary="Billing deploy work should check migration order.",
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                evidence=["Deploy passed after migration order was corrected."],
                liability_score=4,
                confidence=0.8,
            )
            mixed = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should surface memory only when it changes action.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Manual preflight validation surfaced useful guidance."],
                liability_score=4,
                confidence=0.9,
                approved_by="CMU owner",
            )
            drag = Memory.create(
                type=MemoryType.PRACTICE,
                title="Deployment retries need marker checks",
                summary="Deployment retry flows must verify release markers before retrying.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Rollback passed after marker verification was restored."],
                liability_score=5,
                confidence=0.85,
                approved_by="Release owner",
            )
            gap = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback trace",
                summary="Checkout rollback work may produce reusable deployment lessons.",
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
            )
            for memory in [useful, mixed, drag, gap]:
                store.add(memory)
            add_strong_receipts(tmp, useful, count=2)
            add_strong_receipts(tmp, mixed, count=2)
            add_drag_receipts(tmp, mixed, count=1)
            add_drag_receipts(tmp, drag, count=2)
            unlinked = MemoryUseReceipt.create(
                gap,
                PreflightQuery(prompt="Investigate checkout rollback", actor="agent", area="checkout", workflow=["deployment"]),
                Match(memory=gap, score=4.0, matched_terms=["checkout", "deployment"]),
                source_command="start",
            )
            MemoryUseStore(tmp).add(unlinked)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "analytics"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Usefulness and Drag Analytics", rendered)
            self.assertIn("Mode: read-only usefulness/drag proof", rendered)
            self.assertIn("Memories With Evidence: 4", rendered)
            self.assertIn("Useful: 1", rendered)
            self.assertIn("Mixed: 1", rendered)
            self.assertIn("Drag: 1", rendered)
            self.assertIn("Evidence Gaps: 1", rendered)
            self.assertIn(f"{useful.id} [situation] Billing deploy checks migration order", rendered)
            self.assertIn("Verdict: useful", rendered)
            self.assertIn("Evidence Readiness: closed enough for first-pass judgment", rendered)
            self.assertIn(f"{mixed.id} [practice] Task-start preflight stays quiet unless useful", rendered)
            self.assertIn("Verdict: mixed", rendered)
            self.assertIn("Governance: ready: governance review", rendered)
            self.assertIn(f"{drag.id} [practice] Deployment retries need marker checks", rendered)
            self.assertIn("Verdict: drag", rendered)
            self.assertIn("review scope and wording", rendered)
            self.assertIn(f"{gap.id} [situation] Checkout rollback trace", rendered)
            self.assertIn("Verdict: evidence-gap", rendered)
            self.assertIn("link or resolve receipts before claiming usefulness or drag", rendered)
            self.assertIn("Proof Meaning:", rendered)

    def test_cli_analytics_filter_keeps_stable_authority_block_visible(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Run CMU start for structural work",
                summary="Meaningful CMU implementation should enter through the Work Cycle.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Start command coordinates trigger, onboarding, preflight, and receipts."],
                liability_score=4,
                confidence=0.85,
            )
            MemoryStore(tmp).add(memory)
            add_strong_receipts(tmp, memory, count=2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "analytics", "--memory", memory.id])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"Memory Filter: {memory.id}", rendered)
            self.assertIn(f"{memory.id} [practice] Run CMU start for structural work", rendered)
            self.assertIn("Verdict: useful", rendered)
            self.assertIn("Governance: blocked: missing authority", rendered)
            self.assertIn("resolve governance first; analytics verdict is useful", rendered)
            self.assertIn("Retrieval Adjustment: +0.50", rendered)


class MemoryBaseReadinessTests(unittest.TestCase):
    def test_readiness_report_prioritizes_real_cleanup_issues_from_stores(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            stable_without_authority = Memory.create(
                type=MemoryType.PRACTICE,
                title="Run CMU start for structural work",
                summary="Meaningful CMU implementation should enter through the Work Cycle.",
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Start command coordinates trigger, onboarding, preflight, and receipts."],
                use_this_path="Use start before large implementation tasks.",
                avoid_this="Do not dump all memory into context.",
                challenge_only_if="The task is tiny and obvious.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.SUPPORTS,
                        target_id="mem_missing_target",
                        reason="Imported relationship target was not present.",
                    )
                ],
                liability_score=4,
                confidence=0.85,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout rollback trace",
                summary="Checkout rollback work may produce reusable deployment lessons.",
                scope=MemoryScope(code=["checkout"], workflow=["deployment"], actor=["agent"]),
                evidence=["Rollback notes showed a repeated release-marker mistake."],
                liability_score=3,
                confidence=0.75,
            )
            for memory in [stable_without_authority, situation]:
                store.add(memory)
            receipt = MemoryUseReceipt.create(
                situation,
                PreflightQuery(prompt="Investigate checkout rollback", actor="agent", area="checkout", workflow=["deployment"]),
                Match(memory=situation, score=4.0, matched_terms=["checkout", "deployment"]),
                source_command="start",
            )
            MemoryUseStore(tmp).add(receipt)

            report = readiness_report(store.list(), MemoryUseStore(tmp).list())

            self.assertEqual(report.memories_reviewed, 2)
            self.assertEqual(report.receipts_reviewed, 1)
            self.assertEqual(report.stable_memories, 1)
            self.assertEqual(report.anti_patterns, 0)
            self.assertEqual(report.questions, 0)
            categories = [(issue.severity, issue.category, issue.state, issue.subject_id) for issue in report.issues]
            self.assertIn((0, "authority", "blocked: missing authority", stable_without_authority.id), categories)
            self.assertIn((1, "receipt", "unresolved receipt", receipt.id), categories)
            self.assertIn((1, "graph", "dangling relationship", stable_without_authority.id), categories)
            self.assertIn((2, "coverage", "missing active Anti-Pattern memory", "anti-pattern"), categories)
            self.assertIn((2, "coverage", "missing active Question memory", "question"), categories)
            self.assertEqual(report.issues[0].severity, 0)
            self.assertEqual(report.issues[0].category, "authority")

            rendered = report.render()
            self.assertIn("CMU Memory Base Readiness", rendered)
            self.assertIn("Cleanup Issues:", rendered)
            self.assertIn("Readiness Verdict: blocked:", rendered)
            self.assertIn(f"run `cmu use-link {receipt.id} --commit <hash>`", rendered)
            self.assertIn("create real Anti-Pattern memories", rendered)
            self.assertIn("create real Question memories", rendered)

            stored_receipt = MemoryUseStore(tmp).get(receipt.id)
            self.assertEqual(stored_receipt.commit_hash, "")
            self.assertEqual(stored_receipt.outcome_signal, "")

    def test_cli_readiness_uses_persisted_memories_receipts_and_includes_retired_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Deployment retries need marker checks",
                summary="Deployment retry flows must verify release markers before retrying.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Rollback passed after marker verification was restored."],
                use_this_path="Verify release markers before retrying deployment.",
                avoid_this="Do not retry deployment blindly.",
                challenge_only_if="The deployment path has no release marker concept.",
                liability_score=5,
                confidence=0.85,
                approved_by="Release owner",
            )
            anti_pattern = Memory.create(
                type=MemoryType.ANTI_PATTERN,
                title="Blind deployment retry",
                summary="Retrying deployment without marker inspection can repeat a bad rollout.",
                scope=MemoryScope(code=["deploy"], workflow=["deployment"], actor=["agent"]),
                evidence=["Prior rollback showed marker mismatch."],
                use_this_path="Inspect markers before retrying.",
                avoid_this="Do not retry blindly.",
            )
            question = Memory.create(
                type=MemoryType.QUESTION,
                title="Which deploy marker is authoritative",
                summary="The release process still needs an owner decision on marker authority.",
                scope=MemoryScope(ownership=["Release owner"], code=["deploy"], workflow=["deployment"]),
                evidence=["Two systems can write release markers."],
                use_this_path="Ask Release owner before changing marker checks.",
                avoid_this="Do not assume both marker systems agree.",
                challenge_only_if="A single authoritative marker is documented.",
            )
            retired = Memory.create(
                type=MemoryType.SITUATION,
                title="Retired deploy note",
                summary="Old deploy note should only appear when retired history is requested.",
                confidence=0.3,
            )
            retired.status = MemoryStatus.RETIRED
            for memory in [practice, anti_pattern, question, retired]:
                store.add(memory)
            add_drag_receipts(tmp, practice, count=3)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "readiness", "--include-retired"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Memory Base Readiness", rendered)
            self.assertIn("History: active + retired", rendered)
            self.assertIn("Memories Reviewed: 4", rendered)
            self.assertIn("Use Receipts Reviewed: 3", rendered)
            self.assertIn("Anti-Patterns: 1", rendered)
            self.assertIn("Questions: 1", rendered)
            self.assertIn(f"P1 quality: {practice.id} Deployment retries need marker checks", rendered)
            self.assertIn("State: decay-ready", rendered)
            self.assertIn("review evidence, then explicitly weaken, demote, or retire", rendered)
            self.assertIn(retired.id, rendered)
            self.assertNotIn("missing active Anti-Pattern memory", rendered)
            self.assertNotIn("missing active Question memory", rendered)


class PromotionTests(unittest.TestCase):
    def test_candidate_to_situation_review_passes_when_gate_fields_exist(self) -> None:
        candidate = Memory.create(
            type=MemoryType.CANDIDATE,
            title="Store init should create missing root",
            summary="The init command failed when the configured root directory did not exist.",
            signals=["explained failure"],
            scope=MemoryScope(code=["cmu/store.py"], workflow=["local setup"], actor=["agent"]),
            evidence=["Smoke test failed before root creation and passed after the fix."],
            use_this_path="Create the configured root before creating .cmu.",
            avoid_this="Do not assume custom store roots already exist.",
            challenge_only_if="Use when adding commands that accept custom local store roots.",
            liability_score=3,
            confidence=0.75,
        )

        review = review_promotion([candidate], candidate.id, MemoryType.SITUATION)

        self.assertTrue(review.gate_passed)
        self.assertIn("Gate: PASS", review.render())

    def test_candidate_to_situation_review_blocks_missing_required_fields(self) -> None:
        candidate = Memory.create(
            type=MemoryType.CANDIDATE,
            title="Thin candidate",
            summary="Something happened.",
            liability_score=2,
        )

        review = review_promotion([candidate], candidate.id, MemoryType.SITUATION)

        self.assertFalse(review.gate_passed)
        self.assertIn("evidence_or_outcome", review.missing)
        self.assertIn("scope", review.missing)

    def test_promote_changes_candidate_to_situation(self) -> None:
        candidate = Memory.create(
            type=MemoryType.CANDIDATE,
            title="Migration order matters",
            summary="Billing deploy failed because service code ran before migration.",
            signals=["explained failure"],
            scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
            evidence=["Deploy passed after migration order was corrected."],
            use_this_path="Run billing migration before service rollout.",
            avoid_this="Do not roll out service code before schema compatibility.",
            challenge_only_if="Use when billing deploy or migration order fails again.",
            liability_score=4,
            confidence=0.65,
        )

        decision = promote_memory([candidate], candidate.id, MemoryType.SITUATION)

        self.assertTrue(decision.promoted)
        self.assertIsNotNone(decision.memory)
        assert decision.memory is not None
        self.assertEqual(decision.memory.type, MemoryType.SITUATION)
        self.assertGreaterEqual(decision.memory.confidence, 0.7)

    def test_situation_to_practice_review_shows_authority_proposal(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery", "agent behavior"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
        )

        review = review_promotion([situation], situation.id, MemoryType.PRACTICE)

        rendered = review.render()
        self.assertTrue(review.gate_passed)
        self.assertIn("CMU Practice Proposal Review", rendered)
        self.assertIn("READY FOR AUTHORITY REVIEW", rendered)
        self.assertIn("Authority Needed: Explicit owner/team approval before promotion.", rendered)
        self.assertIn("Choices: Approve, Narrow/Edit, or Keep as Situation.", rendered)
        self.assertIn("Status: Proposal only. No promotion has been applied.", rendered)

    def test_situation_to_anchor_review_blocks_low_liability(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Local wording cleanup",
            summary="A local wording cleanup avoided a small bit of confusion.",
            scope=MemoryScope(code=["README.md"]),
            evidence=["The wording was clarified."],
            liability_score=2,
            confidence=0.8,
        )

        review = review_promotion([situation], situation.id, MemoryType.ANCHOR)

        self.assertFalse(review.gate_passed)
        self.assertIn("high_memory_liability", review.missing)
        self.assertIn("NEEDS NARROWING", review.render())

    def test_situation_to_practice_promotion_requires_approval(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery", "agent behavior"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
        )

        decision = promote_memory([situation], situation.id, MemoryType.PRACTICE)

        self.assertFalse(decision.promoted)
        self.assertEqual(situation.type, MemoryType.SITUATION)
        self.assertIn("explicit owner/team approval required", decision.render())

    def test_situation_to_practice_promotion_records_approval(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery", "agent behavior"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start, then surface only compact Action Notes that change the next action.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.72,
        )

        decision = promote_memory([situation], situation.id, MemoryType.PRACTICE, approved_by="CMU core owner")

        self.assertTrue(decision.promoted)
        self.assertIsNotNone(decision.memory)
        assert decision.memory is not None
        self.assertEqual(decision.memory.type, MemoryType.PRACTICE)
        self.assertEqual(decision.memory.approved_by, "CMU core owner")
        self.assertIn("Authority approval: CMU core owner", decision.memory.evidence)
        self.assertGreaterEqual(decision.memory.confidence, 0.75)


class CliPromotionTests(unittest.TestCase):
    def test_cli_promote_updates_stored_candidate_to_situation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Dependency version quirk",
                summary="A dependency version quirk caused repeated test errors.",
                signals=["repeated error", "tooling quirk"],
                scope=MemoryScope(code=["tools"], workflow=["testing"], actor=["agent"]),
                evidence=["Tests passed after pinning the version."],
                use_this_path="Pin the tool version before rerunning tests.",
                avoid_this="Do not keep retrying tests without checking dependency versions.",
                challenge_only_if="Use when the same dependency version mismatch appears.",
                liability_score=4,
                confidence=0.8,
            )
            store.add(candidate)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "promote", candidate.id, "--to", "situation"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Promotion Applied", output.getvalue())
            situations = MemoryStore(tmp).list(type=MemoryType.SITUATION)
            self.assertEqual(len(situations), 1)
            self.assertEqual(situations[0].id, candidate.id)

    def test_cli_review_anchor_proposal_does_not_promote_situation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                signals=["auth", "credentials", "security"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Previous rotation risk was resolved by enforcing lock order."],
                use_this_path="Acquire the rotation lock before updating active credentials.",
                avoid_this="Do not update credentials without the lock.",
                challenge_only_if="The rotation service no longer shares credential state.",
                liability_score=5,
                confidence=0.85,
            )
            store.add(situation)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "review", situation.id, "--to", "anchor"])

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Anchor Proposal Review", output.getvalue())
            self.assertIn("Status: Proposal only. No promotion has been applied.", output.getvalue())
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.ANCHOR)), 0)
            situations = MemoryStore(tmp).list(type=MemoryType.SITUATION)
            self.assertEqual(len(situations), 1)
            self.assertEqual(situations[0].id, situation.id)

    def test_cli_promote_anchor_updates_situation_with_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                signals=["auth", "credentials", "security"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Previous rotation risk was resolved by enforcing lock order."],
                use_this_path="Acquire the rotation lock before updating active credentials.",
                avoid_this="Do not update credentials without the lock.",
                challenge_only_if="The rotation service no longer shares credential state.",
                liability_score=5,
                confidence=0.85,
            )
            store.add(situation)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "promote",
                        situation.id,
                        "--to",
                        "anchor",
                        "--approved-by",
                        "security owner",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Promotion Applied", output.getvalue())
            self.assertIn("Approved By: security owner", output.getvalue())
            anchors = MemoryStore(tmp).list(type=MemoryType.ANCHOR)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0].id, situation.id)
            self.assertEqual(anchors[0].approved_by, "security owner")
            self.assertIn("Authority approval: security owner", anchors[0].evidence)


class ChallengeTests(unittest.TestCase):
    def test_challenge_practice_records_candidate_without_mutating_stable_memory(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
            approved_by="CMU core owner",
        )

        decision = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="A batch import task needs many memories at once, not only one compact Action Note.",
                benefit="Create a scoped exception for batch import planning.",
                risk="Too much context could make normal agent work noisy.",
                rollback="Keep the original preflight behavior for non-batch tasks.",
                challenged_by="agent",
                evidence=["Batch import planning requires comparing many candidate records."],
            ),
        )

        self.assertTrue(decision.saved)
        self.assertEqual(practice.type, MemoryType.PRACTICE)
        self.assertIsNotNone(decision.challenge_memory)
        assert decision.challenge_memory is not None
        self.assertEqual(decision.challenge_memory.type, MemoryType.CANDIDATE)
        self.assertIn("practice challenge", decision.challenge_memory.signals)
        self.assertIn(f"Challenges stable memory: {practice.id}", decision.challenge_memory.evidence)
        self.assertIn("Status: Challenge recorded. Stable memory was not changed.", decision.render())

    def test_challenge_blocks_non_stable_memory(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Local situation",
            summary="A reusable but not stable situation.",
            scope=MemoryScope(code=["cmu"]),
            evidence=["Observed during local work."],
        )

        decision = challenge_stable_memory(
            [situation],
            ChallengeRequest(
                memory_id=situation.id,
                mismatch="This should not use the stable-memory challenge path.",
                benefit="Use normal Situation refinement instead.",
                risk="Would add friction too early.",
                rollback="Keep it as a Situation.",
            ),
        )

        self.assertFalse(decision.saved)
        self.assertIn("only Practice or Anchor", decision.render())

    def test_resolve_challenge_exception_creates_exception_and_retires_challenge(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
            approved_by="CMU core owner",
        )
        challenge = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="Batch import planning needs many memories at once.",
                benefit="Create a scoped exception for batch import planning.",
                risk="Too much context could make normal agent work noisy.",
                rollback="Keep compact preflight for non-batch tasks.",
                challenged_by="agent",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [practice, challenge],
            ResolveChallengeRequest(
                challenge_id=challenge.id,
                outcome="exception",
                approved_by="CMU core owner",
            ),
        )

        self.assertTrue(resolution.applied)
        self.assertIsNotNone(resolution.outcome_memory)
        assert resolution.outcome_memory is not None
        self.assertEqual(resolution.outcome_memory.type, MemoryType.EXCEPTION)
        self.assertEqual(resolution.outcome_memory.approved_by, "CMU core owner")
        self.assertIn(f"Exception to stable memory: {practice.id}", resolution.outcome_memory.evidence)
        self.assertEqual(challenge.status, MemoryStatus.RETIRED)
        self.assertEqual(practice.type, MemoryType.PRACTICE)

    def test_resolve_challenge_strengthen_adds_evidence_without_changing_type(self) -> None:
        anchor = Memory.create(
            type=MemoryType.ANCHOR,
            title="Auth token rotation ordering",
            summary="Token rotation must acquire the lock before updating active credentials.",
            signals=["auth", "credentials", "security"],
            scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
            evidence=["Previous rotation risk was resolved by enforcing lock order."],
            use_this_path="Acquire the rotation lock before updating active credentials.",
            avoid_this="Do not update credentials without the lock.",
            challenge_only_if="The rotation service no longer shares credential state.",
            liability_score=5,
            confidence=0.75,
            approved_by="security owner",
        )
        challenge = challenge_stable_memory(
            [anchor],
            ChallengeRequest(
                memory_id=anchor.id,
                mismatch="The new service may isolate credential state.",
                benefit="Avoid unnecessary global lock ordering.",
                risk="Removing the lock too broadly could reintroduce races.",
                rollback="Keep the lock-order Anchor for shared credential state.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [anchor, challenge],
            ResolveChallengeRequest(
                challenge_id=challenge.id,
                outcome="strengthen",
                approved_by="security owner",
            ),
        )

        self.assertTrue(resolution.applied)
        self.assertIsNone(resolution.outcome_memory)
        self.assertEqual(anchor.type, MemoryType.ANCHOR)
        self.assertGreaterEqual(anchor.confidence, 0.8)
        self.assertIn(f"Challenge reviewed and precedent strengthened: {challenge.id}", anchor.evidence)
        self.assertEqual(challenge.status, MemoryStatus.RETIRED)

    def test_resolve_challenge_requires_approval(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Stable practice",
            summary="A stable practice.",
            scope=MemoryScope(code=["cmu"]),
            evidence=["Approved earlier."],
            use_this_path="Follow the stable path.",
            challenge_only_if="Constraints differ.",
        )
        challenge = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="Something changed.",
                benefit="Maybe improve the practice.",
                risk="Could weaken the precedent.",
                rollback="Keep the old practice.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [practice, challenge],
            ResolveChallengeRequest(challenge_id=challenge.id, outcome="exception", approved_by=""),
        )

        self.assertFalse(resolution.applied)
        self.assertIn("explicit owner/team approval required", resolution.render())

    def test_resolve_challenge_update_requires_explicit_replacement_details(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Stable practice",
            summary="A stable practice.",
            scope=MemoryScope(code=["cmu"]),
            evidence=["Approved earlier."],
            use_this_path="Follow the stable path.",
            avoid_this="Avoid the old trap.",
            challenge_only_if="Constraints differ.",
            approved_by="owner",
        )
        challenge = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="The old default is now too broad.",
                benefit="Replace the default with narrower guidance.",
                risk="A weak update could erase a useful precedent.",
                rollback="Restore the prior stable practice.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [practice, challenge],
            ResolveChallengeRequest(challenge_id=challenge.id, outcome="update", approved_by="owner"),
        )

        self.assertFalse(resolution.applied)
        self.assertIn("replacement_summary", resolution.render())
        self.assertIn("resolution_evidence", resolution.render())

    def test_resolve_challenge_update_mutates_stable_memory_after_approval(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should surface compact Action Notes only when memory changes action.",
            signals=["practice discovery"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
            approved_by="CMU core owner",
        )
        challenge = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="High-risk stable-memory work needs a visible preflight lead.",
                benefit="Clarify when the agent should mention the CMU-backed lead.",
                risk="Mentioning CMU too often could become noisy.",
                rollback="Return to quiet-only preflight behavior.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [practice, challenge],
            ResolveChallengeRequest(
                challenge_id=challenge.id,
                outcome="update",
                approved_by="CMU core owner",
                replacement_summary="CMU should stay quiet unless memory changes action, but high-risk matched work should name the CMU-backed lead.",
                replacement_use_path="Run preflight at task start and mention the memory-backed lead when it shapes high-risk work.",
                replacement_avoid="Do not expose raw memory or create a dashboard moment.",
                replacement_challenge="The task is small, local, low-risk, and follows an obvious existing pattern.",
                evidence=["Owner approved visible leads for high-risk matched work."],
            ),
        )

        self.assertTrue(resolution.applied)
        self.assertEqual(practice.summary, "CMU should stay quiet unless memory changes action, but high-risk matched work should name the CMU-backed lead.")
        self.assertIn("mention the memory-backed lead", practice.use_this_path)
        self.assertIn(f"Stable memory updated from challenge: {challenge.id}", practice.evidence)
        self.assertIn("Rollback path from challenge: Return to quiet-only preflight behavior.", practice.evidence)
        self.assertEqual(challenge.status, MemoryStatus.RETIRED)

    def test_resolve_challenge_retire_retires_stable_memory_after_approval(self) -> None:
        anchor = Memory.create(
            type=MemoryType.ANCHOR,
            title="Auth token rotation ordering",
            summary="Token rotation must acquire the lock before updating active credentials.",
            signals=["auth", "credentials", "security"],
            scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
            evidence=["Previous rotation risk was resolved by enforcing lock order."],
            use_this_path="Acquire the rotation lock before updating active credentials.",
            avoid_this="Do not update credentials without the lock.",
            challenge_only_if="The rotation service no longer shares credential state.",
            liability_score=5,
            confidence=0.85,
            approved_by="security owner",
        )
        challenge = challenge_stable_memory(
            [anchor],
            ChallengeRequest(
                memory_id=anchor.id,
                mismatch="The old shared credential service has been removed.",
                benefit="Stop guiding agents toward obsolete lock ordering.",
                risk="Retiring the anchor too early could hide a race in old deployments.",
                rollback="Restore the anchor for old shared credential deployments.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [anchor, challenge],
            ResolveChallengeRequest(
                challenge_id=challenge.id,
                outcome="retire",
                approved_by="security owner",
                retirement_reason="The shared credential service no longer exists in supported deployments.",
                evidence=["Supported deployments use isolated credential state."],
            ),
        )

        self.assertTrue(resolution.applied)
        self.assertEqual(anchor.status, MemoryStatus.RETIRED)
        self.assertIn(f"Stable memory retired from challenge: {challenge.id}", anchor.evidence)
        self.assertIn("Retirement reason: The shared credential service no longer exists in supported deployments.", anchor.evidence)
        self.assertEqual(challenge.status, MemoryStatus.RETIRED)

    def test_resolve_challenge_split_creates_scoped_stable_memory(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Task-start preflight stays quiet unless useful",
            summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
            signals=["practice discovery"],
            scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
            evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
            use_this_path="Run preflight at task start.",
            avoid_this="Do not dump memory into context just because it exists.",
            challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
            liability_score=4,
            confidence=0.9,
            approved_by="CMU core owner",
        )
        challenge = challenge_stable_memory(
            [practice],
            ChallengeRequest(
                memory_id=practice.id,
                mismatch="Batch import planning needs many memories at once.",
                benefit="Create a split-off practice for batch import planning.",
                risk="Too much context could make normal agent work noisy.",
                rollback="Keep compact preflight for non-batch tasks.",
            ),
        ).challenge_memory
        assert challenge is not None

        resolution = resolve_challenge(
            [practice, challenge],
            ResolveChallengeRequest(
                challenge_id=challenge.id,
                outcome="split",
                approved_by="CMU core owner",
                split_title="Batch import planning can inspect multiple memories",
                split_summary="Batch import planning may need a broader memory review before selecting candidates.",
                split_use_path="Inspect the relevant memory set before narrowing import decisions.",
                split_avoid="Do not apply broad memory review to ordinary task-start preflight.",
                split_challenge="The work is not a batch import, migration, or consolidation task.",
                split_scope=MemoryScope(code=["cmu"], workflow=["batch import"], actor=["agent"]),
                evidence=["Batch import planning requires comparing many candidate records."],
            ),
        )

        self.assertTrue(resolution.applied)
        self.assertIsNotNone(resolution.outcome_memory)
        assert resolution.outcome_memory is not None
        self.assertEqual(resolution.outcome_memory.type, MemoryType.PRACTICE)
        self.assertEqual(resolution.outcome_memory.approved_by, "CMU core owner")
        self.assertIn("batch import", resolution.outcome_memory.scope.workflow)
        self.assertIn(f"Split from stable memory: {practice.id}", resolution.outcome_memory.evidence)
        self.assertIn(f"Split-off stable memory: {resolution.outcome_memory.id}", practice.evidence)
        self.assertEqual(practice.status, MemoryStatus.ACTIVE)
        self.assertEqual(challenge.status, MemoryStatus.RETIRED)


class CliChallengeTests(unittest.TestCase):
    def test_cli_challenge_stores_candidate_and_preserves_anchor(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            anchor = Memory.create(
                type=MemoryType.ANCHOR,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                signals=["auth", "credentials", "security"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Previous rotation risk was resolved by enforcing lock order."],
                use_this_path="Acquire the rotation lock before updating active credentials.",
                avoid_this="Do not update credentials without the lock.",
                challenge_only_if="The rotation service no longer shares credential state.",
                liability_score=5,
                confidence=0.85,
                approved_by="security owner",
            )
            store.add(anchor)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "challenge",
                        anchor.id,
                        "--mismatch",
                        "The new rotation service uses isolated credential state.",
                        "--benefit",
                        "Allow a narrower path without global lock ordering.",
                        "--risk",
                        "Removing the lock too broadly could reintroduce credential races.",
                        "--rollback",
                        "Keep the original lock-order Anchor for shared credential state.",
                        "--challenged-by",
                        "security agent",
                        "--evidence",
                        "New service design document says credential state is isolated.",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Stable Memory Challenge", output.getvalue())
            self.assertIn("Stable memory was not changed", output.getvalue())
            anchors = MemoryStore(tmp).list(type=MemoryType.ANCHOR)
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0].id, anchor.id)
            candidates = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(candidates), 1)
            self.assertIn("anchor challenge", candidates[0].signals)
            self.assertIn(f"Challenges stable memory: {anchor.id}", candidates[0].evidence)

    def test_cli_resolve_challenge_exception_persists_exception_and_retires_challenge(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["practice discovery"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(practice)
            challenge = challenge_stable_memory(
                [practice],
                ChallengeRequest(
                    memory_id=practice.id,
                    mismatch="Batch import planning needs many memories at once.",
                    benefit="Create a scoped exception for batch import planning.",
                    risk="Too much context could make normal agent work noisy.",
                    rollback="Keep compact preflight for non-batch tasks.",
                ),
            ).challenge_memory
            assert challenge is not None
            store.add(challenge)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "resolve-challenge",
                        challenge.id,
                        "--outcome",
                        "exception",
                        "--approved-by",
                        "CMU core owner",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Challenge Resolution Applied", output.getvalue())
            exceptions = MemoryStore(tmp).list(type=MemoryType.EXCEPTION)
            self.assertEqual(len(exceptions), 1)
            self.assertIn(f"Exception to stable memory: {practice.id}", exceptions[0].evidence)
            retired = MemoryStore(tmp).list(type=MemoryType.CANDIDATE, status=MemoryStatus.RETIRED)
            self.assertEqual(len(retired), 1)
            self.assertEqual(retired[0].id, challenge.id)

    def test_cli_resolve_challenge_strengthen_updates_anchor_and_retires_challenge(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            anchor = Memory.create(
                type=MemoryType.ANCHOR,
                title="Auth token rotation ordering",
                summary="Token rotation must acquire the lock before updating active credentials.",
                signals=["auth", "credentials", "security"],
                scope=MemoryScope(code=["auth"], workflow=["credential rotation"], actor=["agent"]),
                evidence=["Previous rotation risk was resolved by enforcing lock order."],
                use_this_path="Acquire the rotation lock before updating active credentials.",
                avoid_this="Do not update credentials without the lock.",
                challenge_only_if="The rotation service no longer shares credential state.",
                liability_score=5,
                confidence=0.75,
                approved_by="security owner",
            )
            store.add(anchor)
            challenge = challenge_stable_memory(
                [anchor],
                ChallengeRequest(
                    memory_id=anchor.id,
                    mismatch="The new service may isolate credential state.",
                    benefit="Avoid unnecessary global lock ordering.",
                    risk="Removing the lock too broadly could reintroduce races.",
                    rollback="Keep the lock-order Anchor for shared credential state.",
                ),
            ).challenge_memory
            assert challenge is not None
            store.add(challenge)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "resolve-challenge",
                        challenge.id,
                        "--outcome",
                        "strengthen",
                        "--approved-by",
                        "security owner",
                    ]
                )

            self.assertEqual(exit_code, 0)
            anchors = MemoryStore(tmp).list(type=MemoryType.ANCHOR)
            self.assertEqual(len(anchors), 1)
            self.assertIn(f"Challenge reviewed and precedent strengthened: {challenge.id}", anchors[0].evidence)
            retired = MemoryStore(tmp).list(type=MemoryType.CANDIDATE, status=MemoryStatus.RETIRED)
            self.assertEqual(len(retired), 1)

    def test_cli_resolve_challenge_split_persists_new_stable_memory_and_retires_challenge(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Task-start preflight stays quiet unless useful",
                summary="CMU should check memory at task start but only surface compact Action Notes when memory changes action.",
                signals=["practice discovery"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU product spec defines the Work Cycle as always available, rarely loud."],
                use_this_path="Run preflight at task start.",
                avoid_this="Do not dump memory into context just because it exists.",
                challenge_only_if="The task is small, local, low-risk, and follows an obvious existing pattern.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(practice)
            challenge = challenge_stable_memory(
                [practice],
                ChallengeRequest(
                    memory_id=practice.id,
                    mismatch="Batch import planning needs many memories at once.",
                    benefit="Create a split-off practice for batch import planning.",
                    risk="Too much context could make normal agent work noisy.",
                    rollback="Keep compact preflight for non-batch tasks.",
                ),
            ).challenge_memory
            assert challenge is not None
            store.add(challenge)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "resolve-challenge",
                        challenge.id,
                        "--outcome",
                        "split",
                        "--approved-by",
                        "CMU core owner",
                        "--split-title",
                        "Batch import planning can inspect multiple memories",
                        "--split-summary",
                        "Batch import planning may need a broader memory review before selecting candidates.",
                        "--split-use-path",
                        "Inspect the relevant memory set before narrowing import decisions.",
                        "--split-avoid",
                        "Do not apply broad memory review to ordinary task-start preflight.",
                        "--split-challenge",
                        "The work is not a batch import, migration, or consolidation task.",
                        "--scope-code",
                        "cmu",
                        "--scope-workflow",
                        "batch import",
                        "--scope-actor",
                        "agent",
                        "--evidence",
                        "Batch import planning requires comparing many candidate records.",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Challenge Resolution Applied", output.getvalue())
            practices = MemoryStore(tmp).list(type=MemoryType.PRACTICE)
            self.assertEqual(len(practices), 2)
            split_practice = next(memory for memory in practices if memory.id != practice.id)
            self.assertIn("batch import", split_practice.scope.workflow)
            self.assertIn(f"Split from stable memory: {practice.id}", split_practice.evidence)
            original = next(memory for memory in practices if memory.id == practice.id)
            self.assertIn(f"Split-off stable memory: {split_practice.id}", original.evidence)
            retired = MemoryStore(tmp).list(type=MemoryType.CANDIDATE, status=MemoryStatus.RETIRED)
            self.assertEqual(len(retired), 1)


class AgentIntegrationBoundaryTests(unittest.TestCase):
    def test_manifest_exposes_stable_agent_tool_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest = AgentIntegration(tmp).manifest()

            self.assertEqual(manifest["api_version"], AGENT_API_VERSION)
            self.assertEqual(
                [tool["name"] for tool in manifest["tools"]],
                ["cmu_task_start", "cmu_after_work", "cmu_link_checkpoint", "cmu_review"],
            )
            self.assertTrue(next(tool for tool in manifest["tools"] if tool["name"] == "cmu_review")["mutates"] is False)

    def test_direct_agent_boundary_runs_guidance_learning_checkpoint_and_review_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Check CMU before structural implementation",
                summary="Structural CMU implementation starts by checking scoped memory guidance.",
                signals=["new convention"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["The CMU Work Cycle requires task-start guidance before meaningful work."],
                use_this_path="Inspect the scoped CMU practice before editing structural code.",
                avoid_this="Do not start broad CMU implementation without checking memory.",
                challenge_only_if="The task is a tiny local edit with no structural impact.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(practice)
            integration = AgentIntegration(tmp)

            started = integration.invoke(
                "cmu_task_start",
                {
                    "prompt": "Implement the CMU agent integration boundary",
                    "actor": "agent",
                    "area": "cmu",
                    "files": ["cmu/agent_api.py"],
                    "workflow": ["implementation"],
                    "risk": "high",
                },
            )

            self.assertTrue(started["ok"])
            self.assertEqual(started["status"], "action-note")
            self.assertEqual(started["matched_memory"]["id"], practice.id)
            self.assertEqual(started["action_note"]["recognized_situation"], practice.title)
            use_id = started["receipt"]["id"]
            stored_receipt = MemoryUseStore(tmp).get(use_id)
            self.assertEqual(stored_receipt.source_command, "agent.task-start")

            learned = integration.invoke(
                "cmu_after_work",
                {
                    "situation": "Agent runtimes need one versioned CMU tool boundary instead of reconstructing CLI orchestration.",
                    "signals": ["new convention"],
                    "outcome": "The direct boundary now exposes structured task-start, learning, checkpoint, and review calls.",
                    "worked": "Route runtime calls through the AgentIntegration service.",
                    "failed": "Depending on rendered CLI output makes integrations brittle.",
                    "future_use": "Use this boundary when wiring MCP, SDK, or autonomous runner integrations.",
                    "evidence": ["The end-to-end agent integration boundary test exercises the complete tool loop."],
                    "liability_score": 4,
                    "scope": {
                        "code": ["cmu/agent_api.py"],
                        "workflow": ["agent integration"],
                        "actor": ["agent"],
                    },
                    "confidence": 0.85,
                },
            )

            self.assertTrue(learned["ok"])
            self.assertEqual(learned["status"], "candidate-saved")
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 1)

            linked = integration.invoke(
                "cmu_link_checkpoint",
                {
                    "use_id": use_id,
                    "manual_commit": {
                        "hash": "abc123",
                        "message": "Add CMU agent integration boundary",
                        "files": ["cmu/agent_api.py"],
                    },
                },
            )

            self.assertTrue(linked["ok"])
            self.assertEqual(linked["status"], "checkpoint-linked")
            self.assertEqual(linked["decision"]["receipt"]["outcome_signal"], "committed")

            reviewed = integration.invoke("cmu_review", {"memory_id": practice.id})

            self.assertTrue(reviewed["ok"])
            self.assertEqual(reviewed["status"], "review-ready")
            self.assertEqual(reviewed["cards"][0]["memory_id"], practice.id)
            self.assertEqual(reviewed["cards"][0]["linked_uses"], 1)
            self.assertEqual(reviewed["cards"][0]["source_counts"], {"agent.task-start": 1})

    def test_task_start_silent_skip_keeps_agent_boundary_quiet(self) -> None:
        with TemporaryDirectory() as tmp:
            response = AgentIntegration(tmp).invoke(
                "cmu_task_start",
                {
                    "prompt": "Adjust a local style label",
                    "actor": "agent",
                    "area": "ui",
                    "files": ["ui/label.css"],
                    "risk": "low",
                },
            )

            self.assertEqual(response["status"], "silent-skip")
            self.assertIsNone(response["action_note"])
            self.assertIsNone(response["receipt"])
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_agent_tools_and_agent_call_render_machine_readable_json(self) -> None:
        with TemporaryDirectory() as tmp:
            tools_output = StringIO()
            with redirect_stdout(tools_output):
                tools_exit = main(["--root", tmp, "agent-tools"])
            manifest = json.loads(tools_output.getvalue())

            self.assertEqual(tools_exit, 0)
            self.assertEqual(manifest["api_version"], AGENT_API_VERSION)

            call_output = StringIO()
            with redirect_stdout(call_output):
                call_exit = main(
                    [
                        "--root",
                        tmp,
                        "agent-call",
                        "cmu_task_start",
                        "--input",
                        json.dumps({"prompt": "Adjust a local style label", "area": "ui", "risk": "low"}),
                    ]
                )
            response = json.loads(call_output.getvalue())

            self.assertEqual(call_exit, 0)
            self.assertEqual(response["tool"], "cmu_task_start")
            self.assertEqual(response["status"], "silent-skip")

            input_file = Path(tmp) / "agent-call.json"
            input_file.write_text(json.dumps({"prompt": "Adjust a local style label", "area": "ui", "risk": "low"}), encoding="utf-8")
            file_output = StringIO()
            with redirect_stdout(file_output):
                file_exit = main(["--root", tmp, "agent-call", "cmu_task_start", "--input-file", str(input_file)])
            file_response = json.loads(file_output.getvalue())

            self.assertEqual(file_exit, 0)
            self.assertEqual(file_response["status"], "silent-skip")

    def test_unknown_agent_tool_returns_structured_error(self) -> None:
        response = AgentIntegration(".").invoke("cmu_missing_tool", {})

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "unknown-tool")
        self.assertIn("cmu_task_start", response["available_tools"])


class AutonomousRunnerHooksTests(unittest.TestCase):
    def test_runner_hooks_manifest_maps_events_to_stable_agent_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest = AutonomousRunnerHooks(tmp).manifest()

            self.assertEqual(manifest["version"], RUNNER_HOOKS_VERSION)
            self.assertEqual(manifest["agent_api_version"], AGENT_API_VERSION)
            self.assertEqual(
                [(hook["name"], hook["delegates_to"], hook["mutates"]) for hook in manifest["hooks"]],
                [
                    ("before_task", "cmu_task_start", True),
                    ("after_task", "cmu_after_work", True),
                    ("after_checkpoint", "cmu_link_checkpoint", True),
                    ("review", "cmu_review", False),
                ],
            )

    def test_runner_hooks_run_real_guidance_learning_checkpoint_and_review_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use CMU runner hooks for autonomous integrations",
                summary="Autonomous runners should call CMU through event hooks that delegate to the stable agent boundary.",
                signals=["runner hooks", "agent integration"],
                scope=MemoryScope(code=["cmu/runner_hooks.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["The runner hook layer should keep autonomous integrations out of human CLI parsing."],
                use_this_path="Call before_task, after_task, after_checkpoint, and review through the runner hook facade.",
                avoid_this="Do not rebuild task-start retrieval or Candidate Memory gates inside runner adapters.",
                challenge_only_if="A host already provides the same AgentIntegration tool-call protocol directly.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            hooks = AutonomousRunnerHooks(tmp)

            started = hooks.before_task(
                "wire CMU autonomous runner hooks",
                actor="agent",
                area="cmu",
                files=["cmu/runner_hooks.py"],
                workflow=["agent integration"],
                risk="high",
            )

            self.assertTrue(started.ok)
            self.assertEqual(started.hook, "before_task")
            self.assertEqual(started.status, "action-note")
            self.assertTrue(started.mutates)
            self.assertEqual(started.response["matched_memory"]["id"], practice.id)
            self.assertEqual(started.response["action_note"]["recognized_situation"], practice.title)
            use_id = started.response["receipt"]["id"]
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.id, use_id)
            self.assertEqual(receipt.source_command, "agent.task-start")
            self.assertEqual(started.next_hooks, ["after_task", "after_checkpoint", "review"])

            skipped = hooks.after_task(reusable_learning=False)

            self.assertEqual(skipped.status, "skipped-no-reusable-learning")
            self.assertFalse(skipped.mutates)
            self.assertEqual(MemoryStore(tmp).list(type=MemoryType.CANDIDATE), [])

            learned = hooks.after_task(
                reusable_learning=True,
                title="Autonomous runner hooks delegate to AgentIntegration",
                situation="Autonomous runner hooks should stay event-shaped while delegating to AgentIntegration.",
                signals=["runner hooks", "agent integration"],
                outcome="The hook facade gives runners before-task, after-task, checkpoint, and review events.",
                worked="Keep hook code thin and let CentralMemoryUnit enforce CMU behavior.",
                failed="Parsing human CLI reports would make autonomous runners brittle.",
                future_use="Use this hook layer when wiring autonomous agent runners into CMU.",
                evidence=["The runner hook test exercises the real persisted Candidate Memory path."],
                liability_score=4,
                scope={"code": ["cmu/runner_hooks.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                confidence=0.85,
            )

            self.assertTrue(learned.ok)
            self.assertEqual(learned.status, "candidate-saved")
            self.assertTrue(learned.mutates)
            [candidate] = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(candidate.title, "Autonomous runner hooks delegate to AgentIntegration")
            self.assertEqual(candidate.summary, "Autonomous runner hooks should stay event-shaped while delegating to AgentIntegration.")

            linked = hooks.after_checkpoint(
                use_id,
                manual_commit={
                    "hash": "runner123",
                    "message": "Add autonomous runner hooks",
                    "files": ["cmu/runner_hooks.py", "tests/test_cmu_spine.py"],
                },
            )

            self.assertTrue(linked.ok)
            self.assertEqual(linked.status, "checkpoint-linked")
            self.assertTrue(linked.mutates)
            linked_receipt = MemoryUseStore(tmp).get(use_id)
            self.assertEqual(linked_receipt.commit_hash, "runner123")
            self.assertEqual(linked_receipt.outcome_signal, "committed")

            reviewed = hooks.review(practice.id)

            self.assertTrue(reviewed.ok)
            self.assertEqual(reviewed.status, "review-ready")
            self.assertFalse(reviewed.mutates)
            self.assertEqual(reviewed.response["cards"][0]["memory_id"], practice.id)
            self.assertEqual(reviewed.response["cards"][0]["linked_uses"], 1)
            self.assertEqual(reviewed.response["cards"][0]["source_counts"], {"agent.task-start": 1})

    def test_runner_hooks_preserve_silent_skip_without_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            result = AutonomousRunnerHooks(tmp).before_task(
                "adjust local label spacing",
                actor="agent",
                area="ui",
                files=["ui/label.css"],
                risk="low",
            )

            self.assertEqual(result.status, "silent-skip")
            self.assertFalse(result.mutates)
            self.assertIsNone(result.response["receipt"])
            self.assertEqual(result.next_hooks, ["after_task", "review"])
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_runner_hooks_renders_contract_and_executes_real_before_task_json(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Runner hook CLI can inspect autonomous integration",
                summary="The runner-hooks command should expose the hook contract and execute before_task when prompted.",
                signals=["runner hooks", "cli"],
                scope=MemoryScope(code=["cmu/runner_hooks.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["Manual verification needs a CLI path over the real hook code."],
                use_this_path="Use cmu runner-hooks to inspect the hook sequence or run a before_task proof.",
                avoid_this="Do not treat runner hook docs as proof unless the executed hook reaches the real store.",
                challenge_only_if="The runner already calls AgentIntegration directly.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)

            contract_output = StringIO()
            with redirect_stdout(contract_output):
                contract_exit = main(["--root", tmp, "runner-hooks"])

            self.assertEqual(contract_exit, 0)
            rendered_contract = contract_output.getvalue()
            self.assertIn("CMU Autonomous Runner Hooks", rendered_contract)
            self.assertIn("before_task (task.start): cmu_task_start", rendered_contract)
            self.assertEqual(MemoryUseStore(tmp).list(), [])

            json_output = StringIO()
            with redirect_stdout(json_output):
                json_exit = main(
                    [
                        "--root",
                        tmp,
                        "runner-hooks",
                        "wire runner hook CLI proof",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--file",
                        "cmu/runner_hooks.py",
                        "--workflow",
                        "agent integration",
                        "--risk",
                        "high",
                        "--json",
                    ]
                )
            payload = json.loads(json_output.getvalue())

            self.assertEqual(json_exit, 0)
            self.assertEqual(payload["manifest"]["version"], RUNNER_HOOKS_VERSION)
            self.assertEqual(payload["result"]["hook"], "before_task")
            self.assertEqual(payload["result"]["status"], "action-note")
            self.assertEqual(payload["result"]["response"]["matched_memory"]["id"], practice.id)
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.id, payload["result"]["response"]["receipt"]["id"])

    def test_runner_hooks_report_is_read_only_without_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            before_memories = MemoryStore(tmp).list()
            before_receipts = MemoryUseStore(tmp).list()

            report = runner_hooks_report(tmp)
            rendered = report.render()

            self.assertIn("CMU Autonomous Runner Hooks", rendered)
            self.assertIn("Proof Meaning: autonomous runners can use these event hooks", rendered)
            self.assertIsNone(report.result)
            self.assertEqual(MemoryStore(tmp).list(), before_memories)
            self.assertEqual(MemoryUseStore(tmp).list(), before_receipts)


class CodexRunnerAdapterTests(unittest.TestCase):
    def test_codex_runner_manifest_is_read_only_and_maps_events_to_hooks(self) -> None:
        with TemporaryDirectory() as tmp:
            before_memories = MemoryStore(tmp).list()
            before_receipts = MemoryUseStore(tmp).list()

            report = codex_runner_report(tmp)
            rendered = report.render()

            self.assertEqual(report.manifest["version"], CODEX_RUNNER_ADAPTER_VERSION)
            self.assertEqual(report.manifest["host"], "codex")
            self.assertEqual(
                [(event["event"], event["hook"], event["mutates"]) for event in report.manifest["events"]],
                [
                    ("codex.task_started", "before_task", True),
                    ("codex.task_finished", "after_task", True),
                    ("codex.checkpoint_created", "after_checkpoint", True),
                    ("codex.review_requested", "review", False),
                ],
            )
            self.assertIn("CMU Codex Runner Adapter", rendered)
            self.assertIn("Proof Meaning: Codex-style runner events", rendered)
            self.assertIsNone(report.result)
            self.assertEqual(MemoryStore(tmp).list(), before_memories)
            self.assertEqual(MemoryUseStore(tmp).list(), before_receipts)

    def test_codex_runner_task_finish_checkpoint_and_review_use_real_store_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use Codex runner adapter for host events",
                summary="Codex-style runner events should enter CMU through the host adapter and existing hooks.",
                signals=["codex runner", "host adapter"],
                scope=MemoryScope(code=["cmu/codex_adapter.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["The adapter should translate host events without duplicating memory logic."],
                use_this_path="Route Codex task, finish, checkpoint, and review events through the adapter.",
                avoid_this="Do not create a separate Codex memory path outside AgentIntegration.",
                challenge_only_if="Codex can call AgentIntegration directly with the same lifecycle semantics.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            adapter = CodexRunnerAdapter(tmp)

            started = adapter.handle(
                {
                    "event": "codex.task_started",
                    "payload": {
                        "prompt": "wire Codex runner host adapter",
                        "actor": "agent",
                        "area": "cmu",
                        "files": ["cmu/codex_adapter.py"],
                        "workflow": ["agent integration"],
                        "risk": "high",
                    },
                }
            )

            self.assertTrue(started.ok)
            self.assertEqual(started.status, "action-note")
            self.assertEqual(started.hook_result["hook"], "before_task")
            self.assertEqual(started.hook_result["response"]["matched_memory"]["id"], practice.id)
            use_id = started.hook_result["response"]["receipt"]["id"]
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.id, use_id)
            self.assertEqual(receipt.source_command, "agent.task-start")

            learned = adapter.handle(
                {
                    "event": "task_finished",
                    "payload": {
                        "reusable_learning": True,
                        "title": "Codex runner adapter delegates to hooks",
                        "situation": "Codex host adapters should translate runner events while leaving CMU logic in hooks.",
                        "signals": ["codex runner", "host adapter"],
                        "outcome": "The adapter can handle start, finish, checkpoint, and review events.",
                        "worked": "Normalize Codex event JSON and call AutonomousRunnerHooks.",
                        "failed": "Adding Codex-only memory logic would bypass existing receipt and Candidate gates.",
                        "future_use": "Use this adapter pattern for future host-specific runner integrations.",
                        "evidence": ["The test verifies persisted Candidate Memory through MemoryStore."],
                        "liability_score": 4,
                        "scope": {"code": ["cmu/codex_adapter.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                        "confidence": 0.85,
                    },
                }
            )

            self.assertTrue(learned.ok)
            self.assertEqual(learned.status, "candidate-saved")
            [candidate] = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(candidate.title, "Codex runner adapter delegates to hooks")

            linked = adapter.handle(
                {
                    "event": "checkpoint_created",
                    "payload": {
                        "use_id": use_id,
                        "note": "manual adapter proof",
                        "manual_commit": {
                            "hash": "codex123",
                            "message": "Add Codex runner adapter",
                            "files": ["cmu/codex_adapter.py", "tests/test_cmu_spine.py"],
                        },
                    },
                }
            )

            self.assertTrue(linked.ok)
            self.assertEqual(linked.status, "checkpoint-linked")
            linked_receipt = MemoryUseStore(tmp).get(use_id)
            self.assertEqual(linked_receipt.commit_hash, "codex123")
            self.assertEqual(linked_receipt.outcome_signal, "committed")

            reviewed = adapter.handle({"event": "review_requested", "payload": {"memory_id": practice.id}})

            self.assertTrue(reviewed.ok)
            self.assertEqual(reviewed.status, "review-ready")
            self.assertFalse(reviewed.hook_result["mutates"])
            self.assertEqual(reviewed.hook_result["response"]["cards"][0]["memory_id"], practice.id)

    def test_cli_codex_runner_executes_json_event_and_reports_invalid_events(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Codex runner CLI can execute host events",
                summary="The codex-runner command should execute JSON host events through the adapter.",
                signals=["codex runner", "cli"],
                scope=MemoryScope(code=["cmu/codex_adapter.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["CLI adapter proof should touch the same receipt store as direct adapter use."],
                use_this_path="Use cmu codex-runner --input for a local host event proof.",
                avoid_this="Do not judge host integration from manifest output only.",
                challenge_only_if="The host uses MCP or SDK directly.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            event = {
                "event": "task_started",
                "payload": {
                    "prompt": "execute Codex runner CLI event",
                    "actor": "agent",
                    "area": "cmu",
                    "files": ["cmu/codex_adapter.py"],
                    "workflow": ["agent integration"],
                    "risk": "high",
                },
            }
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "codex-runner", "--input", json.dumps(event), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["manifest"]["version"], CODEX_RUNNER_ADAPTER_VERSION)
            self.assertEqual(payload["result"]["event"], "codex.task_started")
            self.assertEqual(payload["result"]["status"], "action-note")
            self.assertEqual(payload["result"]["hook_result"]["response"]["matched_memory"]["id"], practice.id)
            self.assertEqual(len(MemoryUseStore(tmp).list()), 1)

            bad_output = StringIO()
            with redirect_stdout(bad_output):
                bad_exit = main(["--root", tmp, "codex-runner", "--input", json.dumps({"event": "codex.unknown"}), "--json"])

            bad_payload = json.loads(bad_output.getvalue())
            self.assertEqual(bad_exit, 1)
            self.assertEqual(bad_payload["result"]["status"], "invalid-event")
            self.assertIn("Unknown Codex runner event", bad_payload["result"]["error"])

    def test_cli_codex_runner_accepts_utf8_bom_input_files(self) -> None:
        with TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "codex-event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "event": "task_started",
                        "payload": {
                            "prompt": "adjust local label spacing",
                            "actor": "agent",
                            "area": "ui",
                            "files": ["ui/label.css"],
                            "risk": "low",
                        },
                    }
                ),
                encoding="utf-8-sig",
            )
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "codex-runner", "--input-file", str(event_path), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["result"]["event"], "codex.task_started")
            self.assertEqual(payload["result"]["status"], "silent-skip")
            self.assertIsNone(payload["result"]["hook_result"]["response"]["receipt"])
            self.assertEqual(MemoryUseStore(tmp).list(), [])


class OpenAIRunnerAdapterTests(unittest.TestCase):
    def test_openai_runner_manifest_is_read_only_and_maps_events_to_hooks(self) -> None:
        with TemporaryDirectory() as tmp:
            before_memories = MemoryStore(tmp).list()
            before_receipts = MemoryUseStore(tmp).list()

            report = openai_runner_report(tmp)
            rendered = report.render()

            self.assertEqual(report.manifest["version"], OPENAI_RUNNER_ADAPTER_VERSION)
            self.assertEqual(report.manifest["host"], "openai")
            self.assertEqual(
                [(event["event"], event["hook"], event["mutates"]) for event in report.manifest["events"]],
                [
                    ("openai.run.started", "before_task", True),
                    ("openai.run.completed", "after_task", True),
                    ("openai.checkpoint.created", "after_checkpoint", True),
                    ("openai.review.requested", "review", False),
                ],
            )
            self.assertIn("CMU OpenAI Runner Adapter", rendered)
            self.assertIn("Proof Meaning: OpenAI Agents-style run events", rendered)
            self.assertIsNone(report.result)
            self.assertEqual(MemoryStore(tmp).list(), before_memories)
            self.assertEqual(MemoryUseStore(tmp).list(), before_receipts)

    def test_openai_runner_task_lifecycle_uses_real_store_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="OpenAI runner adapter delegates to CMU hooks",
                summary="OpenAI Agents-style runner events should enter CMU through the host adapter and existing hooks.",
                signals=["openai runner", "host adapter"],
                scope=MemoryScope(code=["cmu/openai_adapter.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["The adapter should translate host events without duplicating memory logic."],
                use_this_path="Route OpenAI run lifecycle events through the adapter.",
                avoid_this="Do not create a separate OpenAI memory path outside AutonomousRunnerHooks.",
                challenge_only_if="The host uses MCP or SDK directly with the same lifecycle semantics.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            adapter = OpenAIRunnerAdapter(tmp)

            started = adapter.handle(
                {
                    "event": "run.started",
                    "payload": {
                        "input": "wire OpenAI runner host adapter",
                        "actor": "agent",
                        "area": "cmu",
                        "files": ["cmu/openai_adapter.py"],
                        "workflow": ["agent integration"],
                        "risk": "high",
                    },
                }
            )

            self.assertTrue(started.ok)
            self.assertEqual(started.status, "action-note")
            self.assertEqual(started.hook_result["hook"], "before_task")
            use_id = started.hook_result["response"]["receipt"]["id"]
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.id, use_id)

            learned = adapter.handle(
                {
                    "event": "openai.run.completed",
                    "payload": {
                        "reusable_learning": True,
                        "title": "OpenAI adapter uses the common runner hook contract",
                        "situation": "Host adapters should translate runtime events while leaving CMU logic in hooks.",
                        "signals": ["openai runner", "host adapter"],
                        "outcome": "The adapter can handle start, finish, checkpoint, and review events.",
                        "worked": "Normalize OpenAI event JSON and call AutonomousRunnerHooks.",
                        "failed": "Adding OpenAI-only memory logic would bypass existing receipt and Candidate gates.",
                        "future_use": "Use this adapter pattern for future host-specific runner integrations.",
                        "evidence": ["The test verifies persisted Candidate Memory through MemoryStore."],
                        "liability_score": 4,
                        "scope": {"code": ["cmu/openai_adapter.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                        "confidence": 0.85,
                    },
                }
            )
            self.assertTrue(learned.ok)
            self.assertEqual(learned.status, "candidate-saved")
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 1)

            linked = adapter.handle(
                {
                    "event": "checkpoint.created",
                    "payload": {
                        "use_id": use_id,
                        "manual_commit": {
                            "hash": "openai123",
                            "message": "Add OpenAI runner adapter",
                            "files": ["cmu/openai_adapter.py", "tests/test_cmu_spine.py"],
                        },
                    },
                }
            )
            self.assertTrue(linked.ok)
            self.assertEqual(linked.status, "checkpoint-linked")
            self.assertEqual(MemoryUseStore(tmp).get(use_id).commit_hash, "openai123")

            reviewed = adapter.handle({"event": "review.requested", "payload": {"memory_id": practice.id}})
            self.assertTrue(reviewed.ok)
            self.assertEqual(reviewed.status, "review-ready")
            self.assertFalse(reviewed.hook_result["mutates"])

    def test_cli_openai_runner_executes_json_event_and_reports_invalid_events(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="OpenAI runner CLI can execute host events",
                summary="The openai-runner command should execute JSON host events through the adapter.",
                signals=["openai runner", "cli"],
                scope=MemoryScope(code=["cmu/openai_adapter.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["CLI adapter proof should touch the same receipt store as direct adapter use."],
                use_this_path="Use cmu openai-runner --input for a local host event proof.",
                avoid_this="Do not judge host integration from manifest output only.",
                challenge_only_if="The host uses MCP or SDK directly.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            event = {
                "event": "run.started",
                "payload": {
                    "input": "execute OpenAI runner CLI event",
                    "actor": "agent",
                    "area": "cmu",
                    "files": ["cmu/openai_adapter.py"],
                    "workflow": ["agent integration"],
                    "risk": "high",
                },
            }

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "openai-runner", "--input", json.dumps(event), "--json"])
            payload = json.loads(output.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["manifest"]["version"], OPENAI_RUNNER_ADAPTER_VERSION)
            self.assertEqual(payload["result"]["event"], "openai.run.started")
            self.assertEqual(payload["result"]["status"], "action-note")
            self.assertEqual(len(MemoryUseStore(tmp).list()), 1)

            bad_output = StringIO()
            with redirect_stdout(bad_output):
                bad_exit = main(["--root", tmp, "openai-runner", "--input", json.dumps({"event": "openai.unknown"}), "--json"])
            bad_payload = json.loads(bad_output.getvalue())
            self.assertEqual(bad_exit, 1)
            self.assertEqual(bad_payload["result"]["status"], "invalid-event")
            self.assertIn("Unknown OpenAI runner event", bad_payload["result"]["error"])


class RunnerScenarioEvidenceTests(unittest.TestCase):
    def test_runner_scenario_runs_full_lifecycle_in_isolated_store_without_source_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use runner scenario proof for hook changes",
                summary="Runner hook changes should be proven through isolated lifecycle scenarios.",
                signals=["runner scenario", "agent integration"],
                scope=MemoryScope(code=["cmu/runner_scenarios.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["Scenario evidence should execute real hooks without mutating the source memory base."],
                use_this_path="Run an isolated runner scenario before trusting hook behavior changes.",
                avoid_this="Do not judge runner hooks only through static manifest checks.",
                challenge_only_if="A host-specific adapter has stronger end-to-end evidence.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(root).add(practice)
            before_memories = (root / ".cmu" / "memories.json").read_text(encoding="utf-8")
            before_uses = (root / ".cmu" / "uses.json").read_text(encoding="utf-8") if (root / ".cmu" / "uses.json").exists() else ""

            report = run_runner_scenario(
                root,
                RunnerScenarioRequest(
                    prompt="implement CMU runner scenario evidence",
                    actor="agent",
                    area="cmu",
                    files=["cmu/runner_scenarios.py"],
                    workflow=["agent integration"],
                    risk="high",
                    run_after_task=True,
                    reusable_learning=True,
                    title="Carry surfaced receipt ids into checkpoint links",
                    situation="Checkpoint linking from autonomous lifecycle proofs depends on preserving the receipt id returned at task start.",
                    signals=["explained failure"],
                    outcome="The lifecycle proof can connect the before-task receipt to later checkpoint evidence.",
                    worked="Pass the task-start receipt id directly into the checkpoint hook.",
                    failed="Recomputing or guessing the receipt id would leave checkpoint evidence unlinked.",
                    future_use="Use this when wiring any event sequence that links memory-use evidence after a checkpoint.",
                    evidence=["The test checks isolated Candidate and receipt counts while source store stays unchanged."],
                    liability_score=4,
                    scope={"code": ["cmu/runner_scenarios.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                    confidence=0.85,
                    checkpoint_hash="scenario123",
                    checkpoint_message="Add runner scenario evidence",
                    checkpoint_files=["cmu/runner_scenarios.py", "tests/test_cmu_spine.py"],
                    expect_start="action-note",
                    expect_memory=practice.id,
                    expect_candidate="candidate-saved",
                    expect_checkpoint="checkpoint-linked",
                ),
                work_dir=root / ".manual" / "test-runner-scenario",
            )

            rendered = report.render()
            self.assertEqual(report.start.status, "action-note")
            self.assertEqual(report.after_task.status, "candidate-saved")
            self.assertEqual(report.checkpoint.status, "checkpoint-linked")
            self.assertEqual(report.review.status, "review-ready")
            self.assertTrue(report.passed, rendered)
            self.assertEqual(report.source_memory_count, 1)
            self.assertEqual(report.source_receipt_count, 0)
            self.assertEqual(report.isolated_memory_count, 2)
            self.assertEqual(report.isolated_receipt_count, 1)
            self.assertIn("CMU Runner Scenario", rendered)
            self.assertIn("Mode: read-only source-store proof", rendered)
            self.assertIn("- start: pass", rendered)
            self.assertIn("- candidate: pass", rendered)
            self.assertEqual(before_memories, (root / ".cmu" / "memories.json").read_text(encoding="utf-8"))
            if before_uses:
                self.assertEqual(before_uses, (root / ".cmu" / "uses.json").read_text(encoding="utf-8"))
            else:
                self.assertFalse((root / ".cmu" / "uses.json").exists())

    def test_runner_scenario_proves_silent_skip_and_after_task_no_learning_skip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            report = run_runner_scenario(
                root,
                RunnerScenarioRequest(
                    prompt="adjust local label spacing",
                    actor="agent",
                    area="ui",
                    files=["ui/label.css"],
                    risk="low",
                    run_after_task=True,
                    reusable_learning=False,
                    expect_start="silent-skip",
                    expect_memory="none",
                    expect_candidate="skipped-no-reusable-learning",
                    expect_checkpoint="not-run",
                ),
                work_dir=root / ".manual" / "test-runner-scenario",
            )

            self.assertTrue(report.passed, report.render())
            self.assertEqual(report.start.status, "silent-skip")
            self.assertEqual(report.after_task.status, "skipped-no-reusable-learning")
            self.assertEqual(report.isolated_memory_count, 0)
            self.assertEqual(report.isolated_receipt_count, 0)
            self.assertEqual(MemoryStore(root).list(), [])
            self.assertEqual(MemoryUseStore(root).list(), [])

    def test_cli_runner_scenario_strict_pass_uses_real_hooks_without_source_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Runner scenario CLI proves hook behavior",
                summary="The CLI runner scenario should execute hooks in an isolated store.",
                signals=["runner scenario", "cli"],
                scope=MemoryScope(code=["cmu/runner_scenarios.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["CLI verification should not mutate source receipts."],
                use_this_path="Use cmu runner-scenario for isolated lifecycle proof.",
                avoid_this="Do not create source receipts during scenario evaluation.",
                challenge_only_if="The operator intentionally runs cmu runner-hooks with a prompt.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(root).add(practice)
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        str(root),
                        "runner-scenario",
                        "verify runner scenario CLI",
                        "--actor",
                        "agent",
                        "--area",
                        "cmu",
                        "--file",
                        "cmu/runner_scenarios.py",
                        "--workflow",
                        "agent integration",
                        "--risk",
                        "high",
                        "--after-task",
                        "--expect-start",
                        "action-note",
                        "--expect-memory",
                        practice.id,
                        "--expect-candidate",
                        "skipped-no-reusable-learning",
                        "--expect-checkpoint",
                        "not-run",
                        "--strict",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0, rendered)
            self.assertIn("CMU Runner Scenario", rendered)
            self.assertIn("Verdict: pass", rendered)
            self.assertEqual(MemoryUseStore(root).list(), [])

    def test_cli_runner_scenario_strict_fails_when_expectation_misses(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "runner-scenario",
                        "adjust local label spacing",
                        "--area",
                        "ui",
                        "--risk",
                        "low",
                        "--expect-start",
                        "action-note",
                        "--strict",
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("- start: fail (expected action-note; actual silent-skip)", rendered)
            self.assertIn("Verdict: review", rendered)


class McpIntegrationTests(unittest.TestCase):
    def test_mcp_exposes_exact_cmu_tools_with_host_usable_schemas(self) -> None:
        tools = mcp_tool_definitions()

        self.assertEqual(
            [tool["name"] for tool in tools],
            ["cmu_task_start", "cmu_after_work", "cmu_link_checkpoint", "cmu_review"],
        )
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("properties", tool["inputSchema"])
            self.assertIn("readOnlyHint", tool["annotations"])
        self.assertEqual(next(tool for tool in tools if tool["name"] == "cmu_review")["annotations"]["readOnlyHint"], True)
        self.assertEqual(next(tool for tool in tools if tool["name"] == "cmu_task_start")["inputSchema"]["required"], ["prompt"])

    def test_mcp_initialize_and_tools_list_use_json_rpc_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = CmuMcpAdapter(tmp)

            initialized = adapter.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            listed = adapter.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

            assert initialized is not None
            assert listed is not None
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "central-memory-unit")
            self.assertEqual(initialized["result"]["capabilities"], {"tools": {}})
            self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], [tool["name"] for tool in mcp_tool_definitions()])

    def test_mcp_task_start_silent_skip_creates_no_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            response = call_mcp_tool(
                CmuMcpAdapter(tmp),
                "cmu_task_start",
                {"prompt": "Adjust local style label", "area": "ui", "files": ["ui/label.css"], "risk": "low"},
            )

            self.assertFalse(response["isError"])
            structured = response["structuredContent"]
            self.assertEqual(structured["status"], "silent-skip")
            self.assertIsNone(structured["receipt"])
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_mcp_task_start_matching_practice_returns_action_note_and_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = add_mcp_practice(tmp)

            response = call_mcp_tool(
                CmuMcpAdapter(tmp),
                "cmu_task_start",
                {
                    "prompt": "Build the CMU MCP adapter",
                    "actor": "agent",
                    "area": "cmu",
                    "files": ["cmu/mcp.py"],
                    "workflow": ["agent integration"],
                    "risk": "high",
                },
            )

            structured = response["structuredContent"]
            self.assertFalse(response["isError"])
            self.assertEqual(structured["status"], "action-note")
            self.assertEqual(structured["matched_memory"]["id"], practice.id)
            self.assertEqual(structured["action_note"]["recognized_situation"], practice.title)
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.id, structured["receipt"]["id"])
            self.assertEqual(receipt.source_command, "agent.task-start")

    def test_mcp_after_work_uses_existing_candidate_quality_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = CmuMcpAdapter(tmp)

            saved = call_mcp_tool(
                adapter,
                "cmu_after_work",
                {
                    "situation": "MCP adapters for CMU must delegate to AgentIntegration instead of duplicating memory logic.",
                    "signals": ["new convention"],
                    "outcome": "The adapter exposes MCP tools while preserving the direct agent boundary.",
                    "worked": "Keep MCP protocol code thin and route tool calls through AgentIntegration.invoke.",
                    "failed": "Reimplementing task-start or after-work logic in MCP would bypass safety gates.",
                    "future_use": "Use this pattern for future external host adapters.",
                    "evidence": ["MCP integration tests exercise the adapter through temporary stores."],
                    "liability_score": 4,
                    "scope": {"code": ["cmu/mcp.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                    "confidence": 0.85,
                },
            )

            rejected = call_mcp_tool(
                adapter,
                "cmu_after_work",
                {
                    "situation": "Changed a label.",
                    "future_use": "Probably no future reuse.",
                    "scope": {},
                    "liability_score": 1,
                },
            )

            self.assertEqual(saved["structuredContent"]["status"], "candidate-saved")
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 1)
            self.assertTrue(saved["structuredContent"]["decision"]["memory"]["id"].startswith("mem_"))
            self.assertTrue(rejected["isError"])
            self.assertEqual(rejected["structuredContent"]["status"], "candidate-not-saved")
            self.assertIn("Missing required Candidate Memory fields", rejected["structuredContent"]["decision"]["reason"])

    def test_mcp_link_checkpoint_and_review_return_structured_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = add_mcp_practice(tmp)
            adapter = CmuMcpAdapter(tmp)
            started = call_mcp_tool(
                adapter,
                "cmu_task_start",
                {
                    "prompt": "Build CMU MCP adapter",
                    "actor": "agent",
                    "area": "cmu",
                    "files": ["cmu/mcp.py"],
                    "workflow": ["agent integration"],
                    "risk": "high",
                },
            )["structuredContent"]
            use_id = started["receipt"]["id"]

            linked = call_mcp_tool(
                adapter,
                "cmu_link_checkpoint",
                {
                    "use_id": use_id,
                    "manual_commit": {
                        "hash": "mcp123",
                        "message": "Add CMU MCP adapter",
                        "files": ["cmu/mcp.py", "tests/test_cmu_spine.py"],
                    },
                },
            )
            reviewed = call_mcp_tool(adapter, "cmu_review", {"memory_id": practice.id})

            self.assertFalse(linked["isError"])
            self.assertEqual(linked["structuredContent"]["status"], "checkpoint-linked")
            self.assertEqual(linked["structuredContent"]["decision"]["receipt"]["commit_hash"], "mcp123")
            self.assertFalse(reviewed["isError"])
            self.assertEqual(reviewed["structuredContent"]["status"], "review-ready")
            self.assertEqual(reviewed["structuredContent"]["cards"][0]["memory_id"], practice.id)
            self.assertEqual(reviewed["structuredContent"]["cards"][0]["linked_uses"], 1)

    def test_mcp_invalid_input_returns_structured_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = CmuMcpAdapter(tmp)

            bad_arguments = adapter.call_tool({"name": "cmu_task_start", "arguments": ["not", "an", "object"]})
            missing_required = adapter.call_tool({"name": "cmu_task_start", "arguments": {"risk": "high"}})
            unknown_tool = adapter.call_tool({"name": "cmu_missing_tool", "arguments": {}})

            self.assertTrue(bad_arguments["isError"])
            self.assertEqual(bad_arguments["structuredContent"]["status"], "invalid-request")
            self.assertTrue(missing_required["isError"])
            self.assertEqual(missing_required["structuredContent"]["status"], "invalid-request")
            self.assertTrue(unknown_tool["isError"])
            self.assertEqual(unknown_tool["structuredContent"]["status"], "unknown-tool")

    def test_mcp_store_root_errors_return_structured_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            bad_root = Path(tmp) / "not-a-directory"
            bad_root.write_text("not a CMU root", encoding="utf-8")

            response = call_mcp_tool(CmuMcpAdapter(bad_root), "cmu_review", {})

            self.assertTrue(response["isError"])
            self.assertEqual(response["structuredContent"]["status"], "store-error")
            self.assertIn("CMU store/root error", response["structuredContent"]["error"])

    def test_mcp_tool_call_delegates_to_agent_integration_invoke(self) -> None:
        adapter = CmuMcpAdapter(".")
        calls = []

        class FakeIntegration:
            def invoke(self, tool, arguments):
                calls.append((tool, arguments))
                return {
                    "api_version": AGENT_API_VERSION,
                    "tool": tool,
                    "ok": True,
                    "status": "fake",
                    "arguments": arguments,
                }

        adapter.integration = FakeIntegration()
        response = adapter.call_tool({"name": "cmu_review", "arguments": {"memory_id": "mem_test"}})

        self.assertEqual(calls, [("cmu_review", {"memory_id": "mem_test"})])
        self.assertEqual(response["structuredContent"]["status"], "fake")


class PythonSdkFacadeTests(unittest.TestCase):
    def test_sdk_facade_runs_named_methods_over_agent_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Check SDK memory before runtime integration",
                summary="Runtime integrations should call CMU through a stable SDK facade.",
                signals=["sdk", "runtime integration"],
                scope=MemoryScope(code=["cmu/sdk.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["SDK ergonomics reduce adapter friction without bypassing authority gates."],
                use_this_path="Use the CentralMemoryUnit facade instead of shelling out to CLI commands.",
                avoid_this="Do not make runtimes parse human-readable CMU output.",
                challenge_only_if="The runtime requires a protocol adapter such as MCP instead of in-process Python.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)
            cmu = CentralMemoryUnit(tmp)

            manifest = cmu.tools()
            self.assertEqual(manifest["api_version"], AGENT_API_VERSION)

            started = cmu.task_start(
                "wire CMU SDK runtime integration",
                actor="agent",
                area="cmu",
                files=["cmu/sdk.py"],
                workflow=["agent integration"],
                risk="high",
            )

            self.assertEqual(started["status"], "action-note")
            self.assertEqual(started["matched_memory"]["id"], practice.id)
            use_id = started["receipt"]["id"]

            learned = cmu.after_work(
                situation="Python runtimes need a small CMU SDK facade over the stable tool boundary.",
                signals=["sdk", "runtime integration"],
                worked="Expose named methods that reuse AgentIntegration.",
                failed="Making callers pass raw tool names everywhere is too adapter-shaped.",
                future_use="Use CentralMemoryUnit when wiring Python agent runtimes.",
                evidence=["SDK facade test exercises the named-method runtime loop."],
                liability_score=3,
                scope={"code": ["cmu/sdk.py"], "workflow": ["agent integration"], "actor": ["agent"]},
                confidence=0.8,
            )

            self.assertEqual(learned["status"], "candidate-saved")
            self.assertEqual(len(MemoryStore(tmp).list(type=MemoryType.CANDIDATE)), 1)

            linked = cmu.link_checkpoint(
                use_id,
                manual_commit={
                    "hash": "sdk123",
                    "message": "Add Python SDK facade",
                    "files": ["cmu/sdk.py"],
                },
            )

            self.assertEqual(linked["status"], "checkpoint-linked")
            self.assertEqual(linked["decision"]["receipt"]["outcome_signal"], "committed")

            reviewed = cmu.review(practice.id)

            self.assertEqual(reviewed["status"], "review-ready")
            self.assertEqual(reviewed["cards"][0]["memory_id"], practice.id)
            self.assertEqual(reviewed["cards"][0]["linked_uses"], 1)

    def test_sdk_facade_preserves_silent_skip_behavior(self) -> None:
        with TemporaryDirectory() as tmp:
            response = CentralMemoryUnit(tmp).task_start(
                "adjust local label spacing",
                actor="agent",
                area="ui",
                files=["ui/label.css"],
                risk="low",
            )

            self.assertEqual(response["status"], "silent-skip")
            self.assertIsNone(response["receipt"])
            self.assertEqual(MemoryUseStore(tmp).list(), [])


class TeamAuthorityModelTests(unittest.TestCase):
    def test_team_directory_persists_repo_team_scope_and_reports_memory_coverage(self) -> None:
        with TemporaryDirectory() as tmp:
            record = TeamScopeRecord.create(
                repo="checkout-service",
                team="Release",
                owner="Release owner",
                code=["checkout"],
                workflow=["rollback"],
                environment=["prod"],
                authority_role="owner",
                consequence="high",
            )
            TeamDirectoryStore(tmp).add(record)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Checkout rollback marker check",
                summary="Checkout rollback must inspect release marker state before retrying.",
                scope=MemoryScope(ownership=["Release owner"], code=["checkout"], workflow=["rollback"], environment=["prod"]),
                evidence=["Rollback succeeded after marker cleanup."],
                use_this_path="Inspect release marker before retrying rollback.",
                avoid_this="Do not retry rollback blindly.",
                challenge_only_if="The checkout service no longer uses release markers.",
                liability_score=4,
                confidence=0.9,
                approved_by="Release owner",
            )
            MemoryStore(tmp).add(memory)

            records = TeamDirectoryStore(tmp).list()
            report = team_directory_report(records, MemoryStore(tmp).list())
            rendered = report.render()

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].repo, "checkout-service")
            self.assertIn("CMU Team Scope Directory", rendered)
            self.assertIn("Records With Matching Memory: 1", rendered)
            self.assertIn(memory.id, rendered)
            self.assertIn("missing=none", rendered)

    def test_team_directory_does_not_treat_environment_overlap_as_coverage(self) -> None:
        with TemporaryDirectory() as tmp:
            billing = TeamScopeRecord.create(
                repo="billing-service",
                team="Billing",
                owner="Billing owner",
                code=["billing"],
                workflow=["deployment"],
                environment=["prod"],
                authority_role="owner",
                consequence="high",
            )
            TeamDirectoryStore(tmp).add(billing)
            checkout_memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Checkout rollback marker check",
                summary="Checkout rollback must inspect release marker state before retrying.",
                scope=MemoryScope(ownership=["Release owner"], code=["checkout"], workflow=["rollback"], environment=["prod"]),
                evidence=["Rollback succeeded after marker cleanup."],
                use_this_path="Inspect release marker before retrying rollback.",
                avoid_this="Do not retry rollback blindly.",
                challenge_only_if="The checkout service no longer uses release markers.",
                liability_score=4,
                confidence=0.9,
                approved_by="Release owner",
            )
            MemoryStore(tmp).add(checkout_memory)

            report = team_directory_report(TeamDirectoryStore(tmp).list(), MemoryStore(tmp).list())
            rendered = report.render()

            self.assertIn("Records With Matching Memory: 0", rendered)
            self.assertIn("Records Missing Memory Coverage: 1", rendered)
            self.assertIn("matched=none", rendered)
            self.assertNotIn(checkout_memory.id, rendered)

    def test_cli_team_scope_add_and_report_surfaces_uncovered_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            add_output = StringIO()
            with redirect_stdout(add_output):
                add_exit = main(
                    [
                        "--root",
                        tmp,
                        "team-scope-add",
                        "--repo",
                        "billing-service",
                        "--team",
                        "Billing",
                        "--owner",
                        "Billing owner",
                        "--code",
                        "billing",
                        "--workflow",
                        "deployment",
                        "--authority-role",
                        "owner",
                        "--consequence",
                        "high",
                    ]
                )

            self.assertEqual(add_exit, 0)
            self.assertIn("CMU Team Scope Added", add_output.getvalue())

            report_output = StringIO()
            with redirect_stdout(report_output):
                report_exit = main(["--root", tmp, "team-scope"])

            rendered = report_output.getvalue()
            self.assertEqual(report_exit, 0)
            self.assertIn("CMU Team Scope Directory", rendered)
            self.assertIn("billing-service/Billing", rendered)
            self.assertIn("Records Missing Memory Coverage: 1", rendered)
            self.assertIn("matched=none", rendered)

    def test_authority_assignment_enforces_consequence_permission(self) -> None:
        practice = Memory.create(
            type=MemoryType.PRACTICE,
            title="Verify billing migration ordering",
            summary="Billing migration work must verify rollout ordering.",
            liability_score=5,
            approved_by="Legacy billing owner",
        )

        blocked = set_memory_authority(
            practice,
            owner="Billing team",
            approved_by="Billing contributor",
            approver_role="member",
            consequence="critical",
        )

        self.assertFalse(blocked.applied)
        self.assertIn("requires org or higher", blocked.reason)

        applied = set_memory_authority(
            practice,
            owner="Billing team",
            approved_by="Billing council",
            approver_role="org",
            consequence="critical",
            review_due_at="2030-01-01T00:00:00+00:00",
        )

        self.assertTrue(applied.applied)
        self.assertEqual(practice.authority_owner, "Billing team")
        self.assertEqual(practice.authority_role, "org")
        self.assertEqual(practice.authority_consequence, "critical")
        self.assertIn("Authority approval: Billing council (org)", practice.evidence)

    def test_authority_report_surfaces_legacy_and_expired_review_states(self) -> None:
        legacy = Memory.create(
            type=MemoryType.PRACTICE,
            title="Legacy deployment practice",
            summary="Keep legacy deployment order.",
            approved_by="Release owner",
        )
        expired = Memory.create(
            type=MemoryType.ANCHOR,
            title="Expired credential anchor",
            summary="Credential rotation lock order must be reviewed.",
            approved_by="Security council",
            authority_owner="Security team",
            authority_role="org",
            authority_consequence="critical",
            authority_review_due_at="2020-01-01T00:00:00+00:00",
        )

        report = authority_report([legacy, expired])
        rendered = report.render()

        self.assertIn("CMU Team and Authority Model", rendered)
        self.assertIn("State: legacy approval metadata", rendered)
        self.assertIn("State: review expired", rendered)
        self.assertIn("Expired Reviews: 1", rendered)

    def test_stable_promotion_can_store_full_authority_and_refuses_underpowered_role_without_mutation(self) -> None:
        situation = Memory.create(
            type=MemoryType.SITUATION,
            title="Verify release marker before rollback",
            summary="Rollback retries must verify release marker state.",
            scope=MemoryScope(code=["deploy"], workflow=["rollback"]),
            evidence=["A stale release marker caused rollback retry failure."],
            use_this_path="Inspect the release marker before retrying rollback.",
            challenge_only_if="The rollback no longer reads or writes release marker state.",
            liability_score=4,
            confidence=0.85,
        )

        blocked = promote_memory(
            [situation],
            situation.id,
            MemoryType.PRACTICE,
            approved_by="Release contributor",
            authority_owner="Release team",
            approver_role="member",
            consequence="high",
        )

        self.assertFalse(blocked.promoted)
        self.assertEqual(situation.type, MemoryType.SITUATION)
        self.assertEqual(situation.approved_by, "")

        applied = promote_memory(
            [situation],
            situation.id,
            MemoryType.PRACTICE,
            approved_by="Release owner",
            authority_owner="Release team",
            approver_role="owner",
            consequence="high",
            review_due_at="2030-01-01T00:00:00+00:00",
        )

        self.assertTrue(applied.promoted)
        self.assertEqual(situation.type, MemoryType.PRACTICE)
        self.assertEqual(situation.authority_owner, "Release team")
        self.assertEqual(situation.authority_role, "owner")
        self.assertEqual(situation.authority_consequence, "high")

    def test_cli_authority_set_persists_metadata_and_governance_blocks_expired_review(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Inspect rollback markers",
                summary="Inspect rollback markers before deployment retry.",
                approved_by="Legacy release owner",
            )
            store.add(practice)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "authority-set",
                        practice.id,
                        "--owner",
                        "Release team",
                        "--approved-by",
                        "Release owner",
                        "--approver-role",
                        "owner",
                        "--consequence",
                        "high",
                        "--review-due",
                        "2020-01-01T00:00:00+00:00",
                    ]
                )

            self.assertEqual(exit_code, 0)
            loaded = MemoryStore(tmp).list(type=MemoryType.PRACTICE)[0]
            self.assertEqual(loaded.authority_owner, "Release team")
            governance_output = StringIO()
            with redirect_stdout(governance_output):
                self.assertEqual(main(["--root", tmp, "governance", "--memory", practice.id]), 0)
            self.assertIn("State: blocked: review expired", governance_output.getvalue())


class MemoryQualityDecayTests(unittest.TestCase):
    def test_quality_report_marks_dragging_expired_stable_memory_decay_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Retry deployment without checking marker",
                summary="An obsolete retry practice that now causes drag.",
                scope=MemoryScope(code=["deploy"]),
                evidence=["Old incident note."],
                confidence=0.45,
                approved_by="Release owner",
                authority_owner="Release team",
                authority_role="owner",
                authority_consequence="high",
                authority_review_due_at="2020-01-01T00:00:00+00:00",
            )
            MemoryStore(tmp).add(practice)
            add_drag_receipts(tmp, practice, count=3)

            card = quality_card(practice, MemoryUseStore(tmp).list())
            rendered = quality_report([practice], MemoryUseStore(tmp).list()).render()

            self.assertEqual(card.state, "decay-ready")
            self.assertIn("authority review expired", card.signals)
            self.assertIn("3 drag signal(s)", card.signals)
            self.assertIn("Decay Ready: 1", rendered)

    def test_stable_decay_requires_authority_then_demotes_to_situation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Blindly retry deployment rollback",
                summary="This old stable practice now repeatedly creates unrelated changes.",
                scope=MemoryScope(code=["deploy"]),
                evidence=["Old rollback guidance."],
                confidence=0.45,
                approved_by="Release owner",
                authority_owner="Release team",
                authority_role="owner",
                authority_consequence="high",
            )
            store.add(practice)
            add_drag_receipts(tmp, practice, count=3)
            receipts = MemoryUseStore(tmp).list()

            blocked = apply_decay_action(
                store.list(),
                receipts,
                practice.id,
                action="demote",
                reason="Repeated no-overlap checkpoint evidence shows this should stop guiding work as stable memory.",
            )

            self.assertFalse(blocked.applied)
            self.assertIn("stable-memory decay requires explicit approval", blocked.reason)

            applied = apply_decay_action(
                store.list(),
                receipts,
                practice.id,
                action="demote",
                reason="Repeated no-overlap checkpoint evidence shows this should stop guiding work as stable memory.",
                approved_by="Release owner",
                approver_role="owner",
            )

            self.assertTrue(applied.applied)
            assert applied.memory is not None
            self.assertEqual(applied.memory.type, MemoryType.SITUATION)
            self.assertEqual(applied.memory.approved_by, "")
            self.assertIn("Decay action demote:", applied.memory.evidence[-3])

    def test_cli_quality_and_decay_apply_persist_controlled_weaken(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Old local dependency workaround",
                summary="This workaround has repeated unrelated checkpoint evidence.",
                scope=MemoryScope(code=["tools"]),
                evidence=["Old workaround note."],
                confidence=0.4,
            )
            store.add(situation)
            add_drag_receipts(tmp, situation, count=2)

            quality_output = StringIO()
            with redirect_stdout(quality_output):
                self.assertEqual(main(["--root", tmp, "quality", "--memory", situation.id]), 0)
            self.assertIn("CMU Memory Quality and Decay", quality_output.getvalue())
            self.assertIn("State: decay-ready", quality_output.getvalue())

            decay_output = StringIO()
            with redirect_stdout(decay_output):
                self.assertEqual(
                    main(
                        [
                            "--root",
                            tmp,
                            "decay-apply",
                            situation.id,
                            "--action",
                            "weaken",
                            "--reason",
                            "Two unrelated linked checkpoints show the workaround is dragging retrieval.",
                        ]
                    ),
                    0,
                )
            self.assertIn("CMU Decay Action Applied", decay_output.getvalue())
            loaded = MemoryStore(tmp).list(type=MemoryType.SITUATION)[0]
            self.assertEqual(loaded.confidence, 0.25)


class ImportExportPortabilityTests(unittest.TestCase):
    def test_export_bundle_preserves_memory_authority_relationships_and_receipts(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Review rollback marker before retry",
                summary="Deployment retries must inspect the rollback marker first.",
                scope=MemoryScope(code=["deploy"], workflow=["release"]),
                evidence=["Incident 42 confirmed marker drift."],
                use_this_path="Inspect marker, then retry.",
                approved_by="Release owner",
                authority_owner="Release team",
                authority_role="owner",
                authority_consequence="high",
            )
            exception = Memory.create(
                type=MemoryType.EXCEPTION,
                title="Skip marker for docs-only deploy",
                summary="Docs-only deploy does not need rollback marker inspection.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.EXCEPTION_TO,
                        target_id=practice.id,
                        reason="Docs deploy has no runtime rollback marker.",
                    )
                ],
            )
            store = MemoryStore(tmp)
            store.add(practice)
            store.add(exception)
            add_strong_receipts(tmp, practice, count=1)

            bundle = export_bundle_from_root(tmp)

            self.assertEqual(bundle.schema, PORTABLE_BUNDLE_VERSION)
            self.assertEqual(bundle.integrity["memory_count"], 2)
            self.assertEqual(bundle.integrity["use_count"], 1)
            exported = {item["id"]: item for item in bundle.memories}
            self.assertEqual(exported[practice.id]["authority_owner"], "Release team")
            self.assertEqual(exported[exception.id]["relationships"][0]["target_id"], practice.id)
            self.assertEqual(bundle.uses[0]["memory_id"], practice.id)
            self.assertFalse(bundle.warnings)

    def test_import_bundle_is_dry_run_until_apply_then_restores_records(self) -> None:
        with TemporaryDirectory() as source, TemporaryDirectory() as target:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Check deploy receipt before follow-up",
                summary="Follow-up deploy work should inspect the previous receipt.",
                evidence=["Two deploy tasks used this successfully."],
                approved_by="Release owner",
                authority_owner="Release team",
                authority_role="owner",
                authority_consequence="high",
            )
            MemoryStore(source).add(practice)
            add_strong_receipts(source, practice, count=1)
            bundle = export_bundle_from_root(source).to_dict()

            dry_run = import_portable_bundle(target, bundle)
            self.assertFalse(dry_run.applied)
            self.assertEqual(dry_run.memory_adds, [practice.id])
            self.assertEqual(len(MemoryStore(target).list()), 0)

            applied = import_portable_bundle(target, bundle, apply=True)
            self.assertTrue(applied.applied)
            [loaded] = MemoryStore(target).list(type=MemoryType.PRACTICE)
            self.assertEqual(loaded.id, practice.id)
            self.assertEqual(loaded.authority_role, "owner")
            self.assertEqual(len(MemoryUseStore(target).list()), 1)

    def test_import_blocks_different_existing_record_unless_update_existing_is_explicit(self) -> None:
        with TemporaryDirectory() as source, TemporaryDirectory() as target:
            original = Memory.create(
                type=MemoryType.SITUATION,
                title="Original import source",
                summary="The source version should win only with update-existing.",
            )
            MemoryStore(source).add(original)
            changed = Memory.from_dict(original.to_dict())
            changed.summary = "The target version is different."
            MemoryStore(target).add(changed)
            bundle = export_bundle_from_root(source).to_dict()

            blocked = import_portable_bundle(target, bundle, apply=True)
            self.assertFalse(blocked.applied)
            self.assertIn(f"memory {original.id} already exists with different content", blocked.conflicts)
            self.assertEqual(MemoryStore(target).list()[0].summary, "The target version is different.")

            updated = import_portable_bundle(target, bundle, apply=True, update_existing=True)
            self.assertTrue(updated.applied)
            self.assertEqual(MemoryStore(target).list()[0].summary, original.summary)

    def test_cli_portable_export_and_import_apply_round_trip(self) -> None:
        with TemporaryDirectory() as source, TemporaryDirectory() as target:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Portable CLI round trip",
                summary="CLI export/import should move this record.",
            )
            MemoryStore(source).add(memory)
            bundle_path = Path(source) / "cmu-portable.json"

            export_output = StringIO()
            with redirect_stdout(export_output):
                self.assertEqual(main(["--root", source, "portable-export", "--output", str(bundle_path)]), 0)
            self.assertIn("CMU Portable Export Written", export_output.getvalue())

            preview_output = StringIO()
            with redirect_stdout(preview_output):
                self.assertEqual(main(["--root", target, "portable-import", str(bundle_path)]), 0)
            self.assertIn("Dry Run: pass --apply", preview_output.getvalue())
            self.assertEqual(MemoryStore(target).list(), [])

            apply_output = StringIO()
            with redirect_stdout(apply_output):
                self.assertEqual(main(["--root", target, "portable-import", str(bundle_path), "--apply"]), 0)
            self.assertIn("Applied: yes", apply_output.getvalue())
            self.assertEqual(MemoryStore(target).list()[0].id, memory.id)

    def test_portable_validation_passes_exported_bundle_and_cli_returns_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Portable validation fixture",
                summary="A clean exported bundle should validate.",
            )
            MemoryStore(tmp).add(memory)
            bundle = export_bundle_from_root(tmp)
            report = validate_portable_bundle(bundle.to_dict())

            self.assertTrue(report.valid)
            self.assertEqual(report.memory_count, 1)
            self.assertEqual(report.use_count, 0)

            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(bundle.render_json(), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-validate", str(bundle_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Status: pass", output.getvalue())

            bom_path = Path(tmp) / "bundle-bom.json"
            bom_path.write_text(bundle.render_json(), encoding="utf-8-sig")
            bom_output = StringIO()
            with redirect_stdout(bom_output):
                bom_exit = main(["--root", tmp, "portable-validate", str(bom_path)])

            self.assertEqual(bom_exit, 0)
            self.assertIn("Status: pass", bom_output.getvalue())

    def test_portable_validation_fails_tampered_integrity_and_duplicate_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Portable tamper fixture",
                summary="Tampering should fail validation.",
            )
            MemoryStore(tmp).add(memory)
            bundle = export_bundle_from_root(tmp).to_dict()
            bundle["integrity"]["memory_count"] = 99
            bundle["integrity"]["contents_sha256"] = "bad-digest"
            bundle["contents"]["memories"].append(dict(bundle["contents"]["memories"][0]))
            report = validate_portable_bundle(bundle)

            self.assertFalse(report.valid)
            self.assertIn("integrity.memory_count expected 99; actual 2", report.errors)
            self.assertIn("integrity.contents_sha256 mismatch", report.errors)
            self.assertIn(f"duplicate memory id: {memory.id}", report.errors)

            bundle_path = Path(tmp) / "tampered.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-validate", str(bundle_path)])

            self.assertEqual(exit_code, 1)
            self.assertIn("Status: fail", output.getvalue())

    def test_portable_compat_passes_valid_invalid_and_future_schema_fixtures(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp) / "fixtures"
            fixture_dir.mkdir()
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Portable compatibility fixture",
                summary="Compatibility fixtures should validate exported bundles across schema expectations.",
                evidence=["Fixture exported from the real store."],
                approved_by="Portability owner",
            )
            MemoryStore(tmp).add(memory)
            bundle = export_bundle_from_root(tmp).to_dict()
            (fixture_dir / "valid-current-v1.json").write_text(json.dumps(bundle), encoding="utf-8")
            invalid = json.loads(json.dumps(bundle))
            invalid["integrity"]["memory_count"] = 99
            (fixture_dir / "invalid-bad-count.json").write_text(json.dumps(invalid), encoding="utf-8")
            future = json.loads(json.dumps(bundle))
            future["schema"] = "cmu-portable-bundle/v2"
            (fixture_dir / "future-v2.json").write_text(json.dumps(future), encoding="utf-8")

            report = portable_compat_report(fixture_dir)
            rendered = report.render()

            self.assertTrue(report.passed, rendered)
            self.assertIn("CMU Portable Compatibility Fixtures", rendered)
            self.assertIn("valid-current-v1.json", rendered)
            self.assertIn("invalid-bad-count.json", rendered)
            self.assertIn("future-v2.json", rendered)
            self.assertIn("future schema failed safely as unsupported", rendered)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-compat", "--fixture-dir", str(fixture_dir)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Status: pass", output.getvalue())

    def test_portable_compat_fails_when_expected_valid_fixture_is_tampered(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_dir = Path(tmp) / "fixtures"
            fixture_dir.mkdir()
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Portable compatibility failure fixture",
                summary="A valid-named fixture should fail when tampered.",
            )
            MemoryStore(tmp).add(memory)
            bundle = export_bundle_from_root(tmp).to_dict()
            bundle["integrity"]["contents_sha256"] = "bad-digest"
            (fixture_dir / "valid-tampered.json").write_text(json.dumps(bundle), encoding="utf-8")

            report = portable_compat_report(fixture_dir)
            rendered = report.render()

            self.assertFalse(report.passed)
            self.assertIn("Status: fail", rendered)
            self.assertIn("integrity.contents_sha256 mismatch", rendered)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-compat", "--fixture-dir", str(fixture_dir)])

            self.assertEqual(exit_code, 1)
            self.assertIn("Status: fail", output.getvalue())


class QuickstartDemoTests(unittest.TestCase):
    def test_dist_check_builds_installs_and_validates_installed_cli_and_mcp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as tmp:
            report = dist_check(root, python_executable=sys.executable, work_dir=Path(tmp))
            rendered = report.render()

            self.assertTrue(report.passed, rendered)
            self.assertIn("CMU Distribution Check", rendered)
            self.assertIn("Status: pass", rendered)
            self.assertIn("installed cmu console script", rendered)
            self.assertIn("installed module entrypoint", rendered)
            self.assertIn("installed install-check", rendered)
            self.assertIn("installed demo-walkthrough", rendered)
            self.assertIn("installed MCP discovery", rendered)
            self.assertFalse(Path(report.work_dir).exists())

    def test_demo_walkthrough_dry_run_composes_real_install_setup_and_quickstart_surfaces(self) -> None:
        root = Path(__file__).resolve().parents[1]
        before_memories = (root / ".cmu" / "memories.json").read_text(encoding="utf-8")
        before_uses = (root / ".cmu" / "uses.json").read_text(encoding="utf-8")

        report = demo_walkthrough(root)
        rendered = report.render()

        self.assertTrue(report.passed, rendered)
        self.assertFalse(report.applied)
        self.assertTrue(report.install_report.passed)
        self.assertEqual(report.setup_report.status.pyproject_scripts, REQUIRED_SCRIPTS)
        self.assertFalse(report.quickstart_report.applied)
        self.assertEqual(report.quickstart_report.reason, "dry run")
        self.assertIn("CMU Demo Walkthrough", rendered)
        self.assertIn("Validate adoption surface", rendered)
        self.assertIn("Inspect host setup", rendered)
        self.assertIn("Run memory proof loop", rendered)
        self.assertIn("Rehearse real work-cycle handoff", rendered)
        self.assertIn("cmu install-check", rendered)
        self.assertIn("cmu setup-guide --host all", rendered)
        self.assertIn("cmu quickstart-demo", rendered)
        self.assertEqual(before_memories, (root / ".cmu" / "memories.json").read_text(encoding="utf-8"))
        self.assertEqual(before_uses, (root / ".cmu" / "uses.json").read_text(encoding="utf-8"))

    def test_demo_walkthrough_cli_dry_run_returns_zero_without_mutating(self) -> None:
        root = Path(__file__).resolve().parents[1]
        before_memories = (root / ".cmu" / "memories.json").read_text(encoding="utf-8")
        before_uses = (root / ".cmu" / "uses.json").read_text(encoding="utf-8")

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["--root", str(root), "demo-walkthrough"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Status: pass", output.getvalue())
        self.assertIn("Mode: read-only walkthrough", output.getvalue())
        self.assertEqual(before_memories, (root / ".cmu" / "memories.json").read_text(encoding="utf-8"))
        self.assertEqual(before_uses, (root / ".cmu" / "uses.json").read_text(encoding="utf-8"))

    def test_demo_walkthrough_apply_uses_real_quickstart_git_receipt_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(tmp)
            write_install_ready_fixture(root)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "demo-walkthrough", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Applied: yes", rendered)
            self.assertIn("Demo Git Checkpoint", rendered)
            [memory] = MemoryStore(tmp).list(type=MemoryType.PRACTICE)
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.memory_id, memory.id)
            self.assertEqual(receipt.outcome_signal, "committed")
            self.assertTrue(receipt.commit_hash)
            self.assertEqual(receipt.commit_files, ["quickstart_demo/rollback_notes.txt"])

    def test_install_check_passes_real_checkout_against_live_adoption_surfaces(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = install_check(root)
        rendered = report.render()

        self.assertTrue(report.passed, rendered)
        self.assertIn("CMU Install Check", rendered)
        self.assertIn("Status: pass", rendered)
        self.assertIn("README quickstart commands", rendered)
        self.assertIn("console scripts", rendered)
        self.assertIn("SDK import", rendered)
        self.assertIn("module entrypoint", rendered)
        self.assertIn("MCP schema", rendered)
        self.assertEqual(set(setup_guide(root).status.pyproject_scripts.items()), set(REQUIRED_SCRIPTS.items()))
        for command in REQUIRED_README_COMMANDS:
            self.assertIn(command, (root / "README.md").read_text(encoding="utf-8"))
        for tool in mcp_tool_definitions():
            self.assertIn(tool["name"], rendered)

    def test_install_check_cli_is_read_only_and_returns_zero_for_real_checkout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        before_memories = (root / ".cmu" / "memories.json").read_text(encoding="utf-8")
        before_uses = (root / ".cmu" / "uses.json").read_text(encoding="utf-8")

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["--root", str(root), "install-check"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Status: pass", output.getvalue())
        self.assertEqual(before_memories, (root / ".cmu" / "memories.json").read_text(encoding="utf-8"))
        self.assertEqual(before_uses, (root / ".cmu" / "uses.json").read_text(encoding="utf-8"))

    def test_install_check_fails_incomplete_checkout_with_specific_reasons(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("cmu init\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                "\n".join(
                    [
                        "[project]",
                        "name = \"broken\"",
                        "readme = \"WRONG.md\"",
                        "[project.scripts]",
                        "cmu = \"cmu.cli:main\"",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            report = install_check(root)
            rendered = report.render()

            self.assertFalse(report.passed)
            self.assertIn("Status: fail", rendered)
            self.assertIn("missing:", rendered)
            self.assertIn("project.readme is 'WRONG.md'", rendered)
            self.assertIn("pyproject={'cmu': 'cmu.cli:main'}", rendered)

    def test_readme_quickstart_documents_real_package_and_host_surfaces(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        report = setup_guide(root, host="all")

        self.assertIn("python -m pip install -e .", readme)
        self.assertIn("cmu init", readme)
        self.assertIn("cmu readiness", readme)
        self.assertIn("cmu quickstart-demo", readme)
        self.assertIn("cmu quickstart-demo --apply", readme)
        self.assertIn("cmu setup-guide --host all", readme)
        self.assertIn("python -m cmu setup-guide --host all", readme)
        self.assertIn("cmu-mcp", readme)
        self.assertIn(MCP_SERVER_NAME, readme)
        for tool in mcp_tool_definitions():
            self.assertIn(tool["name"], readme)
        for script_name in report.status.pyproject_scripts:
            self.assertIn(script_name, readme)

    def test_pyproject_package_metadata_matches_setup_guide_entrypoints(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        report = setup_guide(root, host="all")

        self.assertEqual(pyproject["project"]["readme"], "README.md")
        self.assertEqual(pyproject["project"]["scripts"], report.status.pyproject_scripts)
        self.assertEqual(pyproject["project"]["scripts"]["cmu"], "cmu.cli:main")
        self.assertEqual(pyproject["project"]["scripts"]["cmu-mcp"], "cmu.mcp:main")
        self.assertEqual(pyproject["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertIn("setuptools>=68", pyproject["build-system"]["requires"])
        self.assertEqual(pyproject["tool"]["setuptools"]["packages"]["find"]["include"], ["cmu*"])

    def test_setup_guide_local_development_fallback_matches_readme(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        rendered = setup_guide(root, host="mcp").render()

        self.assertIn("python -m cmu --root <project-root> mcp", rendered)
        self.assertIn('"command": "python"', readme)
        self.assertIn('"args": ["-m", "cmu", "--root", "<project-root>", "mcp"]', readme)

    def test_setup_guide_reports_real_host_tools_and_project_state_without_mutating(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-created"
            report = setup_guide(root, host="mcp")
            rendered = report.render()

            self.assertFalse(report.status.initialized)
            self.assertFalse(report.status.git_repository)
            self.assertEqual(report.agent_tools, [tool["name"] for tool in AgentIntegration(root).manifest()["tools"]])
            self.assertEqual(report.mcp_tools, [tool["name"] for tool in mcp_tool_definitions()])
            self.assertIn("CMU Setup Guide", rendered)
            self.assertIn("Host: mcp", rendered)
            self.assertIn("CMU Store Initialized: no", rendered)
            self.assertIn("cmu-mcp", rendered)
            self.assertIn("cmu_task_start", rendered)
            self.assertFalse(root.exists())

    def test_setup_guide_cli_uses_real_store_git_and_pyproject_state(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            MemoryStore(tmp).init()
            MemoryUseStore(tmp).init()
            (Path(tmp) / "pyproject.toml").write_text(
                "\n".join(
                    [
                        "[project]",
                        "name = \"fixture\"",
                        "[project.scripts]",
                        "cmu = \"cmu.cli:main\"",
                        "cmu-mcp = \"cmu.mcp:main\"",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            before_memories = json.loads((Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8"))
            before_uses = json.loads((Path(tmp) / ".cmu" / "uses.json").read_text(encoding="utf-8"))
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "setup-guide", "--host", "all"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Store Initialized: yes", rendered)
            self.assertIn("Git Repository: yes", rendered)
            self.assertIn("Quickstart Apply Ready: yes", rendered)
            self.assertIn("Project Scripts: cmu=cmu.cli:main, cmu-mcp=cmu.mcp:main", rendered)
            self.assertIn("Python SDK Setup", rendered)
            self.assertIn("MCP Host Setup", rendered)
            self.assertIn("Codex MCP Setup", rendered)
            self.assertEqual(before_memories, json.loads((Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8")))
            self.assertEqual(before_uses, json.loads((Path(tmp) / ".cmu" / "uses.json").read_text(encoding="utf-8")))

    def test_cli_quickstart_demo_dry_run_explains_proof_loop_without_mutating(self) -> None:
        with TemporaryDirectory() as tmp:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "quickstart-demo"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Quickstart Demo", rendered)
            self.assertIn("Applied: no", rendered)
            self.assertIn("seed one scoped Practice memory", rendered)
            self.assertEqual(MemoryStore(tmp).list(), [])
            self.assertEqual(MemoryUseStore(tmp).list(), [])

    def test_cli_quickstart_demo_apply_creates_commit_linked_receipt_and_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "quickstart-demo", "--apply"])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Applied: yes", rendered)
            self.assertIn("demo checkpoint committed and linked", rendered)
            self.assertIn("CMU Memory Use Summary", rendered)
            self.assertIn("Committed: 1", rendered)
            [memory] = MemoryStore(tmp).list(type=MemoryType.PRACTICE)
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.memory_id, memory.id)
            self.assertEqual(receipt.outcome_signal, "committed")
            self.assertTrue(receipt.commit_hash)
            self.assertEqual(receipt.commit_files, ["quickstart_demo/rollback_notes.txt"])


def init_git_repo(root: str) -> None:
    run_git_test(root, ["init"])
    run_git_test(root, ["config", "user.email", "cmu@example.test"])
    run_git_test(root, ["config", "user.name", "CMU Test"])


def write_install_ready_fixture(root: Path) -> None:
    readme_lines = [
        "# Fixture README",
        MCP_SERVER_NAME,
        *REQUIRED_README_COMMANDS,
        *(tool["name"] for tool in mcp_tool_definitions()),
        "",
    ]
    (root / "README.md").write_text("\n".join(readme_lines), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                "name = \"fixture\"",
                "readme = \"README.md\"",
                "[project.scripts]",
                "cmu = \"cmu.cli:main\"",
                "cmu-mcp = \"cmu.mcp:main\"",
                "[build-system]",
                "requires = [\"setuptools>=68\"]",
                "build-backend = \"setuptools.build_meta\"",
                "[tool.setuptools.packages.find]",
                "include = [\"cmu*\"]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def call_mcp_tool(adapter: CmuMcpAdapter, name: str, arguments: dict | list | None) -> dict:
    response = adapter.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return response["result"]


def add_mcp_practice(root: str) -> Memory:
    practice = Memory.create(
        type=MemoryType.PRACTICE,
        title="Use AgentIntegration for MCP adapters",
        summary="CMU protocol adapters should wrap the stable AgentIntegration boundary.",
        signals=["mcp", "agent integration", "adapter"],
        scope=MemoryScope(code=["cmu/mcp.py"], workflow=["agent integration"], actor=["agent"]),
        evidence=["The direct agent boundary already enforces trigger, retrieval, receipt, and Candidate gates."],
        use_this_path="Expose protocol tools, then delegate each call to AgentIntegration.invoke.",
        avoid_this="Do not rebuild CMU task-start, checkpoint, review, or Candidate logic inside MCP.",
        challenge_only_if="A future MCP SDK provides the same delegation without changing CMU behavior.",
        liability_score=4,
        confidence=0.9,
        approved_by="CMU core owner",
    )
    MemoryStore(root).add(practice)
    return practice


def add_drag_receipts(root: str, memory: Memory, *, count: int) -> None:
    for index in range(count):
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
            match=type("MatchStub", (), {"score": 4.2})(),
        )
        receipt.commit_hash = f"drag{index}"
        receipt.commit_message = "Fix unrelated deploy issue"
        receipt.commit_files = ["ui/settings.css"]
        receipt.outcome_signal = "committed_low_confidence"
        receipt.link_confidence = 0.25
        receipt.flags = ["no_file_overlap"]
        MemoryUseStore(root).add(receipt)

class ProductHardeningWorkflowTests(unittest.TestCase):
    def test_team_review_handoff_surfaces_owner_and_authority_next_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="High-risk deploys need owner approval",
                summary="Stable deploy memory should not guide high-risk work without authority metadata.",
                scope=MemoryScope(ownership=["Release team"], code=["deploy"], workflow=["release"]),
                liability_score=5,
                approved_by="Release owner",
            )
            store.add(memory)
            TeamDirectoryStore(tmp).add(
                TeamScopeRecord.create(
                    repo="checkout",
                    team="Release",
                    owner="Release team",
                    code=["checkout"],
                )
            )

            report = team_review_handoffs(store.list(), TeamDirectoryStore(tmp).list())
            rendered = report.render()

            self.assertIn("CMU Team Review Handoffs", rendered)
            self.assertIn("stable-authority-handoff", rendered)
            self.assertIn(memory.id, rendered)
            self.assertIn("team-scope-metadata", rendered)
            self.assertIn("team-scope-coverage", rendered)
            self.assertIn("cmu authority-set", rendered)
            self.assertEqual(len(MemoryStore(tmp).list()), 1)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "team-review-handoff"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Team Review Handoffs", output.getvalue())

    def test_evidence_session_records_real_monitor_summary_and_applies_clean_link(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/deploy.py", "deploy = true\n", "Fix billing deploy")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Do migration before deploy",
                summary="Deploys should respect database migration order.",
                signals=["billing", "deploy"],
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            report = run_evidence_session(
                tmp,
                MemoryStore(tmp).list(),
                MemoryUseStore(tmp).list(),
                apply=True,
                record=True,
            )

            self.assertTrue(report.ok, report.render())
            self.assertTrue(report.recorded)
            self.assertEqual(report.record.linked, 1)
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.commit_hash, metadata.commit_hash)
            session_file = Path(tmp) / ".cmu" / "evidence_sessions.json"
            self.assertTrue(session_file.exists())
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
            self.assertEqual(session_data["sessions"][0]["linked"], 1)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-session", "--record"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Evidence Session", output.getvalue())

    def test_evidence_watch_runs_bounded_cycles_and_records_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            write_and_commit(tmp, "billing/watch.py", "watch = true\n", "Fix billing watch")
            metadata = inspect_git_commit(tmp, "HEAD")
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Watch billing evidence after commits",
                summary="Evidence watch should link clean receipts after matching commits.",
                signals=["billing", "watch"],
                scope=MemoryScope(code=["billing"], workflow=["deployment"], actor=["agent"]),
                liability_score=5,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Fix billing watch", actor="agent", area="billing", files=["billing/watch.py"], risk="high"),
                match=type("MatchStub", (), {"score": 4.2})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=30)
            MemoryUseStore(tmp).add(receipt)

            report = run_evidence_watch(
                tmp,
                MemoryStore(tmp).list(),
                MemoryUseStore(tmp).list(),
                cycles=2,
                apply=True,
                record=True,
            )

            self.assertTrue(report.ok, report.render())
            self.assertEqual(len(report.cycles), 2)
            self.assertEqual(report.cycles[0].linked, 1)
            self.assertEqual(report.cycles[1].linked, 0)
            [linked] = MemoryUseStore(tmp).list()
            self.assertEqual(linked.commit_hash, metadata.commit_hash)
            session_data = json.loads((Path(tmp) / ".cmu" / "evidence_sessions.json").read_text(encoding="utf-8"))
            self.assertEqual(len(session_data["sessions"]), 2)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-watch", "--cycles", "1", "--record"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Evidence Watch", output.getvalue())

    def test_reminder_delivery_writes_jsonl_outbox_only_with_apply(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Expired checkout authority",
                summary="Checkout Practice needs renewed authority.",
                approved_by="Checkout owner",
                authority_owner="Checkout team",
                authority_role="owner",
                authority_consequence="high",
                authority_review_due_at="2026-01-01T00:00:00+00:00",
            )
            MemoryStore(tmp).add(memory)
            reminders = review_reminders(MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), days=30)
            outbox = Path(tmp) / ".cmu" / "outbox.jsonl"

            preview = deliver_reminders_to_outbox(reminders, root=tmp, outbox=outbox, apply=False)
            self.assertFalse(outbox.exists())
            self.assertGreaterEqual(preview.urgent_count, 1)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "reminder-delivery", "--outbox", str(outbox), "--apply"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertTrue(outbox.exists())
            [line] = outbox.read_text(encoding="utf-8").strip().splitlines()
            event = json.loads(line)
            self.assertEqual(event["schema"], "cmu-reminder-delivery/v1")
            self.assertEqual(event["payload"]["schema"], "cmu-review-reminders/v1")
            self.assertGreaterEqual(event["payload"]["summary"]["total"], 1)

    def test_team_review_action_applies_authority_and_team_metadata_handoffs(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Authority handoff should have an apply path",
                summary="Stable memory authority handoff should be explicit and controlled.",
                scope=MemoryScope(ownership=["Release team"], code=["deploy"]),
                liability_score=4,
                approved_by="legacy release owner",
            )
            MemoryStore(tmp).add(memory)
            team = TeamScopeRecord.create(repo="checkout", team="Release", owner="", code=["checkout"])
            TeamDirectoryStore(tmp).add(team)

            authority = apply_team_review_action(
                tmp,
                memory.id,
                action="authority",
                owner="Release team",
                approved_by="Release owner",
                approver_role="owner",
                consequence="high",
                review_due="2026-12-31T00:00:00+00:00",
            )
            self.assertTrue(authority.applied, authority.render())
            loaded = MemoryStore(tmp).list()[0]
            self.assertEqual(loaded.authority_owner, "Release team")
            self.assertEqual(loaded.authority_role, "owner")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "team-review-action",
                        team.id,
                        "--action",
                        "team-metadata",
                        "--owner",
                        "Release team",
                        "--approver-role",
                        "owner",
                        "--consequence",
                        "high",
                    ]
                )
            self.assertEqual(exit_code, 0, output.getvalue())
            [updated_team] = TeamDirectoryStore(tmp).list()
            self.assertEqual(updated_team.owner, "Release team")
            self.assertEqual(updated_team.authority_role, "owner")
            self.assertIn("CMU Team Review Action Applied", output.getvalue())

    def test_team_review_action_applies_challenge_strengthen_retire_split_and_narrow_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Controlled handoff outcomes need stable guardrails",
                summary="Owner/team review actions should mutate stable memory only through explicit controlled outcomes.",
                signals=["owner handoff", "stable review"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["Review queue surfaced a stable-memory owner action."],
                use_this_path="Apply owner/team outcomes through CMU controlled actions.",
                avoid_this="Do not hand-edit stable memory JSON for review outcomes.",
                challenge_only_if="A lower-level explicit command already captures the same approved outcome.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            store.add(memory)

            challenge = apply_team_review_action(
                tmp,
                memory.id,
                action="challenge",
                mismatch="The stable guidance might be too broad for tiny local edits.",
                benefit="Review whether tiny edits should bypass this stable guidance.",
                risk="Too much bypassing would hide consequential review moments.",
                rollback="Keep the stable guidance for structural CMU work.",
                challenged_by="release owner",
                evidence=["Tiny local edits produced repeated review noise."],
            )
            self.assertTrue(challenge.applied, challenge.render())
            self.assertIsNotNone(challenge.challenge_memory)
            assert challenge.challenge_memory is not None
            candidates = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].id, challenge.challenge_memory.id)

            strengthened = apply_team_review_action(
                tmp,
                challenge.challenge_memory.id,
                action="strengthen",
                approved_by="CMU core owner",
            )
            self.assertTrue(strengthened.applied, strengthened.render())
            loaded = next(item for item in MemoryStore(tmp).list() if item.id == memory.id)
            self.assertIn(f"Challenge reviewed and precedent strengthened: {challenge.challenge_memory.id}", loaded.evidence)
            self.assertEqual(
                MemoryStore(tmp).list(type=MemoryType.CANDIDATE, status=MemoryStatus.RETIRED)[0].id,
                challenge.challenge_memory.id,
            )

            retire_challenge = apply_team_review_action(
                tmp,
                memory.id,
                action="challenge",
                mismatch="A retired path is needed for an obsolete workflow.",
                benefit="Stop surfacing obsolete stable guidance.",
                risk="Retiring without evidence could remove useful guidance.",
                rollback="Restore from the retired memory evidence if the workflow returns.",
                evidence=["The workflow was removed from active development."],
            )
            self.assertTrue(retire_challenge.applied, retire_challenge.render())
            assert retire_challenge.challenge_memory is not None
            retired = apply_team_review_action(
                tmp,
                retire_challenge.challenge_memory.id,
                action="retire",
                approved_by="CMU core owner",
                retirement_reason="The workflow has been removed from active CMU development.",
                evidence=["Owner review confirmed the workflow is obsolete."],
            )
            self.assertTrue(retired.applied, retired.render())
            retired_memory = next(item for item in MemoryStore(tmp).list(status=MemoryStatus.RETIRED) if item.id == memory.id)
            self.assertEqual(retired_memory.id, memory.id)
            self.assertIn("Retirement reason: The workflow has been removed from active CMU development.", retired_memory.evidence)

            split_base = Memory.create(
                type=MemoryType.PRACTICE,
                title="Review handoff split base",
                summary="Broad review handoff guidance sometimes needs a split stable memory.",
                signals=["handoff split"],
                scope=MemoryScope(code=["cmu"], workflow=["implementation"], actor=["agent"]),
                evidence=["A split review case appeared in the owner queue."],
                use_this_path="Keep the general owner handoff path.",
                avoid_this="Do not use it for adapter-specific review work.",
                challenge_only_if="The work is adapter-specific and needs narrower handling.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(split_base)
            split_challenge = apply_team_review_action(
                tmp,
                split_base.id,
                action="challenge",
                mismatch="Adapter-specific work needs separate review instructions.",
                benefit="Create a split practice for adapter review.",
                risk="Keeping only one broad practice could over-apply guidance.",
                rollback="Use the original practice for non-adapter work.",
                evidence=["Adapter work has distinct owner boundaries."],
            )
            self.assertTrue(split_challenge.applied, split_challenge.render())
            assert split_challenge.challenge_memory is not None

            split_output = StringIO()
            with redirect_stdout(split_output):
                split_exit = main(
                    [
                        "--root",
                        tmp,
                        "team-review-action",
                        split_challenge.challenge_memory.id,
                        "--action",
                        "split",
                        "--approved-by",
                        "CMU core owner",
                        "--split-title",
                        "Adapter handoffs need adapter-owner review",
                        "--split-summary",
                        "Adapter-specific review should route through the adapter owner before stable guidance changes.",
                        "--split-use-path",
                        "Ask the adapter owner to approve adapter-specific stable-memory changes.",
                        "--split-avoid",
                        "Do not treat adapter-specific review as general CMU owner review.",
                        "--split-challenge",
                        "The work is not adapter-specific.",
                        "--scope-code",
                        "cmu/openai_adapter.py",
                        "--scope-workflow",
                        "adapter integration",
                        "--scope-actor",
                        "agent",
                        "--evidence",
                        "Adapter owner review requires a narrow stable practice.",
                    ]
                )
            self.assertEqual(split_exit, 0, split_output.getvalue())
            self.assertIn("Outcome Memory:", split_output.getvalue())
            practices = MemoryStore(tmp).list(type=MemoryType.PRACTICE)
            self.assertTrue(any(item.title == "Adapter handoffs need adapter-owner review" for item in practices))

            narrow_target = next(item for item in MemoryStore(tmp).list(type=MemoryType.PRACTICE) if item.id == split_base.id)
            narrowed = apply_team_review_action(
                tmp,
                narrow_target.id,
                action="narrow-scope",
                approved_by="CMU core owner",
                scope=MemoryScope(code=["cmu/team_review_action.py"], workflow=["implementation"], actor=["agent"]),
                evidence=["Owner review narrowed this practice to the handoff action module."],
            )
            self.assertTrue(narrowed.applied, narrowed.render())
            narrowed_memory = next(item for item in MemoryStore(tmp).list(type=MemoryType.PRACTICE) if item.id == split_base.id)
            self.assertEqual(narrowed_memory.scope.code, ["cmu/team_review_action.py"])
            self.assertIn("Scope narrowed by CMU core owner", narrowed_memory.evidence)

    def test_portable_fixture_seed_creates_current_historical_invalid_future_and_legacy_fixtures(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Portable fixtures come from real stores",
                summary="Portable migration tests should derive fixtures from real exported memory.",
            )
            MemoryStore(tmp).add(memory)
            fixture_dir = Path(tmp) / "fixtures"

            report = seed_portable_fixtures(tmp, fixture_dir, include_historical=True)
            compat = portable_compat_report(fixture_dir)

            self.assertEqual(
                sorted(report.files),
                [
                    "future-v999-export.json",
                    "historical-2023-current-schema-export.json",
                    "historical-2024-current-schema-export.json",
                    "invalid-missing-memories.json",
                    "legacy-v0-export.json",
                    "migration-v0-to-current-plan.json",
                    "valid-current-export.json",
                ],
            )
            self.assertTrue(compat.passed, compat.render())
            self.assertIn("historical current-schema fixture still validates", compat.render())
            self.assertIn("legacy schema fixture failed validation", compat.render())
            self.assertIn("migration fixture failed safely", compat.render())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-compat", "--fixture-dir", str(fixture_dir)])
            self.assertEqual(exit_code, 0, output.getvalue())

            cli_dir = Path(tmp) / "cli-fixtures"
            seed_output = StringIO()
            with redirect_stdout(seed_output):
                seed_exit = main(["--root", tmp, "portable-fixture-seed", "--output", str(cli_dir), "--historical"])
            self.assertEqual(seed_exit, 0, seed_output.getvalue())
            self.assertTrue((cli_dir / "historical-2024-current-schema-export.json").exists())

    def test_host_path_suite_runs_fixture_scenarios_runner_codex_and_compare(self) -> None:
        with TemporaryDirectory() as tmp:
            report = run_host_path_suite(Path(tmp) / "suite", keep=True)
            rendered = report.render()

            self.assertTrue(report.passed, rendered)
            self.assertEqual({item.kind for item in report.items}, {"billing-incident", "checkout-release", "inventory-migration"})
            self.assertTrue(all(item.scenario_passed for item in report.items))
            self.assertTrue(all(item.runner_passed for item in report.items))
            self.assertTrue(all(item.codex_ok for item in report.items))
            self.assertTrue(all(item.openai_ok for item in report.items))
            self.assertTrue(all(item.comparison_class == "unchanged-pass" for item in report.items))
            self.assertIn("openai=pass", rendered)
            self.assertIn("CMU Host Path Suite", rendered)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["host-path-suite", "--work-dir", str(Path(tmp) / "cli-suite"), "--strict"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("Status: pass", output.getvalue())

    def test_evidence_service_records_background_service_state(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Evidence service should reuse session monitor",
                summary="Background evidence service cycles should record durable state.",
                scope=MemoryScope(code=["cmu/evidence_service.py"]),
                evidence=["Service proof uses the real evidence session path."],
            )
            MemoryStore(tmp).add(memory)

            report = run_evidence_service(
                tmp,
                MemoryStore(tmp).list(),
                MemoryUseStore(tmp).list(),
                interval_seconds=0,
                max_cycles=1,
                record_sessions=True,
                record_service=True,
            )
            self.assertEqual(len(report.cycles), 1, report.render())
            state = json.loads((Path(tmp) / ".cmu" / "evidence_service_runs.json").read_text(encoding="utf-8"))
            self.assertEqual(state["service_runs"][0]["schema"], "cmu-evidence-service/v1")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-service", "--interval", "0", "--max-cycles", "1"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Evidence Service", output.getvalue())

    def test_lifecycle_settle_applies_gravity_backed_settling_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Settled release checklist memory",
                summary="Release checklist guidance has enough scope, evidence, and use to settle.",
                signals=["release", "checklist", "settling"],
                scope=MemoryScope(ownership=["Release"], code=["release"], workflow=["deploy"], actor=["agent"]),
                evidence=["Used in deploy dry run.", "Used in rollback rehearsal.", "Reviewed by release owner.", "Kept scoped."],
                liability_score=4,
                confidence=0.7,
            )
            MemoryStore(tmp).add(memory)
            add_strong_receipts(tmp, memory, count=2)

            preview = lifecycle_settle(MemoryStore(tmp).list(), MemoryUseStore(tmp).list())
            self.assertTrue(preview.items, preview.render())
            applied = lifecycle_settle(MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), apply=True)
            for changed in applied.changed_memories:
                MemoryStore(tmp).update(changed)
            loaded = MemoryStore(tmp).list()[0]
            self.assertTrue(any(item.startswith("Lifecycle settled in current scope") for item in loaded.evidence))
            self.assertGreater(loaded.confidence, 0.7)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-settle", "--apply"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Lifecycle Settling", output.getvalue())

    def test_lifecycle_scope_suggest_creates_candidate_from_receipt_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Broad adapter rollout guidance",
                summary="Adapter rollout guidance may need narrower retrieval scope after drag evidence.",
                scope=MemoryScope(code=["cmu"], workflow=["adapter rollout"], actor=["agent"]),
                evidence=["Initial stable guidance was broad."],
                approved_by="Adapter owner",
                liability_score=4,
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(
                    prompt="Change OpenAI adapter behavior",
                    actor="agent",
                    area="adapters",
                    files=["cmu/openai_adapter.py"],
                    workflow=["openai adapter"],
                    risk="high",
                ),
                match=type("MatchStub", (), {"score": 4.1})(),
            )
            receipt.outcome_signal = "low_confidence"
            receipt.flags = ["no_file_overlap"]
            MemoryUseStore(tmp).add(receipt)

            preview = lifecycle_scope_suggestions(MemoryStore(tmp).list(), MemoryUseStore(tmp).list())
            self.assertEqual(preview.items[0].action, "scope-refinement")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "lifecycle-scope-suggest", "--apply"])
            self.assertEqual(exit_code, 0, output.getvalue())
            candidates = MemoryStore(tmp).list(type=MemoryType.CANDIDATE)
            self.assertEqual(len(candidates), 1)
            self.assertIn("Scope refinement target", "\n".join(candidates[0].evidence))

    def test_review_export_writes_structured_non_cli_review_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Review export authority gap",
                summary="Stable memory should appear in structured review export.",
                scope=MemoryScope(ownership=["Platform"], code=["cmu"]),
                liability_score=4,
                approved_by="legacy owner",
            )
            MemoryStore(tmp).add(memory)
            output_path = Path(tmp) / ".cmu" / "review_payload.json"
            memories = MemoryStore(tmp).list()
            receipts = MemoryUseStore(tmp).list()
            team_scopes = TeamDirectoryStore(tmp).list()
            preview = export_review_payload(
                root=tmp,
                output=output_path,
                queue=review_queue(memories, receipts, team_scopes),
                handoffs=team_review_handoffs(memories, team_scopes),
                reminders=review_reminders(memories, receipts, team_scopes=team_scopes),
                write=False,
            )
            self.assertFalse(output_path.exists())
            self.assertGreaterEqual(preview.queue_cards + preview.handoff_cards, 1)

            cli_output = StringIO()
            with redirect_stdout(cli_output):
                exit_code = main(["--root", tmp, "review-export", "--output", str(output_path), "--write"])
            self.assertEqual(exit_code, 0, cli_output.getvalue())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "cmu-review-export/v1")
            self.assertTrue(payload["read_only"])
            self.assertGreaterEqual(payload["summary"]["queue_cards"] + payload["summary"]["handoff_cards"], 1)

    def test_host_setup_manifest_writes_adapter_and_mcp_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            report = host_setup_manifest(tmp, host="all", output=".cmu/host_manifest.json", write=True)
            self.assertTrue(report.wrote, report.render())
            manifest_path = Path(tmp) / ".cmu" / "host_manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "cmu-host-setup-manifest/v1")
            self.assertEqual(payload["mcp"]["server_name"], MCP_SERVER_NAME)
            self.assertIn("codex", payload["adapters"])
            self.assertIn("openai", payload["adapters"])

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "host-setup-manifest", "--host", "openai", "--output", ".cmu/openai_manifest.json", "--write"])
            self.assertEqual(exit_code, 0, output.getvalue())
            openai_payload = json.loads((Path(tmp) / ".cmu" / "openai_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(openai_payload["adapters"]), ["openai"])
            self.assertIn("CMU Host Setup Manifest", output.getvalue())

    def test_evidence_service_install_generates_service_manager_wrappers(self) -> None:
        with TemporaryDirectory() as tmp:
            report = evidence_service_install(tmp, target="windows-task", output=".cmu/wrappers", interval_seconds=5, write=True)
            rendered = report.render()
            wrapper_dir = Path(tmp) / ".cmu" / "wrappers"
            manifest = json.loads((wrapper_dir / "cmu-evidence-service.install.json").read_text(encoding="utf-8"))
            script = (wrapper_dir / "cmu-evidence-service-task.ps1").read_text(encoding="utf-8")

            self.assertTrue(report.wrote, rendered)
            self.assertEqual(manifest["schema"], "cmu-evidence-service-install/v1")
            self.assertIn("evidence-service", manifest["command"])
            self.assertIn("--interval 5", manifest["command"])
            self.assertIn("Set-Location", script)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-service-install", "--target", "systemd-user", "--interval", "0", "--write"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertTrue((Path(tmp) / ".cmu" / "service-wrappers" / "cmu-evidence-service.service").exists())
            self.assertIn("CMU Evidence Service Install", output.getvalue())

    def test_host_examples_write_manifest_derived_runtime_examples(self) -> None:
        with TemporaryDirectory() as tmp:
            report = host_examples(tmp, host="all", output=".cmu/examples", write=True)
            example_dir = Path(tmp) / ".cmu" / "examples"
            codex = json.loads((example_dir / "codex-mcp.json").read_text(encoding="utf-8"))
            openai = json.loads((example_dir / "openai-runner-event.json").read_text(encoding="utf-8"))

            self.assertTrue(report.wrote, report.render())
            self.assertEqual(codex["mcpServers"]["central-memory-unit"]["command"], "cmu-mcp")
            self.assertEqual(openai["event"], "openai.run.started")
            self.assertTrue((example_dir / "mcp-tool-call.json").exists())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "host-examples", "--host", "openai", "--output", ".cmu/openai-example", "--write"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertTrue((Path(tmp) / ".cmu" / "openai-example" / "openai-runner-event.json").exists())
            self.assertFalse((Path(tmp) / ".cmu" / "openai-example" / "codex-mcp.json").exists())

    def test_review_inbox_renders_live_and_exported_non_cli_review_items(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Inbox authority gap",
                summary="Review inbox should surface stable authority gaps.",
                scope=MemoryScope(ownership=["Platform"], code=["cmu"]),
                liability_score=4,
                approved_by="legacy owner",
            )
            MemoryStore(tmp).add(memory)
            memories = MemoryStore(tmp).list()
            receipts = MemoryUseStore(tmp).list()
            team_scopes = TeamDirectoryStore(tmp).list()
            inbox = review_inbox_from_reports(
                root=tmp,
                queue=review_queue(memories, receipts, team_scopes),
                handoffs=team_review_handoffs(memories, team_scopes),
                reminders=review_reminders(memories, receipts, team_scopes=team_scopes),
            )
            self.assertGreaterEqual(len(inbox.items), 1, inbox.render())
            self.assertTrue(inbox.to_json().startswith("{"))

            export_path = Path(tmp) / ".cmu" / "review_export.json"
            export_review_payload(
                root=tmp,
                output=export_path,
                queue=review_queue(memories, receipts, team_scopes),
                handoffs=team_review_handoffs(memories, team_scopes),
                reminders=review_reminders(memories, receipts, team_scopes=team_scopes),
                write=True,
            )
            exported = review_inbox_from_export(export_path)
            self.assertEqual(len(exported.items), len(inbox.items))

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "review-inbox", "--input", str(export_path), "--json"])
            self.assertEqual(exit_code, 0, output.getvalue())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], "cmu-review-inbox/v1")

    def test_product_console_combines_graph_review_evidence_cleanup_and_navigation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use approved rollback checklist",
                summary="Release rollback should follow the owner-approved checklist.",
                scope=MemoryScope(ownership=["Release"], code=["checkout"], workflow=["deploy"]),
                evidence=["Two checkout releases reused the checklist."],
                use_this_path="Run the rollback checklist before touching production deploy state.",
                liability_score=4,
                confidence=0.9,
            )
            situation = Memory.create(
                type=MemoryType.SITUATION,
                title="Checkout release rollback",
                summary="Checkout release failures came from skipping the rollback checklist.",
                scope=MemoryScope(ownership=["Release"], code=["checkout"], workflow=["deploy"]),
                evidence=["Incident review linked the rollback miss to deploy recovery time."],
                use_this_path="Stop deployment and run rollback checklist before retrying.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.RELATED_PRACTICE,
                        target_id=practice.id,
                        reason="The incident should navigate to the stable rollback practice.",
                    )
                ],
                liability_score=4,
                confidence=0.85,
            )
            exception = Memory.create(
                type=MemoryType.EXCEPTION,
                title="Read-only checkout docs exception",
                summary="Docs-only checkout edits do not need rollback execution.",
                relationships=[
                    MemoryRelationship(
                        type=MemoryRelationType.EXCEPTION_TO,
                        target_id=situation.id,
                        reason="Docs-only changes should not trigger the deploy rollback path.",
                    )
                ],
            )
            for memory in [practice, situation, exception]:
                store.add(memory)

            receipt = MemoryUseReceipt.create(
                situation,
                PreflightQuery(
                    actor="agent",
                    area="checkout",
                    files=["src/checkout/release.py"],
                    prompt="Fix checkout deploy rollback handling",
                    risk="high",
                    workflow=["deploy"],
                ),
                Match(memory=situation, score=2.4, matched_terms=["checkout", "rollback"]),
                source_command="start",
            )
            link_commit(
                receipt,
                CommitLinkRequest(
                    use_id=receipt.id,
                    commit_hash="abc123456789",
                    message="Apply checkout rollback checklist",
                    files=["src/checkout/release.py"],
                    metadata_source="manual-test",
                    note="Product console evidence fixture.",
                ),
            )
            MemoryUseStore(tmp).add(receipt)

            report = product_console_report(
                MemoryStore(tmp).list(),
                MemoryUseStore(tmp).list(),
                TeamDirectoryStore(tmp).list(),
                root=tmp,
            )
            self.assertGreaterEqual(len(report.graph_nodes), 3, report.render())
            self.assertTrue(any(card.category == "authority-approval" for card in report.review_cards), report.render())
            self.assertTrue(any(card.memory_id == situation.id for card in report.trust_cards), report.render())
            self.assertTrue(any(item.category == "authority" for item in report.cleanup_items), report.render())
            nav = next(path for path in report.navigation_paths if path.situation_id == situation.id)
            self.assertIn(practice.id, " ".join(nav.practices))
            self.assertIn(exception.id, " ".join(nav.exceptions))

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "product-console", "--json"])
            self.assertEqual(exit_code, 0, output.getvalue())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], "cmu-product-console/v1")
            self.assertTrue(payload["read_only"])
            self.assertGreaterEqual(payload["summary"]["review_cards"], 1)
            self.assertGreaterEqual(payload["summary"]["navigation_paths"], 1)

    def test_product_console_memory_filter_focuses_existing_store_without_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Filtered evidence practice",
                summary="Filtered console should keep evidence scoped to this memory.",
                scope=MemoryScope(code=["cmu/product"]),
                approved_by="legacy owner",
                liability_score=4,
            )
            candidate = Memory.create(
                type=MemoryType.CANDIDATE,
                title="Unrelated candidate",
                summary="This unrelated card should not appear in a focused product console.",
                scope=MemoryScope(code=["other"]),
                evidence=["candidate evidence"],
                use_this_path="Promote only when related.",
                liability_score=2,
            )
            store.add(practice)
            store.add(candidate)
            receipt = MemoryUseReceipt(
                id="use_product_filter",
                memory_id=practice.id,
                memory_title=practice.title,
                prompt="Inspect product console trust evidence",
                actor="agent",
                area="product",
                files=["cmu/product_console.py"],
                risk="medium",
                match_score=2.1,
            )
            MemoryUseStore(tmp).add(receipt)
            memories_before = (Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8")
            receipts_before = (Path(tmp) / ".cmu" / "uses.json").read_text(encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "product-console", "--memory", practice.id])

            self.assertEqual(exit_code, 0, output.getvalue())
            rendered = output.getvalue()
            self.assertIn("CMU Product Console", rendered)
            self.assertIn(practice.id, rendered)
            self.assertNotIn(candidate.id, rendered)
            self.assertEqual(memories_before, (Path(tmp) / ".cmu" / "memories.json").read_text(encoding="utf-8"))
            self.assertEqual(receipts_before, (Path(tmp) / ".cmu" / "uses.json").read_text(encoding="utf-8"))

    def test_fixture_repo_catalog_includes_inventory_migration_fixture(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "inventory-migration"
            report = create_fixture_repo("inventory-migration", fixture_root)
            self.assertEqual(report.kind, "inventory-migration")
            self.assertTrue((fixture_root / "src" / "inventory" / "migrate.py").exists())
            self.assertTrue((fixture_root / "tests" / "test_inventory_migrate.py").exists())

            memories = MemoryStore(fixture_root).list()
            scenarios = ScenarioLibraryStore(fixture_root).list(tag="migration")
            self.assertEqual(len(memories), 1)
            self.assertEqual(len(scenarios), 1)
            self.assertEqual(scenarios[0].expect_memory, memories[0].id)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", str(fixture_root), "scenario-run", "--tag", "fixture", "--strict"])
            self.assertEqual(exit_code, 0, output.getvalue())

    def test_portable_fixture_seed_adds_migration_and_multiple_historical_fixtures(self) -> None:
        with TemporaryDirectory() as tmp:
            MemoryStore(tmp).add(
                Memory.create(
                    type=MemoryType.SITUATION,
                    title="Portable migration corpus memory",
                    summary="Portable fixtures should include migration and historical corpus examples.",
                    scope=MemoryScope(code=["portable"]),
                    evidence=["Fixture corpus uses real exported stores."],
                )
            )
            fixture_dir = Path(tmp) / "fixtures"
            report = seed_portable_fixtures(tmp, fixture_dir, include_historical=True)
            self.assertIn("migration-v0-to-current-plan.json", report.files)
            self.assertIn("historical-2023-current-schema-export.json", report.files)
            self.assertIn("historical-2024-current-schema-export.json", report.files)

            compat = portable_compat_report(fixture_dir)
            self.assertTrue(compat.passed, compat.render())
            self.assertIn("migration fixture failed safely", compat.render())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "portable-compat", "--fixture-dir", str(fixture_dir)])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("migration-v0-to-current-plan.json", output.getvalue())


class FiveChunkBurndownCycleTests(unittest.TestCase):
    def test_copilot_runner_adapter_uses_real_hooks_and_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use Copilot runner adapter for IDE work",
                summary="Copilot chat events should call CMU runner hooks before meaningful IDE edits.",
                signals=["copilot runner", "ide adapter"],
                scope=MemoryScope(code=["cmu/copilot_adapter.py"], workflow=["agent integration"], actor=["agent"]),
                evidence=["The adapter should translate host events without duplicating memory logic."],
                use_this_path="Route Copilot chat starts through the runner adapter.",
                avoid_this="Do not parse CLI prose from Copilot host integrations.",
                challenge_only_if="The host can call AgentIntegration directly with identical semantics.",
                liability_score=4,
                confidence=0.9,
                approved_by="CMU core owner",
            )
            MemoryStore(tmp).add(practice)

            report = copilot_runner_report(tmp)
            self.assertEqual(report.manifest["version"], COPILOT_RUNNER_ADAPTER_VERSION)
            self.assertEqual(report.manifest["host"], "copilot")

            adapter = CopilotRunnerAdapter(tmp)
            started = adapter.handle(
                {
                    "event": "copilot.chat.started",
                    "payload": {
                        "message": "wire Copilot runner IDE adapter",
                        "actor": "agent",
                        "area": "cmu",
                        "files": ["cmu/copilot_adapter.py"],
                        "workflow": ["agent integration"],
                        "risk": "high",
                    },
                }
            )

            self.assertTrue(started.ok)
            self.assertEqual(started.status, "action-note")
            self.assertEqual(started.hook_result["response"]["matched_memory"]["id"], practice.id)
            [receipt] = MemoryUseStore(tmp).list()
            self.assertEqual(receipt.memory_id, practice.id)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "copilot-runner",
                        "--input",
                        json.dumps({"event": "copilot.chat.finished", "payload": {"reusable_learning": False}}),
                    ]
                )
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("copilot.chat.finished", output.getvalue())

    def test_mcp_setup_check_validates_generated_and_configured_hosts(self) -> None:
        with TemporaryDirectory() as tmp:
            generated = mcp_setup_check(tmp, host="vscode")
            self.assertTrue(generated.passed, generated.render())

            config = Path(tmp) / "mcp.json"
            config.write_text(
                json.dumps({"mcp": {"servers": {MCP_SERVER_NAME: {"command": "cmu-mcp", "args": ["--root", tmp]}}}}),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "mcp-setup-check", "--host", "vscode", "--config", str(config)])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("Status: pass", output.getvalue())

            workspace_tmp_parent = Path.cwd() / ".manual" / "test-mcp-setup-check"
            workspace_tmp_parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=workspace_tmp_parent) as workspace_tmp:
                workspace_config = Path(workspace_tmp) / "mcp.json"
                workspace_config.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
                relative_config = workspace_config.relative_to(Path.cwd())
                workspace_report = mcp_setup_check(tmp, host="vscode", config=relative_config)
                self.assertTrue(workspace_report.passed, workspace_report.render())

    def test_ide_workflow_writes_vscode_tasks_mcp_and_snippet_files(self) -> None:
        with TemporaryDirectory() as tmp:
            preview = ide_workflow(tmp, write=False)
            self.assertEqual(len(preview.files), 3)
            self.assertFalse((Path(tmp) / ".vscode" / "tasks.json").exists())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "ide-workflow", "--write"])
            self.assertEqual(exit_code, 0, output.getvalue())
            tasks = json.loads((Path(tmp) / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(tasks["tasks"][0]["label"], "CMU: start work")
            mcp = json.loads((Path(tmp) / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
            self.assertIn(MCP_SERVER_NAME, mcp["servers"])
            self.assertTrue((Path(tmp) / ".vscode" / "cmu.code-snippets").exists())

    def test_reminder_dispatch_is_idempotent_after_outbox_delivery(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Dispatch expired authority reminder",
                summary="Expired authority reminders should reach a dispatch log.",
                approved_by="Checkout owner",
                authority_owner="Checkout team",
                authority_role="owner",
                authority_consequence="high",
                authority_review_due_at="2026-01-01T00:00:00+00:00",
            )
            MemoryStore(tmp).add(memory)
            reminders = review_reminders(MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), days=30)
            outbox = Path(tmp) / ".cmu" / "outbox.jsonl"
            deliver_reminders_to_outbox(reminders, root=tmp, outbox=outbox, apply=True)
            dispatch_log = Path(tmp) / ".cmu" / "dispatch.jsonl"

            preview = dispatch_reminder_outbox(tmp, outbox=outbox, dispatch_log=dispatch_log, apply=False)
            self.assertEqual(len(preview.dispatched), 1)
            self.assertFalse(dispatch_log.exists())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["--root", tmp, "reminder-dispatch", "--outbox", str(outbox), "--dispatch-log", str(dispatch_log), "--apply"]
                )
            self.assertEqual(exit_code, 0, output.getvalue())
            [line] = dispatch_log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(json.loads(line)["schema"], "cmu-reminder-dispatch/v1")

            second = dispatch_reminder_outbox(tmp, outbox=outbox, dispatch_log=dispatch_log, apply=True)
            self.assertEqual(len(second.dispatched), 0)
            self.assertEqual(second.skipped, 1)

    def test_scenario_no_memory_comparison_shows_cmu_added_guidance(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use billing reconciliation guard",
                summary="Billing reconciliation fixes must replay invoices before settlement.",
                signals=["billing reconciliation", "settlement"],
                scope=MemoryScope(code=["billing/reconcile.py"], workflow=["incident"], actor=["agent"]),
                evidence=["Previous incident replay found settlement drift."],
                use_this_path="Replay invoices before settlement changes.",
                avoid_this="Do not patch settlement totals without replay evidence.",
                challenge_only_if="A newer incident runbook supersedes this flow.",
                liability_score=5,
                confidence=0.9,
                approved_by="Billing owner",
            )
            MemoryStore(tmp).add(memory)
            scenario = ScenarioDefinition.create(
                name="Billing replay incident",
                prompt="Fix billing reconciliation settlement drift",
                actor="agent",
                area="billing",
                files=["billing/reconcile.py"],
                workflow=["incident"],
                risk="high",
                expect_action="action-note",
                expect_memory=memory.id,
                tags=["ab"],
            )
            ScenarioLibraryStore(tmp).add(scenario)
            scenarios = ScenarioLibraryStore(tmp).list(tag="ab")

            report = compare_scenario_library_to_no_memory(
                scenarios,
                current_memories=MemoryStore(tmp).list(),
                current_receipts=MemoryUseStore(tmp).list(),
                root=tmp,
                tag="ab",
            )
            self.assertEqual(report.items[0].classification, "cmu-added-guidance")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "scenario-no-memory-compare", "--tag", "ab"])
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("cmu-added-guidance", output.getvalue())

    def test_cli_work_loop_run_executes_runtime_events_and_auto_evidence_session(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Check release marker before retry",
                summary="Checkout rollback retries must inspect release marker state first.",
                signals=["checkout", "rollback", "release marker"],
                scope=MemoryScope(code=["src/checkout/release.py"], workflow=["rollback"], actor=["agent"]),
                evidence=["A previous rollback succeeded after release marker inspection."],
                use_this_path="Inspect the release marker before retrying rollback.",
                avoid_this="Do not retry the rollback blindly.",
                challenge_only_if="Rollback no longer uses release markers.",
                approved_by="release owner",
            )
            MemoryStore(tmp).add(memory)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(
                    prompt="Fix checkout rollback release marker retry",
                    actor="agent",
                    area="checkout",
                    files=["src/checkout/release.py"],
                    workflow=["rollback"],
                    risk="high",
                ),
                match=type("MatchStub", (), {"score": 4.2})(),
                source_command="work-loop-run",
            )
            MemoryUseStore(tmp).add(receipt)
            write_and_commit(tmp, "src/checkout/release.py", "MARKER = 'checked'\n", "Fix checkout rollback release marker")
            event_file = Path(tmp) / "events.json"
            event_file.write_text(json.dumps({"events": [{"event": "evidence.session"}]}), encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        tmp,
                        "work-loop-run",
                        "--input-file",
                        str(event_file),
                        "--auto-evidence",
                        "--apply-evidence",
                        "--record",
                    ]
                )
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertIn("CMU Automatic Work Loop Run", output.getvalue())
            self.assertIn("evidence.session", output.getvalue())
            linked = MemoryUseStore(tmp).get(receipt.id)
            self.assertEqual(linked.metadata_source, "git-monitor")
            self.assertTrue((Path(tmp) / ".cmu" / "work_loop_runs.json").exists())
            self.assertTrue((Path(tmp) / ".cmu" / "evidence_sessions.json").exists())

    def test_evidence_monitor_links_documentation_only_checkpoint_without_mixed_drag(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            memory = Memory.create(
                type=MemoryType.SITUATION,
                title="Document release notes after rollback",
                summary="Release rollback work should update the operator notes.",
                signals=["release notes", "rollback"],
                scope=MemoryScope(code=["docs/release.md"], workflow=["documentation"], actor=["agent"]),
                evidence=["Operators relied on the rollback notes."],
                use_this_path="Update release notes with the rollback decision.",
                avoid_this="Do not leave operators without the note.",
            )
            MemoryStore(tmp).add(memory)
            write_and_commit(tmp, "docs/release.md", "rollback notes\n", "Update rollback release notes")
            metadata = inspect_git_commit(tmp, "HEAD")
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(
                    prompt="Update rollback release notes",
                    actor="agent",
                    area="docs",
                    files=["docs/release.md"],
                    workflow=["documentation"],
                    risk="medium",
                ),
                match=type("MatchStub", (), {"score": 3.9})(),
            )
            receipt.surfaced_at = before_commit(metadata, minutes=5)
            MemoryUseStore(tmp).add(receipt)

            report = monitor_checkpoints(tmp, MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), apply=True)
            self.assertEqual(report.linked_count, 1, report.render())
            linked = MemoryUseStore(tmp).get(receipt.id)
            self.assertIn("documentation_only", linked.flags)
            self.assertNotIn("mixed_commit", linked.flags)
            self.assertEqual(linked.outcome_signal, "committed")

    def test_evidence_monitor_classifies_multi_commit_candidates_for_review(self) -> None:
        with TemporaryDirectory() as tmp:
            init_git_repo(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Verify billing replay before settlement",
                summary="Billing settlement fixes should replay invoices first.",
                signals=["billing", "settlement", "replay"],
                scope=MemoryScope(code=["billing/replay.py"], workflow=["incident"], actor=["agent"]),
                evidence=["Replay caught settlement drift."],
                use_this_path="Replay invoices before settlement changes.",
                avoid_this="Do not patch totals without replay.",
                approved_by="billing owner",
            )
            MemoryStore(tmp).add(memory)
            write_and_commit(tmp, "billing/replay.py", "STEP = 1\n", "Billing replay settlement guard")
            first = inspect_git_commit(tmp, "HEAD")
            write_and_commit(tmp, "billing/replay.py", "STEP = 2\n", "Billing replay settlement followup")
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(
                    prompt="Fix billing settlement replay drift",
                    actor="agent",
                    area="billing",
                    files=["billing/replay.py"],
                    workflow=["incident"],
                    risk="high",
                ),
                match=type("MatchStub", (), {"score": 4.1})(),
            )
            receipt.surfaced_at = before_commit(first, minutes=5)
            MemoryUseStore(tmp).add(receipt)

            report = monitor_checkpoints(tmp, MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), apply=True)
            self.assertEqual(report.linked_count, 0, report.render())
            self.assertEqual(report.review_count, 1, report.render())
            self.assertIn("multi_commit_candidates", report.items[0].flags)
            self.assertEqual(MemoryUseStore(tmp).get(receipt.id).commit_hash, "")

    def test_cli_evidence_metrics_reports_longitudinal_sessions_and_drag(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Use package version guard",
                summary="Dependency fixes should inspect package versions before retries.",
                signals=["dependency", "package"],
                scope=MemoryScope(code=["requirements.txt"], workflow=["debugging"], actor=["agent"]),
                evidence=["Version mismatch caused repeated test failures."],
                use_this_path="Inspect package versions before rerunning tests.",
                avoid_this="Do not rerun tests blindly.",
                approved_by="platform owner",
            )
            MemoryStore(tmp).add(memory)
            add_strong_receipts(tmp, memory, count=1)
            receipt = MemoryUseReceipt.create(
                memory,
                PreflightQuery(prompt="Debug package retry", actor="agent", area="deps", files=["requirements.txt"], risk="medium"),
                match=type("MatchStub", (), {"score": 3.8})(),
            )
            receipt.commit_hash = "draggy"
            receipt.commit_files = ["unrelated.py"]
            receipt.outcome_signal = "committed_low_confidence"
            receipt.flags = ["no_file_overlap"]
            receipt.link_confidence = 0.25
            MemoryUseStore(tmp).add(receipt)
            run_evidence_session(tmp, MemoryStore(tmp).list(), MemoryUseStore(tmp).list(), record=True)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "evidence-metrics"])
            self.assertEqual(exit_code, 0, output.getvalue())
            rendered = output.getvalue()
            self.assertIn("CMU Longitudinal Evidence Metrics", rendered)
            self.assertIn("Sessions: 1", rendered)
            self.assertIn("Strong Uses: 1", rendered)
            self.assertIn("Drag Signals: 1", rendered)

    def test_cli_scenario_suite_records_longitudinal_no_memory_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Replay invoices before settlement",
                summary="Billing settlement incident fixes should replay invoices before applying totals.",
                signals=["billing settlement", "invoice replay"],
                scope=MemoryScope(code=["billing/reconcile.py"], workflow=["incident"], actor=["agent"]),
                evidence=["Invoice replay caught settlement drift."],
                use_this_path="Replay invoices before settlement changes.",
                avoid_this="Do not patch totals without replay evidence.",
                challenge_only_if="Settlement no longer depends on invoices.",
                approved_by="billing owner",
            )
            MemoryStore(tmp).add(memory)
            ScenarioLibraryStore(tmp).add(
                ScenarioDefinition.create(
                    name="Billing settlement replay",
                    prompt="Fix billing settlement invoice replay drift",
                    actor="agent",
                    area="billing",
                    files=["billing/reconcile.py"],
                    workflow=["incident"],
                    risk="high",
                    expect_action="action-note",
                    expect_memory=memory.id,
                    tags=["longitudinal"],
                )
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "scenario-suite", "--tag", "longitudinal", "--record", "--strict"])
            self.assertEqual(exit_code, 0, output.getvalue())
            rendered = output.getvalue()
            self.assertIn("CMU Longitudinal Scenario Suite", rendered)
            self.assertIn("cmu_added_guidance=1", rendered)
            runs = json.loads((Path(tmp) / ".cmu" / "scenario_suite_runs.json").read_text(encoding="utf-8"))
            self.assertEqual(runs["runs"][0]["total"], 1)


def add_strong_receipts(root: str, memory: Memory, *, count: int) -> None:
    for index in range(count):
        receipt = MemoryUseReceipt.create(
            memory,
            PreflightQuery(prompt="Fix billing deploy", actor="agent", area="billing", files=["billing/deploy.py"], risk="high"),
            match=type("MatchStub", (), {"score": 4.2})(),
        )
        receipt.commit_hash = f"strong{index}"
        receipt.commit_message = "Fix billing deploy"
        receipt.commit_files = ["billing/deploy.py"]
        receipt.outcome_signal = "committed"
        receipt.link_confidence = 0.85
        MemoryUseStore(root).add(receipt)


def before_commit(metadata, *, minutes: int) -> str:
    committed = datetime.fromisoformat(metadata.commit_time)
    return (committed - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def short_test_hash(value: str) -> str:
    return value[:12]


class RetrievalMetricsAndPublishCheckTests(unittest.TestCase):
    def test_retrieval_metrics_and_benchmark_use_saved_scenario_expectations(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            memory = Memory.create(
                type=MemoryType.PRACTICE,
                title="Checkout rollback order",
                summary="Checkout rollback work must revert the feature flag before replaying orders.",
                signals=["checkout rollback", "feature flag", "orders"],
                scope=MemoryScope(code=["checkout"], workflow=["rollback"], actor=["agent"]),
                evidence=["Release drill showed this avoids duplicate order replay."],
                use_this_path="Revert the feature flag, verify order replay is disabled, then continue rollback.",
                avoid_this="Do not replay orders before the flag is reverted.",
                challenge_only_if="The incident is outside checkout rollback.",
                liability_score=4,
                confidence=0.9,
                approved_by="Release Owner",
            )
            store.add(memory)
            library = ScenarioLibraryStore(tmp)
            library.add(
                ScenarioDefinition.create(
                    name="Checkout rollback memory should surface",
                    prompt="Recover checkout rollback after order replay risk appears.",
                    actor="agent",
                    area="checkout",
                    workflow=["rollback"],
                    risk="high",
                    expect_action="action-note",
                    expect_memory=memory.id,
                    tags=["metrics"],
                )
            )
            library.add(
                ScenarioDefinition.create(
                    name="Unrelated cosmetic work should stay quiet",
                    prompt="Change a footer icon label.",
                    actor="agent",
                    area="unrelated-ui",
                    workflow=["cosmetic-ui"],
                    risk="low",
                    expect_trigger="silent-skip",
                    expect_action="quiet",
                    expect_memory="none",
                    tags=["metrics"],
                )
            )

            metrics = retrieval_metrics_report(tmp, store.list(), [], tag="metrics")
            self.assertTrue(metrics.passed)
            self.assertEqual(metrics.true_positive_count, 1)
            self.assertEqual(metrics.true_rejection_count, 1)
            self.assertEqual(metrics.false_positive_count, 0)
            self.assertEqual(metrics.false_negative_count, 0)
            self.assertIn("precision=1.00", metrics.render())

            benchmark = retrieval_benchmark_report(tmp, store.list(), tag="metrics")
            self.assertTrue(benchmark.passed)
            self.assertEqual(benchmark.current_hits, 1)
            self.assertEqual(benchmark.no_memory_hits, 0)
            self.assertIn("generic_vector_hits", benchmark.render())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "retrieval-metrics", "--tag", "metrics", "--strict"])
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Retrieval Metrics", output.getvalue())

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "retrieval-benchmark", "--tag", "metrics", "--strict"])
            self.assertEqual(exit_code, 0)
            self.assertIn("CMU Retrieval Benchmark", output.getvalue())

    def test_scenario_eval_fixtures_seed_four_concrete_case_shapes(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            practice = Memory.create(
                type=MemoryType.PRACTICE,
                title="Checkout release guard",
                summary="Checkout release changes need rollback verification.",
                signals=["checkout release", "rollback verification"],
                scope=MemoryScope(code=["checkout"], workflow=["release"], actor=["agent"]),
                evidence=["Fixture proof"],
                use_this_path="Verify rollback before release.",
                liability_score=4,
                approved_by="Release Owner",
            )
            exception = Memory.create(
                type=MemoryType.EXCEPTION,
                title="Checkout hotfix exception",
                summary="A hotfix exception can bypass the normal release queue with owner approval.",
                signals=["checkout hotfix", "exception"],
                scope=MemoryScope(code=["checkout"], workflow=["challenge-review"], actor=["agent"]),
                relationships=[MemoryRelationship(type=MemoryRelationType.CHALLENGES, target_id=practice.id)],
            )
            store.add(practice)
            store.add(exception)

            preview = seed_retrieval_evaluation_cases(tmp, store.list(), write=False)
            self.assertFalse((Path(tmp) / ".cmu" / "scenarios.json").exists())
            self.assertEqual(len(preview.scenarios), 4)
            self.assertIn("retrieval-miss", {tag for scenario in preview.scenarios for tag in scenario.tags})
            self.assertIn("bad-match", {tag for scenario in preview.scenarios for tag in scenario.tags})
            self.assertIn("governance-block", {tag for scenario in preview.scenarios for tag in scenario.tags})
            self.assertIn("challenge-outcome", {tag for scenario in preview.scenarios for tag in scenario.tags})

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--root", tmp, "scenario-eval-fixtures", "--write"])
            self.assertEqual(exit_code, 0)
            self.assertIn("Written: yes", output.getvalue())
            saved = ScenarioLibraryStore(tmp).list(tag="retrieval-evaluation")
            self.assertEqual(len(saved), 4)

    def test_publish_check_validates_read_only_publication_workflow(self) -> None:
        report = publish_check(Path.cwd())
        self.assertTrue(report.passed, report.render())
        self.assertIn("CMU Publish Check", report.render())

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["--root", ".", "publish-check"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Status: pass", output.getvalue())


def write_and_commit(root: str, path: str, content: str, message: str) -> None:
    write_files_and_commit(root, {path: content}, [message])


def write_files_and_commit(root: str, files: dict[str, str], messages: list[str]) -> None:
    for path, content in files.items():
        file_path = Path(root) / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        run_git_test(root, ["add", path])
    commit_args = ["commit"]
    for message in messages:
        commit_args.extend(["-m", message])
    run_git_test(root, commit_args)


def run_git_test(root: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise unittest.SkipTest(f"git unavailable: {error}") from error
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


if __name__ == "__main__":
    unittest.main()
