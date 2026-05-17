"""
小红书工具层 — 封装 scripts/xiaohongshu/local_rednote_agent 的 MCP 客户端

文件位置说明：
  local_rednote_agent 现位于 scripts/xiaohongshu/local_rednote_agent/。
  本模块通过 sys.path 将项目根目录加入搜索路径后导入其核心类，
  避免重复实现 MCP 客户端逻辑。

初始化方式：
  在 FastAPI lifespan 中调用 init_xhs_client()，
  应用退出时调用 close_xhs_client()。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# 将项目根目录加入 Python 路径（backend/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_RedNoteMCPClient: type[Any] | None = None
_mcp_tools_to_openai_tools: Callable[[list[Any]], list[dict]] | None = None
_stringify_tool_result: Callable[[Any], str] | None = None


def _load_rednote_agent() -> tuple[type[Any], Callable[[list[Any]], list[dict]], Callable[[Any], str]]:
    """
    Load the MCP-backed RedNote client only when XHS is enabled.

    This keeps `python backend/cli.py --no-xhs` and non-XHS routes usable even
    when the optional `mcp` Python package has not been installed yet.
    """
    global _RedNoteMCPClient, _mcp_tools_to_openai_tools, _stringify_tool_result
    if _RedNoteMCPClient and _mcp_tools_to_openai_tools and _stringify_tool_result:
        return _RedNoteMCPClient, _mcp_tools_to_openai_tools, _stringify_tool_result

    try:
        from scripts.xiaohongshu.local_rednote_agent.chat_with_rednote import (
            RedNoteMCPClient,
            mcp_tools_to_openai_tools,
            stringify_tool_result,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            raise RuntimeError(
                "缺少 Python 包 `mcp`。请先安装 backend/requirements.txt，"
                "例如：python -m pip install -r backend/requirements.txt"
            ) from exc
        raise

    _RedNoteMCPClient = RedNoteMCPClient
    _mcp_tools_to_openai_tools = mcp_tools_to_openai_tools
    _stringify_tool_result = stringify_tool_result
    return RedNoteMCPClient, mcp_tools_to_openai_tools, stringify_tool_result

# ── 模块级单例（应用生命周期内保持连接） ─────────────────────────────────────

_client: Any | None = None
_mcp_tools: list[Any] = []
_openai_tool_defs: list[dict] = []


async def init_xhs_client(command: str, args: list[str]) -> None:
    """
    启动 rednote-mcp 子进程并建立 MCP 连接。
    在 FastAPI lifespan 的启动阶段调用。
    需要系统已安装：npm install -g rednote-mcp && rednote-mcp init
    """
    global _client, _mcp_tools, _openai_tool_defs
    RedNoteMCPClient, mcp_tools_to_openai_tools, _ = _load_rednote_agent()
    _client = RedNoteMCPClient(command, args)
    await _client.connect()
    _mcp_tools = await _client.list_tools()
    _openai_tool_defs = mcp_tools_to_openai_tools(_mcp_tools)
    print(f"[XHS] MCP 已连接，发现 {len(_mcp_tools)} 个工具: {[t.name for t in _mcp_tools]}")


async def close_xhs_client() -> None:
    """关闭 MCP 连接，在 FastAPI lifespan 的关闭阶段调用。"""
    global _client
    if _client:
        await _client.close()
        _client = None
        print("[XHS] MCP 连接已关闭")


def get_xhs_tool_definitions() -> list[dict]:
    """返回 OpenAI function-calling 格式的 XHS 工具定义列表（动态从 MCP 服务器获取）。"""
    return _openai_tool_defs


def get_xhs_tool_names() -> list[str]:
    """返回所有 XHS 工具名称。"""
    return [t.name for t in _mcp_tools]


def is_xhs_available() -> bool:
    """小红书 MCP 是否可用。"""
    return _client is not None and len(_mcp_tools) > 0


async def call_xhs_tool(tool_name: str, arguments: dict) -> str:
    """
    调用指定 XHS MCP 工具。

    常见工具名（以实际 rednote-mcp 暴露的为准）：
      - search       : 按关键词搜索笔记
      - get_note     : 获取单篇笔记详情
      - get_comments : 获取笔记评论
    """
    if not _client:
        return "小红书工具未初始化（MCP 客户端未连接，请检查 rednote-mcp 是否已安装并登录）"
    result = await _client.call_tool(tool_name, arguments)
    _, _, stringify_tool_result = _load_rednote_agent()
    return stringify_tool_result(result)
