from __future__ import annotations

import importlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_api import AGENT_API_VERSION
from .mcp import MCP_SERVER_NAME, mcp_tool_definitions
from .setup import setup_guide


INSTALL_CHECK_VERSION = "cmu-install-check/v1"
REQUIRED_SCRIPTS = {"cmu": "cmu.cli:main", "cmu-mcp": "cmu.mcp:main"}
REQUIRED_README_COMMANDS = [
    "python -m pip install -e .",
    "cmu init",
    "cmu readiness",
    "cmu quickstart-demo",
    "cmu quickstart-demo --apply",
    "cmu demo-walkthrough",
    "cmu setup-guide --host all",
    "cmu install-check",
    "cmu dist-check",
]


@dataclass(frozen=True)
class InstallCheckItem:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class InstallCheckReport:
    root: str
    items: list[InstallCheckItem]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Install Check",
            f"Version: {INSTALL_CHECK_VERSION}",
            f"Root: {self.root}",
            f"Status: {'pass' if self.passed else 'fail'}",
            "Mode: read-only packaging and adoption validation; no stores, memories, receipts, or Git checkpoints are mutated.",
            "",
            "Checks:",
        ]
        for item in self.items:
            marker = "pass" if item.passed else "fail"
            lines.append(f"- [{marker}] {item.name}: {item.detail}")
        lines.extend(
            [
                "",
                "Proof Meaning: install-check validates the checkout's README, package metadata, SDK import, "
                "module entrypoint, setup-guide consistency, and MCP schema against live CMU code.",
            ]
        )
        return "\n".join(lines)


def install_check(root: Path | str = ".") -> InstallCheckReport:
    root_path = Path(root)
    pyproject_path = root_path / "pyproject.toml"
    readme_path = root_path / "README.md"
    pyproject = read_pyproject(pyproject_path)
    readme = read_text(readme_path)
    setup = setup_guide(root_path, host="all")
    items = [
        check_readme_exists(readme_path, readme),
        check_readme_commands(readme),
        check_pyproject_readme(pyproject),
        check_build_system(pyproject),
        check_project_scripts(pyproject, setup.status.pyproject_scripts),
        check_package_discovery(pyproject),
        check_sdk_import(),
        check_module_entrypoint(),
        check_setup_guide_consistency(setup.status.pyproject_scripts),
        check_mcp_schema(readme),
    ]
    return InstallCheckReport(root=str(root_path), items=items)


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


def check_readme_exists(path: Path, readme: str) -> InstallCheckItem:
    return InstallCheckItem(
        "README.md",
        path.exists() and bool(readme.strip()),
        "present and non-empty" if path.exists() and readme.strip() else "missing or empty",
    )


def check_readme_commands(readme: str) -> InstallCheckItem:
    missing = [command for command in REQUIRED_README_COMMANDS if command not in readme]
    return InstallCheckItem(
        "README quickstart commands",
        not missing,
        "all required adoption commands present" if not missing else "missing: " + ", ".join(missing),
    )


def check_pyproject_readme(pyproject: dict[str, Any]) -> InstallCheckItem:
    readme = pyproject.get("project", {}).get("readme")
    return InstallCheckItem(
        "pyproject README binding",
        readme == "README.md",
        "project.readme points to README.md" if readme == "README.md" else f"project.readme is {readme!r}",
    )


def check_build_system(pyproject: dict[str, Any]) -> InstallCheckItem:
    build_system = pyproject.get("build-system", {})
    backend = build_system.get("build-backend")
    requires = build_system.get("requires", [])
    passed = backend == "setuptools.build_meta" and "setuptools>=68" in requires
    return InstallCheckItem(
        "build backend",
        passed,
        "setuptools build backend declared" if passed else f"backend={backend!r}, requires={requires!r}",
    )


def check_project_scripts(pyproject: dict[str, Any], setup_scripts: dict[str, str]) -> InstallCheckItem:
    scripts = pyproject.get("project", {}).get("scripts", {})
    passed = scripts == REQUIRED_SCRIPTS and setup_scripts == REQUIRED_SCRIPTS
    return InstallCheckItem(
        "console scripts",
        passed,
        "cmu and cmu-mcp entrypoints match setup-guide"
        if passed
        else f"pyproject={scripts!r}, setup-guide={setup_scripts!r}",
    )


def check_package_discovery(pyproject: dict[str, Any]) -> InstallCheckItem:
    include = (
        pyproject.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
        .get("include")
    )
    return InstallCheckItem(
        "package discovery",
        include == ["cmu*"],
        "setuptools finds cmu packages" if include == ["cmu*"] else f"include={include!r}",
    )


def check_sdk_import() -> InstallCheckItem:
    try:
        module = importlib.import_module("cmu")
        facade = getattr(module, "CentralMemoryUnit", None)
    except (ImportError, AttributeError):
        facade = None
    return InstallCheckItem(
        "SDK import",
        facade is not None,
        "from cmu import CentralMemoryUnit is available" if facade is not None else "CentralMemoryUnit import failed",
    )


def check_module_entrypoint() -> InstallCheckItem:
    try:
        module = importlib.import_module("cmu.__main__")
        has_main = hasattr(module, "main")
    except ImportError:
        has_main = False
    return InstallCheckItem(
        "module entrypoint",
        has_main,
        "python -m cmu entrypoint is importable" if has_main else "cmu.__main__.main is unavailable",
    )


def check_setup_guide_consistency(setup_scripts: dict[str, str]) -> InstallCheckItem:
    passed = setup_scripts == REQUIRED_SCRIPTS and AGENT_API_VERSION == "cmu-agent-tools/v1"
    return InstallCheckItem(
        "setup-guide consistency",
        passed,
        f"scripts and AgentIntegration {AGENT_API_VERSION} are consistent"
        if passed
        else f"scripts={setup_scripts!r}, agent_api={AGENT_API_VERSION}",
    )


def check_mcp_schema(readme: str) -> InstallCheckItem:
    tools = [tool["name"] for tool in mcp_tool_definitions()]
    missing_in_readme = [tool for tool in tools if tool not in readme]
    passed = MCP_SERVER_NAME in readme and not missing_in_readme and tools == [
        "cmu_task_start",
        "cmu_after_work",
        "cmu_link_checkpoint",
        "cmu_review",
    ]
    detail = (
        f"{MCP_SERVER_NAME} exposes {', '.join(tools)}"
        if passed
        else f"server_in_readme={MCP_SERVER_NAME in readme}, missing_tools={missing_in_readme}, tools={tools}"
    )
    return InstallCheckItem("MCP schema", passed, detail)
