from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .portable import PORTABLE_BUNDLE_VERSION, load_portable_bundle, validate_portable_bundle


PORTABLE_COMPAT_VERSION = "cmu-portable-compat/v1"


@dataclass(frozen=True)
class PortableCompatFixture:
    path: Path
    expectation: str
    schema: str
    status: str
    reason: str

    def render(self, base: Path) -> str:
        rel = self.path.relative_to(base) if self.path.is_relative_to(base) else self.path
        return f"- [{self.status}] {rel}: expect={self.expectation}; schema={self.schema or 'missing'}; {self.reason}"


@dataclass
class PortableCompatReport:
    fixture_dir: Path
    fixtures: list[PortableCompatFixture] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors and all(fixture.status == "pass" for fixture in self.fixtures)

    def render(self) -> str:
        lines = [
            "CMU Portable Compatibility Fixtures",
            f"Version: {PORTABLE_COMPAT_VERSION}",
            f"Current Bundle Schema: {PORTABLE_BUNDLE_VERSION}",
            f"Fixture Directory: {self.fixture_dir}",
            f"Status: {'pass' if self.passed else 'fail'}",
            "",
            "Summary:",
            f"- Fixtures: {len(self.fixtures)}",
            f"- Passed: {sum(1 for fixture in self.fixtures if fixture.status == 'pass')}",
            f"- Failed: {sum(1 for fixture in self.fixtures if fixture.status == 'fail')}",
            f"- Errors: {len(self.errors)}",
            "",
            "Fixtures:",
        ]
        if not self.fixtures:
            lines.append("- None")
        else:
            lines.extend(fixture.render(self.fixture_dir) for fixture in self.fixtures)
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"- {error}" for error in self.errors)
        lines.extend(
            [
                "",
                "Proof Meaning: portable bundle compatibility is now checked against saved fixtures, including current valid bundles, historical current-schema exports, intentionally invalid bundles, future-schema bundles, legacy bundles, and migration-planned bundles that must fail safely.",
            ]
        )
        return "\n".join(lines)


def portable_compat_report(fixture_dir: Path | str) -> PortableCompatReport:
    root = Path(fixture_dir)
    report = PortableCompatReport(fixture_dir=root)
    if not root.exists():
        report.errors.append("fixture directory does not exist")
        return report
    if not root.is_dir():
        report.errors.append("fixture path is not a directory")
        return report
    paths = sorted(root.glob("*.json"))
    if not paths:
        report.errors.append("fixture directory has no JSON bundle fixtures")
        return report
    for path in paths:
        fixture = evaluate_fixture(path)
        report.fixtures.append(fixture)
        if fixture.status == "fail":
            report.errors.append(f"{path.name}: {fixture.reason}")
    return report


def evaluate_fixture(path: Path) -> PortableCompatFixture:
    expectation = expectation_for_path(path)
    try:
        bundle = load_portable_bundle(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return PortableCompatFixture(
            path=path,
            expectation=expectation,
            schema="",
            status="pass" if expectation == "invalid" else "fail",
            reason=f"JSON load failed: {error}",
        )
    schema = bundle.get("schema", "") if isinstance(bundle, dict) else ""
    validation = validate_portable_bundle(bundle)
    if expectation == "valid":
        if validation.valid and schema == PORTABLE_BUNDLE_VERSION:
            return PortableCompatFixture(path, expectation, schema, "pass", "current schema validates")
        return PortableCompatFixture(path, expectation, schema, "fail", "; ".join(validation.errors) or "valid fixture did not validate")
    if expectation == "historical":
        if validation.valid and schema == PORTABLE_BUNDLE_VERSION:
            return PortableCompatFixture(path, expectation, schema, "pass", "historical current-schema fixture still validates")
        return PortableCompatFixture(path, expectation, schema, "fail", "; ".join(validation.errors) or "historical fixture did not validate")
    if expectation == "invalid":
        if not validation.valid:
            return PortableCompatFixture(path, expectation, schema, "pass", "invalid fixture failed validation as expected")
        return PortableCompatFixture(path, expectation, schema, "fail", "invalid fixture unexpectedly validated")
    if expectation == "future":
        unsupported = any("unsupported schema" in error for error in validation.errors)
        if not validation.valid and unsupported and schema != PORTABLE_BUNDLE_VERSION:
            return PortableCompatFixture(path, expectation, schema, "pass", "future schema failed safely as unsupported")
        return PortableCompatFixture(path, expectation, schema, "fail", "future fixture did not fail solely through unsupported-schema validation")
    if expectation == "legacy":
        if not validation.valid and schema and schema != PORTABLE_BUNDLE_VERSION:
            return PortableCompatFixture(path, expectation, schema, "pass", "legacy schema fixture failed validation instead of importing silently")
        return PortableCompatFixture(path, expectation, schema, "fail", "legacy fixture did not fail safely")
    if expectation == "migration":
        migration_target = bundle.get("migration_target_schema", "") if isinstance(bundle, dict) else ""
        if not validation.valid and schema and schema != PORTABLE_BUNDLE_VERSION and migration_target == PORTABLE_BUNDLE_VERSION:
            return PortableCompatFixture(path, expectation, schema, "pass", "migration fixture failed safely pending explicit migration support")
        return PortableCompatFixture(path, expectation, schema, "fail", "migration fixture did not fail safely with a current-schema target")
    return PortableCompatFixture(path, expectation, schema, "fail", "fixture filename must start with valid-, historical-, invalid-, legacy-, migration-, or future-")


def expectation_for_path(path: Path) -> str:
    name = path.name.lower()
    for expectation in ["valid", "historical", "invalid", "future", "legacy", "migration"]:
        if name.startswith(f"{expectation}-"):
            return expectation
    return "unknown"
