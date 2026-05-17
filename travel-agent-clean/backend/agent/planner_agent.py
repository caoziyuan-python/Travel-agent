"""
V3 行程规划 Agent

职责:只负责"规划行程草稿",不做天气闲聊、不做攻略搜索。
    工具集精简到路线/POI/酒店/天气,绝不暴露小红书 — 避免被拽去做非规划任务。

输出:半结构化自然语言草稿(每天一段,stop 用 `-` 列表),由 output_formatter
    再拿去生成严格 JSON。草稿留一点自然语言余量,LLM 表现比"直接 JSON"更稳。
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from utils.config import (
    AGENT_TEMPERATURE,
    AZURE_BASE_URL,
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    MAX_TOOL_ROUNDS,
    OPENAI_TIMEOUT_SECONDS,
    should_send_temperature,
)
from agent.tools.amap_tools import AMAP_TOOL_DEFINITIONS, call_amap_tool, _DISPATCH as AMAP_DISPATCH
from agent.tools.flight_hotel_tools import (
    FLIGHT_HOTEL_TOOL_DEFINITIONS,
    _DISPATCH as FLIGHT_HOTEL_DISPATCH,
    call_flight_hotel_tool,
)
from agent.tools.rag_tools import RAG_TOOL_DEFINITIONS, call_rag_tool, RAG_TOOL_NAMES
from api.schemas import Itinerary, RequirementResult

PLANNER_MAX_TOOL_ROUNDS = 14  # more rounds needed for multi-day planning

PLANNER_TOOL_NAMES = {
    # RAG static knowledge base (preferred — no API cost)
    "search_pois_rag",
    "get_route_rag",
    "get_poi_details_rag",
    "search_pois_nearby_rag",
    # Amap live fallback
    "search_poi",
    "get_weather",
    "get_transit_route",
    "get_driving_route",
    "get_walking_route",
    "get_taxi_route",
    # Accommodation
    "search_hotels",
}


PLANNER_SYSTEM_PROMPT = """IMPORTANT: Write the entire itinerary draft in English by default. Only use Chinese if the user's request was in Chinese (contains Chinese characters 中文).

你是北京旅行"行程规划专员"，负责生成带具体时刻表的详细多日行程草稿。

【工具使用策略（严格按顺序）】

第一阶段 — 地点发现（2-3 次工具调用批量获取，优先用 RAG）:
1. search_pois_rag("北京 景点 历史文化 主题", top_k=12)  ← 获取景点候选+POI_ID+坐标
2. search_pois_rag("北京 特色餐厅 老字号 美食", top_k=10)  ← 获取餐厅候选
3. search_pois_rag("酒店 住宿", top_k=6) 或 search_hotels(...)  ← 选定住宿

第二阶段 — 路线核实（用 RAG，不在库则用实时工具）:
4. get_route_rag(hotel_id, first_attraction_id)  ← 每天第一段路程
5. get_route_rag(attraction_id, lunch_restaurant_id)  ← 景点→午餐
6. 若 get_route_rag 提示"超出采样范围"→ 改用 get_transit_route / get_driving_route

第三阶段 — 附近补充（午餐/休闲/购物中途停留）:
7. search_pois_nearby_rag(attraction_id, "特色餐厅 小吃", radius_km=1.0)  ← 找景点附近午餐
8. search_pois_nearby_rag(attraction_id, "咖啡 休闲 购物", radius_km=1.5)  ← 找下午茶/购物中途站

【时刻表构建规则】
- 遵守 open_time_raw 字段的营业时间，不安排已关闭或尚未开放的景点
- 每段时间 = 前一个地点结束时间 + 路线交通时间（取自 get_route_rag 的 duration_sec）
- 典型游览时长参考：故宫/颐和园 3-4h，普通博物馆 1.5-2h，胡同/街区 1-1.5h，餐厅 45-60min，咖啡/休闲 30min
- 标准节奏：09:00 出发 → 上午景点 → 12:00 午餐 → 14:00 下午景点/购物 → 17:30 傍晚活动 → 18:30 晚餐 → 20:00 返回酒店
- 同一天的景点优先选同一区域（district 字段相同），减少跨城折返
- 户外/露天景点：若需要天气参考，调用 get_weather

【预算计算规则】
- 餐饮：取 POI 的 price 字段（人均价格）× 人数
- 交通：取 get_route_rag 的 cost_cny 字段汇总
- 住宿：search_hotels 返回的价格 × 晚数
- 门票：已知的景点票价（故宫¥60，颐和园¥30，天坛¥15，长城¥65等）
- 不确定的费用说明"约¥X"

【输出格式（自然语言草稿，不要 JSON）】

▌禁止内部用语
绝对不能出现："已查到""工具返回""知识库""POI_ID""RAG""静态库""相关POI"
全部直接呈现地点名称和信息，就像人工撰写的旅游攻略。

▌餐厅格式（必须具体）
**餐厅名称**（类型，如：北京菜/火锅/小吃）
- 人均：¥X | 评分：X.X分 | 距离：步行X分钟 （没有就省略）
- 招牌菜：XXXX、XXXX （没有就省略）
- 实用提示：XX时段较忙，建议提前到

▌休闲/购物格式（必须具体，不写"随便逛"）
**场所名称**（类型：咖啡馆/书店/文创集合店/茶馆/商场）
- 特色：一句话描述风格/卖点
- 人均/参考消费：¥X
- 距离：步行X分钟

▌单一方案
只给一套方案，不写"方案A/方案B"，不写"你也可以选择"。

▌预算表（行程末尾）
| 项目 | 金额/人 | 说明 |
|------|---------|------|
| 机票 | ¥X | 单程/往返 |
| 住宿 | ¥X | X晚×¥X/晚 |
| 景点门票 | ¥X | 各景点合计 |
| 餐饮 | ¥X | X餐×平均¥X |
| 市内交通 | ¥X | 地铁+偶尔打车 |
| 合计 | ¥X | |

▌结尾固定追加（请根据用户提问的语言选择对应的版本或翻译成用户使用的语言）

【中文用户】
---
💡 **需要我用小红书帮你查一下吗？**
- 🔍 这些景点的真实游览体验和近期避坑提醒（哪些值得去，哪些可以跳过？）
- 🍜 推荐餐厅的近期真实评价（有没有踩雷？排队多久？）
- 📸 最佳拍照时段和机位推荐

【英文用户】
---
💡 **Would you like me to check Xiaohongshu (RED) for you?**
- 🔍 Real visitor experiences and recent tips to avoid tourist traps for these attractions.
- 🍜 Recent authentic reviews for the recommended restaurants (Any letdowns? How long is the wait?)
- 📸 Best photo spots and timing recommendations.

最多调用 {max_rounds} 轮工具，超过后基于已有信息直接输出草稿。
"""


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=AZURE_API_KEY,
        base_url=AZURE_BASE_URL,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _planner_tools() -> list[dict]:
    combined = RAG_TOOL_DEFINITIONS + AMAP_TOOL_DEFINITIONS + FLIGHT_HOTEL_TOOL_DEFINITIONS
    return [t for t in combined if t.get("function", {}).get("name") in PLANNER_TOOL_NAMES]


async def _dispatch(name: str, arguments: dict) -> str:
    if name in RAG_TOOL_NAMES:
        return await call_rag_tool(name, arguments)
    if name in AMAP_DISPATCH:
        return await call_amap_tool(name, arguments)
    if name in FLIGHT_HOTEL_DISPATCH:
        return await call_flight_hotel_tool(name, arguments)
    return f"未知/未授权工具: {name}"


def _format_requirement_block(req: RequirementResult) -> str:
    lines = ["[Planning Requirements]"]
    if req.intent:
        lines.append(f"- intent: {req.intent}")
    ent = req.entities or {}
    # 只挑关键字段摆上去,避免噪声
    keys = [
        "destination_city", "duration_days", "themes", "num_people",
        "budget", "travel_date", "check_in_date", "check_out_date",
    ]
    for k in keys:
        v = ent.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            v = "/".join(str(x) for x in v)
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _format_previous_itinerary(prev: Itinerary | None) -> str | None:
    if prev is None:
        return None
    # 压缩成紧凑摘要,供 LLM 做最小改动参考
    days = []
    for d in prev.days:
        stops = "; ".join(
            f"{s.start_time or ''} {s.name}({s.type})¥{s.cost or 0}".strip()
            for s in d.stops
        )
        days.append(f"Day {d.day_index}: {stops}")
    return "[已有行程,请在此基础上按用户要求做最小改动]\n" + "\n".join(days)


_LANG_DIRECTIVE_EN = (
    "\n\n[LANGUAGE DIRECTIVE] Write the ENTIRE itinerary draft in English. "
    "Every stop name, transit note, restaurant description, timing note, and summary paragraph "
    "must be in English. Do NOT output any Chinese characters.\n"
    "[COORDINATES DIRECTIVE] For EVERY stop that has lat/lng from tool results, "
    "append the coordinates immediately after the stop name in this exact format: "
    "(lat: XX.XXXXXX, lng: XXX.XXXXXX). Example: '- The Forbidden City (lat: 39.917839, lng: 116.397029)'. "
    "This is required for map rendering — omitting coordinates means no map."
)

_LANG_DIRECTIVE_ZH = "\n\n[语言要求] 请用中文撰写所有行程内容。"


async def draft_itinerary(
    requirement: RequirementResult,
    previous_itinerary: Itinerary | None = None,
    response_language: str = "en",
) -> tuple[str, list[str]]:
    """
    执行行程规划,返回 (草稿文本, 调用过的工具名列表)。
    response_language: "en" or "zh"
    """
    client = _build_client()
    tools = _planner_tools()
    tools_called: list[str] = []

    system = PLANNER_SYSTEM_PROMPT.format(max_rounds=PLANNER_MAX_TOOL_ROUNDS)
    lang_directive = _LANG_DIRECTIVE_EN if response_language == "en" else _LANG_DIRECTIVE_ZH
    user_block = _format_requirement_block(requirement) + lang_directive
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_block},
    ]
    prev_block = _format_previous_itinerary(previous_itinerary)
    if prev_block:
        messages.append({"role": "user", "content": prev_block})

    for _ in range(PLANNER_MAX_TOOL_ROUNDS):
        req: dict[str, Any] = {
            "model": AZURE_DEPLOYMENT,
            "messages": messages,
            "tools": tools or None,
            "tool_choice": "auto" if tools else None,
        }
        if should_send_temperature(AZURE_DEPLOYMENT):
            req["temperature"] = AGENT_TEMPERATURE
        try:
            resp = await client.chat.completions.create(**req)
        except Exception as exc:
            return f"Planner model error: {type(exc).__name__}: {exc}", tools_called
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            return (msg.content or "").strip() or "Failed to generate itinerary draft.", tools_called

        messages.append({
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
        })

        for tc in tool_calls:
            name = tc.function.name
            tools_called.append(name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                if name not in PLANNER_TOOL_NAMES:
                    result_text = f"Tool '{name}' is not authorized for the planner. Use an authorized tool."
                else:
                    result_text = await _dispatch(name, args)
            except Exception as exc:
                result_text = f"工具调用失败 [{name}]: {exc}"

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

    # 轮次用尽,再要一次纯文本 summarize
    req = {
        "model": AZURE_DEPLOYMENT,
        "messages": messages + [{
            "role": "system",
            "content": "Maximum tool rounds reached. Output the itinerary draft now based on results collected so far. Do not call any more tools.",
        }],
    }
    if should_send_temperature(AZURE_DEPLOYMENT):
        req["temperature"] = AGENT_TEMPERATURE
    try:
        resp = await client.chat.completions.create(**req)
    except Exception as exc:
        return f"规划模型总结失败: {type(exc).__name__}: {exc}", tools_called
    text = (resp.choices[0].message.content or "").strip()
    return text or "Failed to generate itinerary draft (tool rounds exhausted).", tools_called
