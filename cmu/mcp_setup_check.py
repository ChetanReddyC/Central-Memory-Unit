from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .mcp import MCP_SERVER_NAME, mcp_tool_definitions


MCP_SETUP_CHECK_VERSION = "cmu-mcp-setup-check/v1"


@dataclass(frozen=True)
class McpSetupCheck:
    name: str
    passed: bool
    detail: str

    def render(self) -> str:
        return f"- {self.name}: {'pass' if self.passed else 'fail'} ({self.detail})"


@dataclass(frozen=True)
class McpSetupCheckReport:
    root: str
    host: str
    config: Path | None
    checks: list[McpSetupCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def render(self) -> str:
        lines = [
            "CMU MCP Setup Check",
            f"Version: {MCP_SETUP_CHECK_VERSION}",
            "Mode: read-only host MCP configuration validation against live CMU MCP tool definitions.",
            f"Host: {self.host}",
            f"Root: {self.root}",
            f"Config: {self.config if self.config else '<generated expectation>'}",
            f"Status: {'pass' if self.passed else 'review'}",
            "",
            "Checks:",
        ]
        lines.extend(check.render() for check in self.checks)
        lines.extend(
            [
                "",
                "Proof Meaning: host-specific MCP setup can now be validated as configuration, not only described in a manifest.",
            ]
        )
        return "\n".join(lines)


def mcp_setup_check(root: Path | str, *, host: str = "codex", config: Path | str | None = None) -> McpSetupCheckReport:
    root_path = Path(root)
    normalized = host.strip().lower() or "codex"
    if normalized not in {"codex", "vscode", "generic"}:
        raise ValueError(f"unknown MCP setup host: {host}")
    config_path = Path(config) if config else None
    if config_path is not None and not config_path.is_absolute() and not config_path.exists():
        config_path = root_path / config_path
    payload = expected_config(root_path, normalized) if config_path is None else json.loads(config_path.read_text(encoding="utf-8-sig"))
    checks = [
        McpSetupCheck("server-name", has_server(payload), f"expected {MCP_SERVER_NAME}"),
        McpSetupCheck("command", has_command(payload), "expected cmu-mcp or python -m cmu mcp"),
        McpSetupCheck("root-arg", has_root_arg(payload, root_path), f"expected --root {root_path}"),
        McpSetupCheck("tools", bool(mcp_tool_definitions()), f"{len(mcp_tool_definitions())} live tools available"),
    ]
    return McpSetupCheckReport(root=str(root_path), host=normalized, config=config_path, checks=checks)


def expected_config(root: Path, host: str) -> dict:
    server = {"command": "cmu-mcp", "args": ["--root", str(root)]}
    if host == "vscode":
        return {"mcp": {"servers": {MCP_SERVER_NAME: server}}}
    return {"mcpServers": {MCP_SERVER_NAME: server}}


def server_entries(payload: dict) -> list[dict]:
    candidates = [
        payload.get("mcpServers", {}),
        payload.get("servers", {}),
        payload.get("mcp", {}).get("servers", {}) if isinstance(payload.get("mcp"), dict) else {},
    ]
    entries = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            for name, config in candidate.items():
                if isinstance(config, dict):
                    item = dict(config)
                    item["_name"] = name
                    entries.append(item)
    return entries


def has_server(payload: dict) -> bool:
    return any(entry.get("_name") in {MCP_SERVER_NAME, "central-memory-unit"} for entry in server_entries(payload))


def has_command(payload: dict) -> bool:
    for entry in server_entries(payload):
        command = str(entry.get("command", ""))
        args = [str(item) for item in entry.get("args", []) if isinstance(item, str)]
        if command == "cmu-mcp":
            return True
        if command.endswith("python") and args[:3] == ["-m", "cmu", "mcp"]:
            return True
    return False


def has_root_arg(payload: dict, root: Path) -> bool:
    expected = str(root)
    for entry in server_entries(payload):
        args = [str(item) for item in entry.get("args", []) if isinstance(item, str)]
        if "--root" in args and expected in args:
            return True
    return False
