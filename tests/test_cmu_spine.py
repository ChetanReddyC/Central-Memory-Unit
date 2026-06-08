import json
import os
import subprocess
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
from cmu.mcp import CmuMcpAdapter, mcp_tool_definitions
from cmu.models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from cmu.onboarding import NORMAL_SEED_WORD_LIMIT, build_onboarding_seed, word_count
from cmu.portable import PORTABLE_BUNDLE_VERSION, export_bundle_from_root, import_portable_bundle, validate_portable_bundle
from cmu.promotion import promote_memory, review_promotion
from cmu.quality import apply_decay_action, quality_card, quality_report
from cmu.readiness import readiness_report
from cmu.remembering import RememberRequest, remember_candidate
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
from cmu.sdk import CentralMemoryUnit
from cmu.setup import setup_guide
from cmu.store import MemoryStore
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


class QuickstartDemoTests(unittest.TestCase):
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
