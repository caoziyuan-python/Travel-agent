"""
北京旅行 Agent — 核心编排层

使用 Azure OpenAI 原生 tool calling（与 scripts/xiaohongshu/local_rednote_agent 保持一致的实现风格），
不引入 LangChain 以减少依赖复杂度。

工具优先级：
  1. 高德工具（amap_tools）：天气、POI、路线 → 结构化实时数据
  2. 小红书工具（xhs_tools）：攻略、游记、评价 → 非结构化真实内容
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import openai
from openai import AsyncOpenAI

from utils.config import (
    AZURE_BASE_URL,
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    AGENT_TEMPERATURE,
    MAX_TOOL_ROUNDS,
    should_send_temperature,
)
from agent.tools.amap_tools import AMAP_TOOL_DEFINITIONS, call_amap_tool, _DISPATCH as AMAP_DISPATCH
from agent.tools.xhs_tools import get_xhs_tool_definitions, get_xhs_tool_names, call_xhs_tool
from agent.tools.flight_hotel_tools import (
    FLIGHT_HOTEL_TOOL_DEFINITIONS,
    call_flight_hotel_tool,
    _DISPATCH as FLIGHT_HOTEL_DISPATCH,
)
from agent.tools.rag_tools import RAG_TOOL_DEFINITIONS, call_rag_tool, RAG_TOOL_NAMES

SYSTEM_PROMPT = """LANGUAGE RULE (highest priority):
- If the user's message contains NO Chinese characters → reply entirely in English.
- If the user's message contains Chinese characters → reply in Chinese.
- When replying in English: ALL place names, restaurant names, attraction names, and addresses from tool results MUST be translated or romanized into English. Never output raw Chinese characters in an English reply. Format: "English Name (Chinese: 中文)" for well-known places, or just the English translation for others.

你是一位专业的北京旅行助手。你可以使用以下工具帮助用户规划旅行：

【静态知识库工具（优先使用，无API费用）】
- search_pois_rag : 语义搜索31,000+个北京POI（景点/餐厅/酒店/购物），返回ID、坐标、评分、价格
- get_route_rag   : 查询两个POI之间的预计算路线（驾车/公交/步行），需要POI_ID

【高德实时工具】
- get_weather     : 查询实时天气
- search_poi      : 实时搜索POI（当静态库结果不足时补充）
- get_transit_route : 实时公共交通路线（地铁/公交）
- get_driving_route : 实时驾车路线
- get_taxi_route    : 打车路线与费用预估
- get_walking_route : 实时步行路线

【机票/酒店工具】
- search_flights  : 查询国内航线最低机票价格（携程实时数据，仅信息）
- search_hotels   : 搜索北京酒店（Booking.com 离线缓存，仅信息）

【小红书工具（动态加载）】
- 搜索真实用户的旅游攻略、游记和评价

内容规范：
- 禁止出现"已查到""工具返回""知识库""POI_ID"等内部用语，直接呈现地点名称和信息
- 餐厅推荐必须具体：餐厅名称 + 人均价格（没有就省略） + 评分（没有就省略））+ 1-2道招牌菜（没有就省略）
- 休闲/购物推荐必须具体：店名 + 类型（咖啡馆/文创店/书店/茶馆等）+ 距离 + 人均或特色（没有就省略）
- 只给一套方案，不提供"方案A/方案B"选项
- 景点门票、餐饮费用均给出具体数字，不用"约"模糊带过（估算可说"预计"）

结尾规范：
每次完成行程规划后，必须根据用户提问的语言动态选择并追加以下提示（或其他语言的准确翻译翻译）：

【中文用户】
---
💡 **需要我用小红书帮你查一下吗？**
- 🔍 这些景点的真实游览体验和近期避坑提醒
- 🍜 推荐餐厅的近期食客评价（有没有踩雷？）
- 📸 拍照打卡最佳机位和时段

【英文用户】
---
💡 **Would you like me to check Xiaohongshu (RED) for you?**
- 🔍 Real visitor experiences and recent tips to avoid tourist traps for these attractions.
- 🍜 Recent diner reviews for the recommended restaurants (Did anyone have a bad experience?)
- 📸 Best photo spots and timing.

工作规则：
1. 优先使用 search_pois_rag 搜索地点，用返回的POI_ID调用 get_route_rag 查询路线，避免实时API调用。
2. 若 get_route_rag 提示路线不在知识库中，改用实时路线工具（get_transit_route/get_driving_route）。
3. 需要经纬度给路线工具时，先调用 search_poi 获取，再调用路线工具。
4. 用户问攻略/推荐/体验类问题时，可调用一次小红书搜索工具（search_notes 类），不要链式调用详情/评论。
5. 机票/酒店仅做信息查询，不能下单/取消/改签——若用户要预订，请告知请去携程/Booking 等平台。
6. 不要凭空编造数据，所有具体信息必须来自工具返回结果。
7. Follow the LANGUAGE RULE at the top — English users get fully English replies with all Chinese names translated.
"""


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _infer_currency_from_messages(messages: list[dict[str, Any]]) -> str:
    """
    默认策略：
      - 最近一条用户消息含中文 -> CNY
      - 否则 -> USD
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return "CNY" if _contains_cjk(content) else "USD"
    return "CNY"


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=AZURE_API_KEY, base_url=AZURE_BASE_URL)


def _all_tool_definitions() -> list[dict]:
    """合并 RAG + 高德 + 机票酒店 + 小红书工具定义。"""
    return RAG_TOOL_DEFINITIONS + AMAP_TOOL_DEFINITIONS + FLIGHT_HOTEL_TOOL_DEFINITIONS + get_xhs_tool_definitions()


def _non_xhs_tool_definitions() -> list[dict]:
    """XHS 降级时使用的工具集（去掉 XHS）。"""
    return RAG_TOOL_DEFINITIONS + AMAP_TOOL_DEFINITIONS + FLIGHT_HOTEL_TOOL_DEFINITIONS


async def _dispatch(name: str, arguments: dict) -> str:
    """根据工具名路由到对应的实现。"""
    if name in RAG_TOOL_NAMES:
        return await call_rag_tool(name, arguments)
    if name in AMAP_DISPATCH:
        return await call_amap_tool(name, arguments)
    if name in FLIGHT_HOTEL_DISPATCH:
        return await call_flight_hotel_tool(name, arguments)
    if name in get_xhs_tool_names():
        return await call_xhs_tool(name, arguments)
    return f"未知工具: {name}"


async def run_agent(
    messages: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """
    执行一轮 Agent 推理，支持多轮工具调用。

    Args:
        messages: 完整对话历史，格式与 OpenAI messages 相同
                  （调用方负责维护，包含 system prompt）

    Returns:
        (reply: str, tools_called: list[str])
        reply       — Agent 最终回答文本
        tools_called — 本轮调用的工具名列表（用于前端展示）
    """
    client = _build_client()
    tools = _all_tool_definitions()
    tools_called: list[str] = []
    xhs_used_once = False
    hotel_search_used_once = False
    force_final_answer = False
    xhs_degraded = False
    tool_result_cache: dict[str, str] = {}
    inferred_currency = _infer_currency_from_messages(messages)

    working = list(messages)

    for _ in range(MAX_TOOL_ROUNDS):
        # 小红书失败后降级：后续轮次不再向模型暴露 XHS 工具，降低触发风控风险
        effective_tools = [] if force_final_answer else (_non_xhs_tool_definitions() if xhs_degraded else tools)
        # 酒店实时抓取代价高，单轮问答限制为一次，避免重复抓取。
        if hotel_search_used_once:
            effective_tools = [
                t for t in effective_tools
                if t.get("function", {}).get("name") != "search_hotels"
            ]
        req: dict[str, Any] = {
            "model": AZURE_DEPLOYMENT,
            "messages": working,
            "tools": effective_tools or None,
            "tool_choice": "auto" if effective_tools else None,
        }
        if should_send_temperature(AZURE_DEPLOYMENT):
            req["temperature"] = AGENT_TEMPERATURE

        try:
            response = await client.chat.completions.create(**req)
        except (openai.APIConnectionError, openai.APITimeoutError):
            await asyncio.sleep(3)
            try:
                response = await client.chat.completions.create(**req)
            except (openai.APIConnectionError, openai.APITimeoutError) as exc:
                return f"网络连接暂时中断，请稍后重试（{type(exc).__name__}）。", tools_called
        except openai.RateLimitError:
            return "请求过于频繁，请等待几秒后重试。", tools_called
        except openai.AuthenticationError:
            return "Azure OpenAI 认证失败，请检查 AZURE_OPENAI_API_KEY 配置。", tools_called
        except openai.APIError as exc:
            return f"API 调用失败: {exc}", tools_called

        choice = response.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None) or []

        # 没有工具调用 → 直接返回文本
        if not tool_calls:
            reply = (msg.content or "").strip()
            return reply or "模型未返回内容。", tools_called

        # 有工具调用 → 执行并将结果追加到 messages。
        # OpenAI 要求 assistant 返回了几个 tool_calls，就必须追加几个 tool response。
        # 对高成本工具仍在下方做跳过/缓存，避免重复真实抓取。
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        }
        working.append(payload)

        post_tool_messages: list[dict[str, Any]] = []
        for tc in tool_calls:
            name = tc.function.name
            tools_called.append(name)
            cache_key: str | None = None
            try:
                args = json.loads(tc.function.arguments or "{}")
                if name in {"search_flights", "search_hotels"} and "currency" not in args:
                    args["currency"] = inferred_currency
                cache_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
                if cache_key in tool_result_cache:
                    result_text = (
                        f"工具 {name} 使用了已缓存结果，请基于已拿到的数据继续回答。\n"
                        f"{tool_result_cache[cache_key]}"
                    )
                    working.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
                    continue

                xhs_tools = set(get_xhs_tool_names())
                is_xhs_tool = name in xhs_tools

                # 每次问答最多一次 XHS 搜索，禁止链式 detail/comments
                if is_xhs_tool:
                    if xhs_used_once:
                        result_text = "已跳过：为降低风控风险，每次问答最多执行一次小红书搜索。请基于已有结果继续回答。"
                    elif "search" not in name:
                        result_text = "已跳过：当前策略仅允许小红书 search_notes 类搜索工具，不调用详情/评论链式抓取。"
                        xhs_used_once = True
                    else:
                        result_text = await _dispatch(name, args)
                        xhs_used_once = True
                else:
                    if name == "search_hotels" and hotel_search_used_once:
                        result_text = "已跳过：每次问答最多执行一次酒店搜索，请基于现有酒店结果继续回答。"
                    else:
                        result_text = await _dispatch(name, args)
                        if name == "search_hotels":
                            hotel_search_used_once = True
            except Exception as exc:
                result_text = f"工具调用失败 [{name}]: {exc}"
            if cache_key:
                tool_result_cache[cache_key] = result_text
            if name == "search_hotels":
                force_final_answer = True

            # 小红书异常时，自动降级到非 XHS 工具链
            if name in get_xhs_tool_names() and (
                "未初始化" in result_text
                or "failed" in result_text.lower()
                or "失败" in result_text
                or "error" in result_text.lower()
            ):
                xhs_degraded = True
                post_tool_messages.append({
                    "role": "system",
                    "content": "小红书工具当前不可用。后续请仅使用高德工具与已有上下文回答，不要再调用小红书工具。",
                })

            working.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
        working.extend(post_tool_messages)
        if force_final_answer:
            working.append({
                "role": "system",
                "content": "酒店搜索结果已经返回。请不要再调用任何工具，直接基于已有酒店数据回答用户。",
            })

    return "已达到最大工具调用轮次，请尝试更简洁的提问。", tools_called
