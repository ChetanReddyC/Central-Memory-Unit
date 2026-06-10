from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Memory, MemoryScope, MemoryType
from .scenarios import ScenarioDefinition, ScenarioLibraryStore
from .store import MemoryStore


FIXTURE_REPO_VERSION = "cmu-fixture-repo/v1"
FIXTURE_KINDS = {"billing-incident", "checkout-release", "inventory-migration"}


@dataclass
class FixtureRepoReport:
    kind: str
    output: Path
    memory_id: str = ""
    scenario_id: str = ""
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Fixture Repository",
            f"Version: {FIXTURE_REPO_VERSION}",
            f"Kind: {self.kind}",
            f"Output: {self.output}",
            f"Memory: {self.memory_id or 'none'}",
            f"Scenario: {self.scenario_id or 'none'}",
            "",
            "Files:",
        ]
        lines.extend(f"- {item}" for item in self.files) if self.files else lines.append("- None")
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {item}" for item in self.warnings)
        lines.extend(
            [
                "",
                "Proof Meaning: CMU now has a generated fixture repository with real files, scoped memory, and saved scenario expectations for repeatable host-path and evaluation tests.",
            ]
        )
        return "\n".join(lines)


def create_fixture_repo(kind: str, output: Path | str) -> FixtureRepoReport:
    normalized = kind.strip().lower()
    if normalized not in FIXTURE_KINDS:
        raise ValueError(f"unsupported fixture kind: {kind}")
    root = Path(output)
    if root.exists() and any(root.iterdir()):
        raise ValueError("fixture output directory already exists and is not empty")
    root.mkdir(parents=True, exist_ok=True)
    if normalized == "checkout-release":
        return create_checkout_release_fixture(root)
    if normalized == "billing-incident":
        return create_billing_incident_fixture(root)
    if normalized == "inventory-migration":
        return create_inventory_migration_fixture(root)
    raise ValueError(f"unsupported fixture kind: {kind}")


def create_checkout_release_fixture(root: Path) -> FixtureRepoReport:
    written: list[str] = []
    write_fixture_file(
        root,
        "README.md",
        "# Checkout Release Fixture\n\nA tiny repository used by CMU scenario and runner integration tests.\n",
        written,
    )
    write_fixture_file(
        root,
        "src/checkout/release.py",
        "\n".join(
            [
                '"""Release marker helpers for the CMU checkout fixture."""',
                "",
                "def should_retry_rollback(marker_state: str) -> bool:",
                '    return marker_state == "clean"',
                "",
            ]
        ),
        written,
    )
    write_fixture_file(
        root,
        "tests/test_checkout_release.py",
        "\n".join(
            [
                "from src.checkout.release import should_retry_rollback",
                "",
                "",
                "def test_retry_requires_clean_marker():",
                '    assert should_retry_rollback("clean")',
                '    assert not should_retry_rollback("stale")',
                "",
            ]
        ),
        written,
    )
    memory = Memory.create(
        type=MemoryType.PRACTICE,
        title="Checkout rollback checks release marker",
        summary="Checkout rollback work should inspect release marker state before retrying production rollback.",
        scope=MemoryScope(
            ownership=["Checkout team"],
            code=["checkout", "src/checkout/release.py"],
            workflow=["rollback", "release"],
            environment=["prod"],
            actor=["agent"],
        ),
        evidence=["Fixture repository encodes stale-marker rollback risk."],
        use_this_path="Inspect marker state before retrying checkout rollback.",
        avoid_this="Do not blindly retry production rollback when the marker is stale.",
        challenge_only_if="The checkout service no longer uses release markers.",
        liability_score=4,
        confidence=0.86,
        approved_by="Checkout owner",
        authority_owner="Checkout team",
        authority_role="owner",
        authority_consequence="high",
    )
    MemoryStore(root).add(memory)
    scenario = ScenarioDefinition.create(
        name="checkout rollback marker scenario",
        description="High-risk checkout rollback should surface the fixture Practice memory.",
        prompt="retry checkout rollback after production release marker drift",
        actor="agent",
        area="checkout",
        files=["src/checkout/release.py"],
        workflow=["rollback"],
        environment=["prod"],
        risk="high",
        irreversible=True,
        expect_trigger="must-call",
        expect_action="action-note",
        expect_memory=memory.id,
        expect_candidate="not-recommended",
        tags=["fixture", "checkout", "runner-host-path"],
    )
    ScenarioLibraryStore(root).add(scenario)
    warnings = init_git(root)
    return FixtureRepoReport(
        kind="checkout-release",
        output=root,
        memory_id=memory.id,
        scenario_id=scenario.id,
        files=sorted(written + [".cmu/memories.json", ".cmu/scenarios.json"]),
        warnings=warnings,
    )


def create_billing_incident_fixture(root: Path) -> FixtureRepoReport:
    written: list[str] = []
    write_fixture_file(
        root,
        "README.md",
        "# Billing Incident Fixture\n\nA tiny repository used by CMU owner-review, scenario, and runner integration tests.\n",
        written,
    )
    write_fixture_file(
        root,
        "src/billing/reconcile.py",
        "\n".join(
            [
                '"""Invoice reconciliation helpers for the CMU billing fixture."""',
                "",
                "def needs_idempotency_key(event: dict) -> bool:",
                '    return not bool(event.get("idempotency_key"))',
                "",
            ]
        ),
        written,
    )
    write_fixture_file(
        root,
        "tests/test_billing_reconcile.py",
        "\n".join(
            [
                "from src.billing.reconcile import needs_idempotency_key",
                "",
                "",
                "def test_reconciliation_requires_idempotency_key():",
                '    assert needs_idempotency_key({})',
                '    assert not needs_idempotency_key({"idempotency_key": "evt_123"})',
                "",
            ]
        ),
        written,
    )
    memory = Memory.create(
        type=MemoryType.PRACTICE,
        title="Billing replay requires idempotency evidence",
        summary="Billing incident work should prove replay idempotency before retrying invoice reconciliation.",
        scope=MemoryScope(
            ownership=["Billing owner"],
            code=["billing", "src/billing/reconcile.py"],
            workflow=["incident", "reconciliation"],
            environment=["prod"],
            actor=["agent"],
        ),
        evidence=["Fixture repository encodes invoice replay idempotency risk."],
        use_this_path="Confirm idempotency keys before replaying billing reconciliation.",
        avoid_this="Do not replay invoice reconciliation from incident logs without an idempotency check.",
        challenge_only_if="The billing system no longer accepts replayed reconciliation events.",
        liability_score=5,
        confidence=0.88,
        approved_by="Billing owner",
        authority_owner="Billing team",
        authority_role="owner",
        authority_consequence="critical",
    )
    MemoryStore(root).add(memory)
    scenario = ScenarioDefinition.create(
        name="billing incident replay scenario",
        description="High-risk billing incident replay should surface the fixture Practice memory.",
        prompt="replay billing invoice reconciliation after incident duplicate event",
        actor="agent",
        area="billing",
        files=["src/billing/reconcile.py"],
        workflow=["incident", "reconciliation"],
        environment=["prod"],
        risk="high",
        irreversible=True,
        expect_trigger="must-call",
        expect_action="action-note",
        expect_memory=memory.id,
        expect_candidate="not-recommended",
        tags=["fixture", "billing", "runner-host-path", "owner-review"],
    )
    ScenarioLibraryStore(root).add(scenario)
    warnings = init_git(root)
    return FixtureRepoReport(
        kind="billing-incident",
        output=root,
        memory_id=memory.id,
        scenario_id=scenario.id,
        files=sorted(written + [".cmu/memories.json", ".cmu/scenarios.json"]),
        warnings=warnings,
    )


def create_inventory_migration_fixture(root: Path) -> FixtureRepoReport:
    written: list[str] = []
    write_fixture_file(
        root,
        "README.md",
        "# Inventory Migration Fixture\n\nA tiny repository used by CMU migration, scenario, and host-path tests.\n",
        written,
    )
    write_fixture_file(
        root,
        "src/inventory/migrate.py",
        "\n".join(
            [
                '"""Inventory migration helpers for the CMU fixture catalog."""',
                "",
                "def requires_shadow_count(before: int, after: int) -> bool:",
                "    return abs(before - after) > 0",
                "",
            ]
        ),
        written,
    )
    write_fixture_file(
        root,
        "tests/test_inventory_migrate.py",
        "\n".join(
            [
                "from src.inventory.migrate import requires_shadow_count",
                "",
                "",
                "def test_shadow_count_required_when_counts_change():",
                "    assert requires_shadow_count(10, 11)",
                "    assert not requires_shadow_count(10, 10)",
                "",
            ]
        ),
        written,
    )
    memory = Memory.create(
        type=MemoryType.PRACTICE,
        title="Inventory migration requires shadow count proof",
        summary="Inventory migration work should compare shadow counts before switching read paths.",
        scope=MemoryScope(
            ownership=["Inventory owner"],
            code=["inventory", "src/inventory/migrate.py"],
            workflow=["migration", "data validation"],
            environment=["staging", "prod"],
            actor=["agent"],
        ),
        evidence=["Fixture repository encodes inventory count drift risk during migration."],
        use_this_path="Run shadow count comparison before switching inventory reads.",
        avoid_this="Do not flip inventory read paths without proving count parity.",
        challenge_only_if="Inventory migrations no longer use shadow-read validation.",
        liability_score=4,
        confidence=0.87,
        approved_by="Inventory owner",
        authority_owner="Inventory team",
        authority_role="owner",
        authority_consequence="high",
    )
    MemoryStore(root).add(memory)
    scenario = ScenarioDefinition.create(
        name="inventory migration shadow count scenario",
        description="High-risk inventory migration should surface the fixture Practice memory.",
        prompt="switch inventory read path after migration count drift",
        actor="agent",
        area="inventory",
        files=["src/inventory/migrate.py"],
        workflow=["migration", "data validation"],
        environment=["prod"],
        risk="high",
        irreversible=True,
        expect_trigger="must-call",
        expect_action="action-note",
        expect_memory=memory.id,
        expect_candidate="not-recommended",
        tags=["fixture", "inventory", "runner-host-path", "migration"],
    )
    ScenarioLibraryStore(root).add(scenario)
    warnings = init_git(root)
    return FixtureRepoReport(
        kind="inventory-migration",
        output=root,
        memory_id=memory.id,
        scenario_id=scenario.id,
        files=sorted(written + [".cmu/memories.json", ".cmu/scenarios.json"]),
        warnings=warnings,
    )


def write_fixture_file(root: Path, relative: str, content: str, written: list[str]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(relative)


def init_git(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "init"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return [f"git init unavailable: {error}"]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return [f"git init failed: {detail}"]
    return []
