from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .agent_api import AgentIntegration
from .codex_adapter import codex_runner_report
from .mcp import MCP_SERVER_NAME, mcp_tool_definitions
from .models import utc_now
from .openai_adapter import openai_runner_report
from .setup import setup_guide


HOST_SETUP_MANIFEST_VERSION = "cmu-host-setup-manifest/v1"
HOSTS = {"codex", "openai", "mcp", "all"}


@dataclass(frozen=True)
class HostSetupManifestReport:
    root: str
    host: str
    output: Path
    wrote: bool
    tools: list[str]
    commands: list[str]

    def render(self) -> str:
        return "\n".join(
            [
                "CMU Host Setup Manifest",
                f"Version: {HOST_SETUP_MANIFEST_VERSION}",
                "Mode: machine-readable IDE/coding-agent setup contract generated from live CMU host surfaces.",
                f"Host: {self.host}",
                f"Root: {self.root}",
                f"Output: {self.output}",
                f"Wrote: {'yes' if self.wrote else 'no'}",
                f"Tools: {', '.join(self.tools)}",
                f"Commands: {', '.join(self.commands)}",
                "",
                "Proof Meaning: host and IDE setup can now consume a structured manifest rather than retyping setup-guide prose or duplicating adapter contracts.",
            ]
        )


def host_setup_manifest(
    root: Path | str,
    *,
    host: str = "all",
    output: Path | str = ".cmu/host_setup_manifest.json",
    write: bool = False,
) -> HostSetupManifestReport:
    normalized = host.strip().lower() or "all"
    if normalized not in HOSTS:
        raise ValueError(f"unknown host setup manifest host: {host}")
    root_path = Path(root)
    setup = setup_guide(root_path, host="all" if normalized in {"all", "openai"} else normalized)
    payload = {
        "schema": HOST_SETUP_MANIFEST_VERSION,
        "created_at": utc_now(),
        "root": str(root_path),
        "host": normalized,
        "read_only": True,
        "mcp": {
            "server_name": MCP_SERVER_NAME,
            "command": "cmu-mcp",
            "args": ["--root", str(root_path)],
            "tools": mcp_tool_definitions(),
        },
        "agent_api": AgentIntegration(root_path).manifest(),
        "setup_guide": {
            "version": setup.agent_api_version,
            "project_initialized": setup.status.initialized,
            "git_repository": setup.status.git_repository,
        },
        "adapters": adapter_payloads(root_path, normalized),
    }
    output_path = Path(output)
    target = output_path if output_path.is_absolute() else root_path / output_path
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tools = [tool["name"] for tool in payload["mcp"]["tools"]]
    commands = ["cmu-mcp", "python -m cmu --root <project-root> mcp"]
    if normalized in {"all", "codex"}:
        commands.append("cmu codex-runner --input-file <event.json>")
    if normalized in {"all", "openai"}:
        commands.append("cmu openai-runner --input-file <event.json>")
    return HostSetupManifestReport(
        root=str(root_path),
        host=normalized,
        output=target,
        wrote=write,
        tools=tools,
        commands=commands,
    )


def adapter_payloads(root: Path, host: str) -> dict:
    adapters = {}
    if host in {"all", "codex"}:
        adapters["codex"] = codex_runner_report(root).manifest
    if host in {"all", "openai"}:
        adapters["openai"] = openai_runner_report(root).manifest
    return adapters
