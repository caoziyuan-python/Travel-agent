"""
本地需求理解（不依赖外部 C# Customer Query Server）

用 Azure OpenAI 把自然语言用户输入解析为结构化 JSON：
  - intent     : 用户意图（旅行内 vs 越界）
  - emotion    : 情绪
  - entities   : 关键实体（城市、日期、人数、预算等）

输出 schema 与 query_validator.QueryAnalysisResult 兼容，
直接喂给 query_validator.validate() 即可做越界拦截。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from utils.config import (
    AZURE_API_KEY,
    AZURE_BASE_URL,
    AZURE_DEPLOYMENT,
    AZURE_ENDPOINT,
    should_send_temperature,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是旅行助手的需求解析模块。请把用户的自然语言查询解析为 JSON。

字段规范：
- intent: 必填。优先匹配以下值之一：
  旅行内（系统支持）：
    search_attractions     搜景点
    search_restaurants     搜餐厅
    search_hotels          搜酒店（仅信息查询，不下单）
    search_flights         搜机票（仅信息查询，不下单）
    get_weather            查天气
    get_transit_route      查路线
    get_travel_guide       看攻略
    plan_itinerary         规划行程
    inquire                泛泛咨询
  越界（系统不支持）：
    book_flight            下单订机票
    cancel_flight          退票
    change_flight          改签
    book_hotel             下单订酒店
    cancel_booking         取消预订
    complaint              投诉

- emotion: 必填，从 happy / sad / angry / neutral 中选一个。

- entities: 可选对象，提取以下字段（缺失则不填）：
    origin_city          出发城市
    destination_city     目的地城市（默认北京可省略）
    check_in_date        入住日期 YYYY-MM-DD
    check_out_date       退房日期 YYYY-MM-DD
    travel_date          出行日期 YYYY-MM-DD
    num_people           人数
    budget               预算（人民币）
    duration_days        行程天数
    themes               主题列表，如 ["亲子","美食","摄影"]

只输出合法 JSON，不要任何额外说明、代码块或注释。"""


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=AZURE_API_KEY, base_url=AZURE_BASE_URL)


async def extract_requirements(user_query: str) -> dict[str, Any] | None:
    """
    解析用户查询为结构化需求。

    Returns:
        {"intent": str, "emotion": str, "entities": {...}}
        失败时返回 None（调用方应优雅降级）
    """
    if not user_query.strip():
        return None
    if not AZURE_API_KEY or not AZURE_ENDPOINT or not AZURE_DEPLOYMENT:
        logger.warning("Azure OpenAI 未配置，跳过需求解析")
        return None

    client = _build_client()
    try:
        req: dict[str, Any] = {
            "model": AZURE_DEPLOYMENT,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_query},
            ],
            "response_format": {"type": "json_object"},
        }
        if should_send_temperature(AZURE_DEPLOYMENT):
            req["temperature"] = 0

        resp = await client.chat.completions.create(**req)
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return None
        return json.loads(text)
    except Exception as e:
        logger.warning(f"需求解析失败: {type(e).__name__}: {e}")
        return None


def format_for_system_prompt(req: dict[str, Any]) -> str:
    """
    把解析结果格式化为短系统提示，注入到对话上下文。
    """
    intent = req.get("intent", "")
    emotion = req.get("emotion", "")
    entities = req.get("entities") or {}

    lines = ["[用户需求解析]"]
    if intent:
        lines.append(f"- 意图: {intent}")
    if emotion and emotion != "neutral":
        lines.append(f"- 情绪: {emotion}")

    nonempty = {k: v for k, v in entities.items() if v not in (None, "", [], {})}
    if nonempty:
        ent_str = ", ".join(
            f"{k}={v}" if not isinstance(v, list) else f"{k}={'/'.join(map(str, v))}"
            for k, v in nonempty.items()
        )
        lines.append(f"- 实体: {ent_str}")

    return "\n".join(lines)
