from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI


SYSTEM_PROMPT = """You are a local terminal agent connected to MCP tools for RedNote/Xiaohongshu.

Rules:
1. Use MCP tools for RedNote content, search, and note operations.
2. Be concise and helpful.
3. If a tool fails, explain clearly and continue if possible.
4. Never invent tool results.
5. Synthesize results into a direct answer.
"""


@dataclass
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


def _build_azure_openai_base_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return endpoint + "/"
    return endpoint + "/openai/v1/"


def _should_send_temperature(model_name: str) -> bool:
    return not model_name.strip().lower().startswith("gpt-5")




class RedNoteMCPClient:
    def __init__(self, command: str, args: list[str]) -> None:
        self.command = command
        self.args = args
        self.exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        resolved_command = shutil.which(self.command) if self.command == "npx" else self.command
        if not resolved_command:
            raise RuntimeError(
                f"Cannot find command '{self.command}'. Install Node.js/npm and ensure '{self.command}' is on PATH."
            )

        server_params = StdioServerParameters(
            command=resolved_command,
            args=self.args,
            env=os.environ.copy(),
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        read_stream, write_stream = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()

    async def close(self) -> None:
        await self.exit_stack.aclose()
        self.session = None

    async def list_tools(self) -> list[MCPToolInfo]:
        if not self.session:
            raise RuntimeError("MCP session is not initialized")

        response = await self.session.list_tools()
        raw_tools = self._extract_tools(response)
        tools: list[MCPToolInfo] = []
        for tool in raw_tools:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", "") or ""
            input_schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {
                "type": "object",
                "properties": {},
            }
            if name:
                tools.append(MCPToolInfo(name=name, description=description, input_schema=input_schema))
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self.session:
            raise RuntimeError("MCP session is not initialized")
        return await self.session.call_tool(tool_name, arguments)

    @staticmethod
    def _extract_tools(response: Any) -> list[Any]:
        if response is None:
            return []
        if hasattr(response, "tools"):
            return list(response.tools)
        return []


def load_config() -> dict[str, Any]:
    load_dotenv()

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()

    if not endpoint:
        raise RuntimeError("Missing AZURE_OPENAI_ENDPOINT in .env")
    if not api_key:
        raise RuntimeError("Missing AZURE_OPENAI_API_KEY in .env")
    if not deployment:
        raise RuntimeError("Missing AZURE_OPENAI_DEPLOYMENT in .env")

    command = os.getenv("MCP_COMMAND", "npx").strip() or "npx"
    args_raw = os.getenv("MCP_ARGS", "rednote-mcp --stdio").strip()
    args = args_raw.split()
    if not args:
        raise RuntimeError("MCP_ARGS cannot be empty")

    temperature = float(os.getenv("AGENT_TEMPERATURE", "0.2"))
    max_rounds = int(os.getenv("MAX_TOOL_ROUNDS", "8"))
    extra_system_prompt = os.getenv("EXTRA_SYSTEM_PROMPT", "").strip()

    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "deployment": deployment,
        "command": command,
        "args": args,
        "temperature": temperature,
        "max_rounds": max_rounds,
        "extra_system_prompt": extra_system_prompt,
    }


def build_openai_client(endpoint: str, api_key: str) -> AsyncOpenAI:
    base_url = _build_azure_openai_base_url(endpoint)
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _clean_json_value(value: Any) -> Any:
    """Clean JSON values to remove invalid surrogate characters."""
    if isinstance(value, str):
        # Remove invalid surrogate characters using regex
        import re
        # Match and remove lone surrogate characters (D800-DFFF)
        cleaned = re.sub(r'[\uD800-\uDFFF]', '', value)
        # Also handle potential surrogate pairs that might have been split
        return cleaned
    elif isinstance(value, dict):
        return {k: _clean_json_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_clean_json_value(item) for item in value]
    return value


def mcp_tools_to_openai_tools(mcp_tools: list[MCPToolInfo]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in mcp_tools:
        schema = tool.input_schema or {"type": "object", "properties": {}}
        schema = _clean_json_value(schema)
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}, "additionalProperties": True}

        description = tool.description or f"MCP tool: {tool.name}"

        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    return result


def stringify_tool_result(result: Any) -> str:
    if result is None:
        return "null"
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if content is not None:
        try:
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(str(text))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
            return "\n".join(parts)
        except Exception:
            pass

    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(result)


async def run_agent() -> None:
    cfg = load_config()

    print("[1/4] Connecting to rednote-mcp...")
    mcp_client = RedNoteMCPClient(cfg["command"], cfg["args"])
    await mcp_client.connect()

    try:
        print("[2/4] Reading available MCP tools...")
        mcp_tools = await mcp_client.list_tools()
        if not mcp_tools:
            print("No MCP tools were exposed by the server. Check whether rednote-mcp started correctly.")
            return

        print("Available tools:")
        for tool in mcp_tools:
            print(f"  - {tool.name}")

        print("[3/4] Connecting to Azure OpenAI...")
        client = build_openai_client(cfg["endpoint"], cfg["api_key"])
        openai_tools = mcp_tools_to_openai_tools(mcp_tools)

        system_prompt = SYSTEM_PROMPT
        if cfg["extra_system_prompt"]:
            system_prompt += "\n\nAdditional instructions:\n" + cfg["extra_system_prompt"]

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        print("[4/4] Agent ready.\n")

        while True:
            try:
                user_input = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("Bye.")
                break

            messages.append({"role": "user", "content": user_input})

            assistant_reply = await complete_with_tools(
                client=client,
                deployment=cfg["deployment"],
                temperature=cfg["temperature"],
                max_rounds=cfg["max_rounds"],
                messages=messages,
                openai_tools=openai_tools,
                mcp_client=mcp_client,
            )

            messages.append({"role": "assistant", "content": assistant_reply})
            print(f"Agent> {assistant_reply}\n")

    finally:
        await mcp_client.close()


async def complete_with_tools(
    client: AsyncOpenAI,
    deployment: str,
    temperature: float,
    max_rounds: int,
    messages: list[dict[str, Any]],
    openai_tools: list[dict[str, Any]],
    mcp_client: RedNoteMCPClient,
) -> str:
    working_messages = list(messages)

    for round_index in range(max_rounds):
        req: dict[str, Any] = {
            "model": deployment,
            "messages": working_messages,
            "tools": openai_tools,
            "tool_choice": "auto",
        }
        if _should_send_temperature(deployment):
            req["temperature"] = temperature

        response = await client.chat.completions.create(**req)

        choice = response.choices[0]
        assistant_message = choice.message

        assistant_payload: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_message.content or "",
        }

        tool_calls = getattr(assistant_message, "tool_calls", None) or []
        if tool_calls:
            assistant_payload["tool_calls"] = []
            for tc in tool_calls:
                assistant_payload["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
            working_messages.append(assistant_payload)

            for tc in tool_calls:
                tool_name = tc.function.name
                raw_arguments = tc.function.arguments or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}

                print(f"[tool] Calling {tool_name}")
                try:
                    tool_result = await mcp_client.call_tool(tool_name, arguments)
                    tool_text = stringify_tool_result(tool_result)
                except Exception as exc:
                    tool_text = f"Tool call failed: {type(exc).__name__}: {exc}"

                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_text,
                    }
                )
            continue

        final_text = assistant_message.content or ""
        if final_text.strip():
            return final_text.strip()

        return "The model returned no text."

    return "Stopped after reaching the maximum number of tool rounds."


def main() -> None:
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\nBye.")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
