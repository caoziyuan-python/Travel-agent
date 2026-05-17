"""
V3 需求理解 Agent

在 requirement_extractor(单次抽取)之上再叠一层:
  1. 累积会话已知 entities(跨轮保留)
  2. 按 intent 查"必填字段表",找缺失
  3. 缺失时调 LLM 生成一句自然语言追问(按用户语言)
  4. ready=True 时,把合并后的需求交给下游 planner 或主 agent

对外只暴露 `analyze_requirement`。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from utils.config import (
    AZURE_BASE_URL,
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    OPENAI_TIMEOUT_SECONDS,
    should_send_temperature,
)
from agent.requirement_extractor import extract_requirements
from api.schemas import RequirementResult

logger = logging.getLogger(__name__)


# 各 intent 必填字段清单。键值用 requirement_extractor 的 intent 字符串。
# entities 里任何一个值非空 => 视为已提供。
REQUIRED_FIELDS_BY_INTENT: dict[str, list[str]] = {
    "plan_itinerary": ["duration_days"],
    "search_flights": ["origin_city", "destination_city"],
    "search_hotels": ["check_in_date"],
    "get_transit_route": ["origin_city", "destination_city"],
    # 其他 intent 无必填,缺字段由 LLM 在后续工具调用里用默认值兜底
}


# 字段 → 用于反问的人类可读名称(仅作 LLM prompt 参考)
FIELD_LABELS = {
    "duration_days": "行程天数 (duration_days)",
    "themes": "旅行主题/偏好 (themes,例如 美食/历史/亲子)",
    "origin_city": "出发城市 (origin_city)",
    "destination_city": "目的地城市 (destination_city)",
    "check_in_date": "入住日期 (check_in_date, YYYY-MM-DD)",
    "travel_date": "出行日期 (travel_date, YYYY-MM-DD)",
    "num_people": "出行人数 (num_people)",
    "budget": "预算 (budget)",
}


# ── 内部工具 ────────────────────────────────────────────────────────────────


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=AZURE_API_KEY,
        base_url=AZURE_BASE_URL,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _merge_entities(old: dict, new: dict) -> dict:
    """
    new 覆盖 old,但 new 中的空值不覆盖 old 的有效值。
    保留 old 独有字段。
    """
    merged = dict(old or {})
    for k, v in (new or {}).items():
        if _is_empty(v):
            continue
        merged[k] = v
    return merged


def _find_missing(intent: str | None, entities: dict) -> list[str]:
    if not intent:
        return []
    required = REQUIRED_FIELDS_BY_INTENT.get(intent, [])
    return [f for f in required if _is_empty(entities.get(f))]


_CLARIFY_SYSTEM_PROMPT = """你负责为旅行助手生成一句自然语言的反问,补全用户旅行需求中的缺失字段。

规则:
- 只输出合法 JSON,形如 {"question": "..."}。
- question 必须是一句简短自然的话,不要列出字段名或用英文变量名。
- 语言跟随 user_query:user_query 含中文 → 中文反问;否则英文。
- 如果缺多于 2 个字段,一句话问清楚最关键的 1-2 个即可,其他以后再问。
- 不要寒暄,不要说"好的""没问题",直接提问。
"""


async def _generate_clarification(
    user_query: str,
    intent: str,
    known_entities: dict,
    missing_fields: list[str],
) -> str | None:
    """调 LLM 生成一句反问。失败时 fallback 到一句中文模板。"""
    if not missing_fields:
        return None

    fallback = _fallback_clarification(missing_fields, user_query)

    if not (AZURE_API_KEY and AZURE_BASE_URL and AZURE_DEPLOYMENT):
        return fallback

    user_payload = {
        "user_query": user_query,
        "intent": intent,
        "known_entities": known_entities,
        "missing_fields": [FIELD_LABELS.get(f, f) for f in missing_fields],
    }

    try:
        client = _build_client()
        req: dict[str, Any] = {
            "model": AZURE_DEPLOYMENT,
            "messages": [
                {"role": "system", "content": _CLARIFY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        if should_send_temperature(AZURE_DEPLOYMENT):
            req["temperature"] = 0
        resp = await client.chat.completions.create(**req)
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return fallback
        data = json.loads(text)
        q = (data.get("question") or "").strip()
        return q or fallback
    except Exception as e:
        logger.warning(f"生成反问失败,使用模板: {type(e).__name__}: {e}")
        return fallback


def _fallback_clarification(missing_fields: list[str], user_query: str = "") -> str:
    pieces = [FIELD_LABELS.get(f, f) for f in missing_fields[:2]]
    if any("一" <= ch <= "鿿" for ch in user_query):
        return f"为了给出更准的建议,请告诉我:{' 、 '.join(pieces)}。"
    return f"To give you a better recommendation, could you tell me: {', '.join(pieces)}?"


# ── 对外 API ────────────────────────────────────────────────────────────────


async def analyze_requirement(
    user_query: str,
    accumulated_entities: dict | None = None,
) -> RequirementResult:
    """
    解析并校验用户需求。

    Args:
        user_query: 当前轮用户输入
        accumulated_entities: 同一 session 跨轮累积的 entities

    Returns:
        RequirementResult:
          - ready=True  → intent+entities 齐全,可进入下游 agent
          - ready=False → missing_fields + clarification_question 非空
    """
    accumulated_entities = accumulated_entities or {}

    extracted = await extract_requirements(user_query)

    if not extracted:
        # 抽取失败:不阻塞,直接放行到兜底 agent;保留已有 entities
        return RequirementResult(
            intent=None,
            emotion="neutral",
            entities=accumulated_entities,
            ready=True,
            missing_fields=[],
            clarification_question=None,
        )

    intent = extracted.get("intent") or None
    emotion = extracted.get("emotion") or "neutral"
    new_entities = extracted.get("entities") or {}
    merged = _merge_entities(accumulated_entities, new_entities)

    missing = _find_missing(intent, merged)

    if not missing:
        return RequirementResult(
            intent=intent,
            emotion=emotion,
            entities=merged,
            ready=True,
            missing_fields=[],
            clarification_question=None,
        )

    question = await _generate_clarification(user_query, intent or "", merged, missing)
    return RequirementResult(
        intent=intent,
        emotion=emotion,
        entities=merged,
        ready=False,
        missing_fields=missing,
        clarification_question=question,
    )
