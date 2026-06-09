from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Memory, MemoryScope, MemoryType
from .scenarios import ScenarioDefinition, ScenarioLibraryStore
from .store import MemoryStore


FIXTURE_REPO_VERSION = "cmu-fixture-repo/v1"
FIXTURE_KINDS = {"checkout-release"}


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
