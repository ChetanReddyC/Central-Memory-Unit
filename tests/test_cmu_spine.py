import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from cmu.challenges import ChallengeRequest, ResolveChallengeRequest, challenge_stable_memory, resolve_challenge
from cmu.cli import main
from cmu.models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryStatus, MemoryType
from cmu.onboarding import NORMAL_SEED_WORD_LIMIT, build_onboarding_seed, word_count
from cmu.promotion import promote_memory, review_promotion
from cmu.remembering import RememberRequest, remember_candidate
from cmu.retrieval import (
    HashingEmbeddingProvider,
    InMemorySemanticIndex,
    PersistentSemanticIndex,
    PreflightQuery,
    SemanticSignal,
    preflight,
    rank_memories,
)
from cmu.store import MemoryStore
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


def init_git_repo(root: str) -> None:
    run_git_test(root, ["init"])
    run_git_test(root, ["config", "user.email", "cmu@example.test"])
    run_git_test(root, ["config", "user.name", "CMU Test"])


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
