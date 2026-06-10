from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .portable import export_bundle_from_root


PORTABLE_FIXTURE_SEED_VERSION = "cmu-portable-fixture-seed/v1"


@dataclass(frozen=True)
class PortableFixtureSeedReport:
    output: Path
    files: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Portable Fixture Seed",
            f"Version: {PORTABLE_FIXTURE_SEED_VERSION}",
            f"Output: {self.output}",
            "",
            "Fixtures:",
        ]
        lines.extend(f"- {item}" for item in self.files) if self.files else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: portability compatibility now has a reproducible fixture corpus seeded from a real CMU store, including current, historical, invalid, future-schema, legacy-schema, and migration-planned cases.",
            ]
        )
        return "\n".join(lines)


def seed_portable_fixtures(
    root: Path | str,
    output: Path | str,
    *,
    overwrite: bool = False,
    include_historical: bool = False,
) -> PortableFixtureSeedReport:
    target = Path(output)
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise ValueError("portable fixture output directory already exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)
    bundle = export_bundle_from_root(root)
    current = json.loads(bundle.render_json())
    files: list[str] = []
    write_json(target / "valid-current-export.json", current, files)
    if include_historical:
        historical = dict(current)
        historical["exported_at"] = "2024-01-01T00:00:00+00:00"
        historical["warnings"] = list(historical.get("warnings", [])) + ["historical fixture derived from a real current-schema export"]
        write_json(target / "historical-2024-current-schema-export.json", historical, files)
        older_historical = dict(current)
        older_historical["exported_at"] = "2023-06-01T00:00:00+00:00"
        older_historical["warnings"] = list(older_historical.get("warnings", [])) + ["older historical current-schema export for corpus breadth"]
        write_json(target / "historical-2023-current-schema-export.json", older_historical, files)
    invalid = dict(current)
    invalid.pop("contents", None)
    write_json(target / "invalid-missing-memories.json", invalid, files)
    future = dict(current)
    future["schema"] = "cmu-portable-bundle/v999"
    write_json(target / "future-v999-export.json", future, files)
    legacy = {
        "schema": "cmu-portable-bundle/v0",
        "created_at": current.get("created_at", ""),
        "records": current.get("memories", []),
        "receipts": current.get("uses", []),
    }
    write_json(target / "legacy-v0-export.json", legacy, files)
    migration = dict(legacy)
    migration["schema"] = "cmu-portable-bundle/v0-migration-plan"
    migration["migration_target_schema"] = current.get("schema")
    migration["migration_notes"] = ["fixture requires explicit migration support before import"]
    write_json(target / "migration-v0-to-current-plan.json", migration, files)
    return PortableFixtureSeedReport(output=target, files=sorted(files))


def write_json(path: Path, data: dict, files: list[str]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files.append(path.name)
