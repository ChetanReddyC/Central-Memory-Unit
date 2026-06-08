from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .agent_api import AGENT_API_VERSION, AGENT_TOOL_DEFINITIONS, AgentIntegration


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_NAME = "central-memory-unit"
MCP_SERVER_VERSION = "0.1.0"


def mcp_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": definition.name,
            "description": definition.description,
            "inputSchema": input_schema_for(definition.name),
            "annotations": {
                "readOnlyHint": not definition.mutates,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        }
        for definition in AGENT_TOOL_DEFINITIONS
    ]


def input_schema_for(tool_name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "cmu_task_start": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "actor": {"type": "string", "default": "agent"},
                "area": {"type": "string"},
                "files": string_array(),
                "workflow": string_array(),
                "environment": string_array(),
                "risk": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                "repeated_error": {"type": "boolean", "default": False},
                "uncertainty": {"type": "boolean", "default": False},
                "shared_contract": {"type": "boolean", "default": False},
                "irreversible": {"type": "boolean", "default": False},
                "unfamiliar": {"type": "boolean", "default": False},
                "semantic": {"type": "string", "enum": ["off", "local"], "default": "off"},
            },
        },
        "cmu_after_work": {
            "type": "object",
            "additionalProperties": False,
            "required": ["situation", "future_use", "scope"],
            "properties": {
                "situation": {"type": "string", "minLength": 1},
                "future_use": {"type": "string", "minLength": 1},
                "scope": scope_schema(),
                "title": {"type": "string"},
                "signals": string_array(),
                "outcome": {"type": "string"},
                "worked": {"type": "string"},
                "failed": {"type": "string"},
                "evidence": string_array(),
                "liability_score": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
                "suggested_next_type": {
                    "type": "string",
                    "enum": ["candidate", "situation", "anchor", "practice", "exception", "anti-pattern", "question"],
                    "default": "situation",
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.6},
            },
        },
        "cmu_link_checkpoint": {
            "type": "object",
            "additionalProperties": False,
            "required": ["use_id"],
            "properties": {
                "use_id": {"type": "string", "minLength": 1},
                "commit_ref": {"type": "string", "default": "HEAD"},
                "note": {"type": "string"},
                "manual_commit": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["hash"],
                    "properties": {
                        "hash": {"type": "string", "minLength": 1},
                        "message": {"type": "string"},
                        "files": string_array(),
                        "time": {"type": "string"},
                    },
                },
            },
        },
        "cmu_review": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "memory_id": {"type": "string"},
            },
        },
    }
    return schemas[tool_name]


def string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "default": []}


def scope_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ownership": string_array(),
            "code": string_array(),
            "workflow": string_array(),
            "environment": string_array(),
            "actor": string_array(),
            "time": string_array(),
        },
    }


class CmuMcpAdapter:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.integration = AgentIntegration(self.root)

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if request_id is None:
            return None
        if not isinstance(method, str):
            return json_rpc_error(request_id, -32600, "JSON-RPC request method must be a string.")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return json_rpc_error(request_id, -32602, "JSON-RPC params must be an object.")
        try:
            if method == "initialize":
                return json_rpc_result(request_id, self.initialize_result())
            if method == "tools/list":
                return json_rpc_result(request_id, {"tools": mcp_tool_definitions()})
            if method == "tools/call":
                return json_rpc_result(request_id, self.call_tool(params))
            if method == "ping":
                return json_rpc_result(request_id, {})
        except Exception as error:
            return json_rpc_error(request_id, -32603, f"CMU MCP adapter error: {error}")
        return json_rpc_error(request_id, -32601, f"Unknown MCP method: {method}")

    def initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
            "instructions": (
                "Use CMU tools at task start, after reusable learning, after checkpoints, "
                "and for read-only usefulness/drag review. CMU MCP delegates memory behavior "
                f"to AgentIntegration {AGENT_API_VERSION}."
            ),
        }

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            response = agent_style_error("cmu_unknown", "invalid-request", "tools/call requires a non-empty string name.")
        elif arguments is None:
            response = self.invoke_agent_tool(name, {})
        elif not isinstance(arguments, dict):
            response = agent_style_error(name, "invalid-request", "tools/call arguments must be a JSON object.")
        else:
            response = self.invoke_agent_tool(name, arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(response, indent=2, ensure_ascii=True),
                }
            ],
            "structuredContent": response,
            "isError": not response.get("ok", False),
        }

    def invoke_agent_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.integration.invoke(name, arguments)
        except OSError as error:
            return agent_style_error(name, "store-error", f"CMU store/root error: {error}")


def json_rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def agent_style_error(tool: str, status: str, error: str) -> dict[str, Any]:
    return {
        "api_version": AGENT_API_VERSION,
        "tool": tool,
        "ok": False,
        "status": status,
        "error": error,
        "available_tools": [definition.name for definition in AGENT_TOOL_DEFINITIONS],
    }


class StdioMcpServer:
    def __init__(self, adapter: CmuMcpAdapter, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
        self.adapter = adapter
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout

    def serve_forever(self) -> int:
        while True:
            raw_message = self.read_message()
            if raw_message is None:
                return 0
            try:
                request = json.loads(raw_message)
            except json.JSONDecodeError as error:
                self.write_message(json_rpc_error(None, -32700, f"Malformed JSON request: {error.msg}"))
                continue
            if not isinstance(request, dict):
                self.write_message(json_rpc_error(None, -32600, "JSON-RPC message must be an object."))
                continue
            response = self.adapter.handle_request(request)
            if response is not None:
                self.write_message(response)

    def read_message(self) -> str | None:
        headers: dict[str, str] = {}
        while True:
            line = self.input_stream.buffer.readline()
            if line == b"":
                return None
            if line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("utf-8").strip()
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.lower()] = value.strip()
        length_text = headers.get("content-length")
        if not length_text:
            return None
        length = int(length_text)
        return self.input_stream.buffer.read(length).decode("utf-8")

    def write_message(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=True).encode("utf-8")
        self.output_stream.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii"))
        self.output_stream.buffer.write(encoded)
        self.output_stream.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cmu-mcp", description="Central Memory Unit MCP stdio server.")
    parser.add_argument("--root", default=".", help="Project root containing the .cmu store. Defaults to the current working directory.")
    args = parser.parse_args(argv)
    return StdioMcpServer(CmuMcpAdapter(args.root)).serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
