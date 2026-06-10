from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dist_check import DIST_CHECK_VERSION
from .install_check import INSTALL_CHECK_VERSION, REQUIRED_SCRIPTS


PUBLISH_CHECK_VERSION = "cmu-publish-check/v1"


@dataclass(frozen=True)
class PublishCheckItem:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PublishCheckReport:
    root: str
    items: list[PublishCheckItem]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Publish Check",
            f"Version: {PUBLISH_CHECK_VERSION}",
            f"Root: {self.root}",
            f"Status: {'pass' if self.passed else 'fail'}",
            "Mode: read-only package publication workflow validation; no build, upload, store, receipt, or Git mutation is performed.",
            "",
            "Checks:",
        ]
        for item in self.items:
            marker = "pass" if item.passed else "fail"
            lines.append(f"- [{marker}] {item.name}: {item.detail}")
        lines.extend(
            [
                "",
                "Proof Meaning: publish-check validates the metadata, commands, and local gate sequence needed before a real package publication workflow.",
            ]
        )
        return "\n".join(lines)


def publish_check(root: Path | str = ".") -> PublishCheckReport:
    root_path = Path(root)
    pyproject = read_pyproject(root_path / "pyproject.toml")
    readme = read_text(root_path / "README.md")
    items = [
        check_project_identity(pyproject),
        check_version(pyproject),
        check_scripts(pyproject),
        check_readme_publish_guidance(readme),
        check_local_gates(readme),
        check_dist_check_linkage(),
    ]
    return PublishCheckReport(root=str(root_path), items=items)


def read_pyproject(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def check_project_identity(pyproject: dict[str, Any]) -> PublishCheckItem:
    project = pyproject.get("project", {})
    name = project.get("name", "")
    description = project.get("description", "")
    passed = bool(name and description and project.get("readme") == "README.md")
    return PublishCheckItem(
        "project identity",
        passed,
        f"name={name}, readme=README.md, description present" if passed else f"name={name!r}, readme={project.get('readme')!r}, description={bool(description)}",
    )


def check_version(pyproject: dict[str, Any]) -> PublishCheckItem:
    version = pyproject.get("project", {}).get("version", "")
    passed = bool(re.fullmatch(r"\d+\.\d+\.\d+", str(version)))
    return PublishCheckItem(
        "publishable version",
        passed,
        f"version {version} uses static semver" if passed else f"version={version!r} is not static semver",
    )


def check_scripts(pyproject: dict[str, Any]) -> PublishCheckItem:
    scripts = pyproject.get("project", {}).get("scripts", {})
    passed = scripts == REQUIRED_SCRIPTS
    return PublishCheckItem(
        "published console scripts",
        passed,
        "cmu and cmu-mcp scripts are declared" if passed else f"scripts={scripts!r}",
    )


def check_readme_publish_guidance(readme: str) -> PublishCheckItem:
    required = ["cmu publish-check", "cmu install-check", "cmu dist-check"]
    missing = [item for item in required if item not in readme]
    return PublishCheckItem(
        "README publish workflow",
        not missing,
        "README names publish-check plus local gates" if not missing else "missing: " + ", ".join(missing),
    )


def check_local_gates(readme: str) -> PublishCheckItem:
    required = ["python -m unittest", "cmu install-check", "cmu dist-check"]
    missing = [item for item in required if item not in readme]
    return PublishCheckItem(
        "pre-publish local gates",
        not missing,
        "tests, install-check, and dist-check are documented as gates" if not missing else "missing: " + ", ".join(missing),
    )


def check_dist_check_linkage() -> PublishCheckItem:
    passed = DIST_CHECK_VERSION == "cmu-dist-check/v1" and INSTALL_CHECK_VERSION == "cmu-install-check/v1"
    return PublishCheckItem(
        "local validation linkage",
        passed,
        f"publish workflow references {INSTALL_CHECK_VERSION} and {DIST_CHECK_VERSION}" if passed else "install/dist check versions changed unexpectedly",
    )
