from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .agent_api import AGENT_API_VERSION, AgentIntegration
from .mcp import MCP_SERVER_NAME, mcp_tool_definitions


SETUP_GUIDE_VERSION = "cmu-setup-guide/v1"
HOST_CHOICES = ["all", "cli", "python-sdk", "mcp", "codex"]


@dataclass(frozen=True)
class SetupStatus:
    root: str
    cmu_store: bool
    memories_file: bool
    uses_file: bool
    git_repository: bool
    pyproject_scripts: dict[str, str] = field(default_factory=dict)

    @property
    def initialized(self) -> bool:
        return self.cmu_store and self.memories_file and self.uses_file

    @property
    def quickstart_apply_ready(self) -> bool:
        return self.git_repository


@dataclass(frozen=True)
class SetupSection:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class SetupGuideReport:
    host: str
    status: SetupStatus
    agent_api_version: str
    agent_tools: list[str]
    mcp_tools: list[str]
    sections: list[SetupSection]

    def render(self) -> str:
        lines = [
            "CMU Setup Guide",
            f"Version: {SETUP_GUIDE_VERSION}",
            f"Host: {self.host}",
            "Mode: read-only host integration and packaging guidance; "
            "no stores, memories, receipts, or Git checkpoints are mutated.",
            "",
            "Project Checks:",
            f"- Root: {self.status.root}",
            f"- CMU Store Initialized: {'yes' if self.status.initialized else 'no'}",
            f"- memories.json: {'yes' if self.status.memories_file else 'no'}",
            f"- uses.json: {'yes' if self.status.uses_file else 'no'}",
            f"- Git Repository: {'yes' if self.status.git_repository else 'no'}",
            f"- Quickstart Apply Ready: {'yes' if self.status.quickstart_apply_ready else 'no'}",
        ]
        if self.status.pyproject_scripts:
            scripts = ", ".join(f"{name}={target}" for name, target in sorted(self.status.pyproject_scripts.items()))
            lines.append(f"- Project Scripts: {scripts}")
        lines.extend(
            [
                "",
                f"Agent Boundary: {self.agent_api_version}",
                f"Agent Tools: {', '.join(self.agent_tools)}",
                f"MCP Tools: {', '.join(self.mcp_tools)}",
            ]
        )
        for section in self.sections:
            lines.extend(["", section.title + ":"])
            lines.extend(f"- {line}" for line in section.lines)
        lines.extend(
            [
                "",
                "Proof Meaning: this guide is generated from CMU's real CLI/package state, "
                "AgentIntegration manifest, and MCP tool schemas so host setup docs cannot drift "
                "silently from the integration boundary.",
            ]
        )
        return "\n".join(lines)


def setup_guide(root: Path | str = ".", *, host: str = "all") -> SetupGuideReport:
    if host not in HOST_CHOICES:
        raise ValueError(f"unknown setup-guide host: {host}")
    root_path = Path(root)
    status = inspect_setup_status(root_path)
    agent_manifest = AgentIntegration(root_path).manifest()
    agent_tools = [tool["name"] for tool in agent_manifest["tools"]]
    mcp_tools = [tool["name"] for tool in mcp_tool_definitions()]
    sections = setup_sections(root_path, host, status)
    return SetupGuideReport(
        host=host,
        status=status,
        agent_api_version=agent_manifest["api_version"],
        agent_tools=agent_tools,
        mcp_tools=mcp_tools,
        sections=sections,
    )


def inspect_setup_status(root: Path) -> SetupStatus:
    cmu_dir = root / ".cmu"
    return SetupStatus(
        root=str(root),
        cmu_store=cmu_dir.exists() and cmu_dir.is_dir(),
        memories_file=(cmu_dir / "memories.json").exists(),
        uses_file=(cmu_dir / "uses.json").exists(),
        git_repository=is_git_repository(root),
        pyproject_scripts=read_project_scripts(root / "pyproject.toml"),
    )


def setup_sections(root: Path, host: str, status: SetupStatus) -> list[SetupSection]:
    sections: list[SetupSection] = []
    selected = set(HOST_CHOICES[1:] if host == "all" else [host])
    if "cli" in selected:
        sections.append(cli_section(status))
    if "python-sdk" in selected:
        sections.append(python_sdk_section(root))
    if "mcp" in selected:
        sections.append(mcp_section(root))
    if "codex" in selected:
        sections.append(codex_section(root))
    sections.append(workflow_section(status))
    return sections


def cli_section(status: SetupStatus) -> SetupSection:
    lines = [
        "Run `cmu init` once for a new project root."
        if not status.initialized
        else "CMU store files are present; `cmu init` is optional."
    ]
    lines.extend(
        [
            "Use `cmu readiness` before cleanup or seeding work.",
            "Use `cmu start --actor agent --area <area> --file <path> <task>` before meaningful work.",
            "Use `cmu remember` only after reusable situational intelligence appears.",
            "Use `cmu quickstart-demo` for a dry-run proof loop.",
        ]
    )
    if status.quickstart_apply_ready:
        lines.append("Use `cmu quickstart-demo --apply` when you want a Git-backed receipt/checkpoint proof.")
    else:
        lines.append("Create or enter a Git repository before `cmu quickstart-demo --apply`.")
    return SetupSection("CLI Setup", lines)


def python_sdk_section(root: Path) -> SetupSection:
    return SetupSection(
        "Python SDK Setup",
        [
            "Import with `from cmu import CentralMemoryUnit`.",
            f"Create the facade with `cmu = CentralMemoryUnit(root={str(root)!r})`.",
            "Call `cmu.task_start(...)`, do the work, call `cmu.after_work(...)` only for reusable learning, "
            "then call `cmu.link_checkpoint(...)` and `cmu.review(...)`.",
            f"The SDK delegates to AgentIntegration `{AGENT_API_VERSION}` rather than reimplementing task-start, receipt, or review behavior.",
        ],
    )


def mcp_section(root: Path) -> SetupSection:
    return SetupSection(
        "MCP Host Setup",
        [
            "Configure command `cmu-mcp` with args `['--root', '<project-root>']` when the package script is installed.",
            f"For this root use args `['--root', {str(root)!r}]`.",
            "Fallback command during local development: `py -m cmu --root <project-root> mcp`.",
            f"Expected server name: `{MCP_SERVER_NAME}`.",
            "Expected tools: " + ", ".join(tool["name"] for tool in mcp_tool_definitions()) + ".",
        ],
    )


def codex_section(root: Path) -> SetupSection:
    return SetupSection(
        "Codex MCP Setup",
        [
            "Add a project-scoped MCP server named `central-memory-unit`.",
            "Use command `cmu-mcp` after installation, or `py` with args "
            "`['-m', 'cmu', '--root', '<project-root>', 'mcp']` during local development.",
            f"Set the project root argument to `{root}`.",
            "Keep CMU calls task-bound: call task-start before meaningful work, after-work only for reusable learning, "
            "checkpoint linking after commits, and review for evidence.",
        ],
    )


def workflow_section(status: SetupStatus) -> SetupSection:
    next_step = (
        "run `cmu readiness` and then integrate a host"
        if status.initialized
        else "run `cmu init` before integrating a host"
    )
    if status.quickstart_apply_ready:
        next_step = "run `cmu quickstart-demo --apply` to prove the receipt/checkpoint loop, then integrate a host"
    return SetupSection(
        "Recommended Next Step",
        [
            next_step,
            "Prefer MCP or the Python SDK over parsing human CLI output in autonomous runtimes.",
            "Keep Practice and Anchor authority explicit; setup guidance does not approve stable memory.",
        ],
    )


def read_project_scripts(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict):
        return {}
    return {str(name): str(target) for name, target in scripts.items()}


def is_git_repository(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0
