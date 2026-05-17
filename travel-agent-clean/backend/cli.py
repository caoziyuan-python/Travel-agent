"""
北京 AI 旅行助手 — 终端测试客户端

用法（在 backend/ 目录下）：
    python cli.py                  # 完整模式：连 rednote-mcp + Azure OpenAI
    python cli.py --no-xhs         # 跳过小红书 MCP 连接（启动更快）
    python cli.py --no-extract     # 跳过需求理解（直接走 agent）

特殊命令：
    exit / quit  退出
    reset        清除对话历史
    history      打印当前对话长度
    last-tools   打印上一轮调用的工具

不依赖 FastAPI / HTTP，直接调用 backend.agent.travel_agent 与
agent.requirement_extractor，复用主后端同一套逻辑。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 允许从任何目录运行：把 backend/ 加到 sys.path
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agent.travel_agent import SYSTEM_PROMPT, run_agent  # noqa: E402
from agent.tools.xhs_tools import init_xhs_client, close_xhs_client, get_xhs_tool_names  # noqa: E402
from agent.requirement_extractor import extract_requirements, format_for_system_prompt  # noqa: E402
from agent.query_validator import QueryAnalysisResult, QueryValidator  # noqa: E402
from utils.config import (  # noqa: E402
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    AZURE_ENDPOINT,
    AMAP_API_KEY,
    MCP_COMMAND,
    MCP_ARGS,
)


# ── 终端着色（无依赖） ───────────────────────────────────────────────────────

class C:
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"


def banner():
    print(f"{C.BOLD}{C.CYAN}" + "=" * 56)
    print("  北京 AI 旅行助手  ·  终端测试版")
    print("=" * 56 + f"{C.RESET}")
    print(f"{C.DIM}命令: exit | reset | history | last-tools{C.RESET}\n")


def env_summary(no_xhs: bool, no_extract: bool):
    items = [
        ("Azure OpenAI", bool(AZURE_API_KEY and AZURE_ENDPOINT and AZURE_DEPLOYMENT)),
        ("AMAP key", bool(AMAP_API_KEY)),
        ("XHS MCP", not no_xhs),
        ("需求理解", not no_extract),
    ]
    parts = []
    for name, ok in items:
        color = C.GREEN if ok else C.YELLOW
        mark = "✓" if ok else "·"
        parts.append(f"{color}{mark} {name}{C.RESET}")
    print("  " + "    ".join(parts) + "\n")


def azure_openai_ready() -> bool:
    return bool(AZURE_API_KEY and AZURE_ENDPOINT and AZURE_DEPLOYMENT)


# ── 主循环 ──────────────────────────────────────────────────────────────────

async def chat_loop(no_xhs: bool, no_extract: bool):
    if not no_xhs:
        print(f"{C.DIM}正在连接 rednote-mcp（首次启动较慢，npx 会下载包）...{C.RESET}")
        try:
            await init_xhs_client(MCP_COMMAND, MCP_ARGS)
            tools = get_xhs_tool_names()
            print(f"{C.GREEN}✓ XHS MCP 已连接，工具: {tools}{C.RESET}\n")
        except Exception as e:
            print(f"{C.YELLOW}⚠ XHS 连接失败: {e}\n  → 仅高德 + 机票/酒店工具可用{C.RESET}\n")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_tools: list[str] = []

    try:
        while True:
            try:
                user_input = input(f"{C.BOLD}{C.BLUE}你 > {C.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break

            if not user_input:
                continue

            cmd = user_input.lower()
            if cmd in ("exit", "quit"):
                print("再见。")
                break
            if cmd == "reset":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                last_tools = []
                print(f"{C.DIM}(对话已重置){C.RESET}\n")
                continue
            if cmd == "history":
                print(f"{C.DIM}当前消息数: {len(messages)}{C.RESET}\n")
                continue
            if cmd == "last-tools":
                print(f"{C.DIM}上一轮工具: {last_tools}{C.RESET}\n")
                continue
            if not azure_openai_ready():
                print(
                    f"\n{C.YELLOW}Azure OpenAI 未配置完整。请在项目根目录或 backend/ 下创建 .env，"
                    f"至少包含 AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / "
                    f"AZURE_OPENAI_DEPLOYMENT。{C.RESET}\n"
                )
                continue

            # 需求理解（可选）
            requirements = None
            if not no_extract:
                requirements = await extract_requirements(user_input)
                if requirements:
                    intent = requirements.get("intent", "?")
                    emotion = requirements.get("emotion", "?")
                    print(f"{C.DIM}[需求] intent={intent} emotion={emotion}{C.RESET}")

                    # 越界检查
                    analysis = QueryAnalysisResult(
                        original_query=user_input,
                        emotion=emotion,
                        intent=intent,
                    )
                    is_valid, validator_msg, escalation = QueryValidator.validate(analysis)
                    if not is_valid:
                        print(f"\n{C.YELLOW}{C.BOLD}助手 > {C.RESET}{C.YELLOW}{validator_msg}{C.RESET}\n")
                        continue
                    if escalation:
                        print(f"{C.MAGENTA}[升级] {escalation}{C.RESET}")

                    # 注入需求到上下文
                    snippet = format_for_system_prompt(requirements)
                    if snippet:
                        messages.append({"role": "system", "content": snippet})

            messages.append({"role": "user", "content": user_input})

            try:
                reply, tools_called = await run_agent(messages)
            except Exception as e:
                print(f"\n{C.RED}错误: {type(e).__name__}: {e}{C.RESET}\n")
                # 回滚最后一条 user 消息，保持上下文一致
                messages.pop()
                continue

            messages.append({"role": "assistant", "content": reply})
            last_tools = tools_called

            if tools_called:
                print(f"{C.DIM}[工具] {' → '.join(tools_called)}{C.RESET}")
            print(f"\n{C.GREEN}{C.BOLD}助手 > {C.RESET}{reply}\n")

    finally:
        if not no_xhs:
            await close_xhs_client()


def parse_args():
    p = argparse.ArgumentParser(description="北京 AI 旅行助手 终端版")
    p.add_argument("--no-xhs", action="store_true", help="跳过 rednote-mcp 连接（启动更快）")
    p.add_argument("--no-extract", action="store_true", help="跳过需求理解（直接走 agent）")
    return p.parse_args()


def main():
    args = parse_args()
    banner()
    env_summary(args.no_xhs, args.no_extract)
    asyncio.run(chat_loop(args.no_xhs, args.no_extract))


if __name__ == "__main__":
    main()
