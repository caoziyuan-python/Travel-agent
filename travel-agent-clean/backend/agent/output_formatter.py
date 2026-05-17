"""
V3 输出格式化

把 planner_agent 的自然语言草稿通过 LLM JSON mode 转成严格的 Itinerary JSON。
解析失败时:重试一次,仍失败 → 返回一份只带 summary=draft 的最小合法 Itinerary(不挡前端)。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from openai import AsyncOpenAI
from pydantic import ValidationError

from utils.config import (
    AZURE_BASE_URL,
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    OPENAI_TIMEOUT_SECONDS,
    should_send_temperature,
)
from api.schemas import (
    Budget,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    RequirementResult,
)

logger = logging.getLogger(__name__)


_FORMAT_SYSTEM_PROMPT = """你是"行程格式化器"。把给定的自然语言行程草稿转为严格 JSON。

输出 schema(字段必须一字不差):
{
  "destination": str,
  "duration_days": int,
  "themes": [str, ...],
  "days": [
    {
      "day_index": int,           // 1-based
      "date": str | null,         // YYYY-MM-DD 或 null
      "stops": [
        {
          "stop_id": str,         // "d{day_index}s{序号}" 如 d1s1
          "type": "attraction" | "restaurant" | "hotel" | "transit" | "other",
          "name": str,
          "start_time": str | null,   // "HH:MM" 或 null
          "duration_min": int | null,
          "location": {"name": str, "address": str|null, "lat": float|null, "lng": float|null} | null,
          "cost": number | null,
          "currency": "CNY",
          "notes": str | null
        }, ...
      ],
      "daily_total": number | null
    }, ...
  ],
  "budget": {
    "transport": number, "accommodation": number, "food": number,
    "tickets": number, "other": number, "total": number, "currency": "CNY"
  },
  "summary": str | null
}

规则:
- stop_id 严格按 d{day_index}s{1-based序号} 生成,不要重复。
- 所有金额数字,没有就写 0(不要字符串)。
- 所有时间字符串 "HH:MM",不要带秒。
- budget.total 应当等于其他各项之和(四舍五入到个位)。
- 只输出 JSON,不要 markdown 代码块,不要任何额外说明文字。
- If the draft contains coordinates in the format "(lat: XX.XXXXXX, lng: XXX.XXXXXX)", extract them into location.lat and location.lng for that stop.
"""


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=AZURE_API_KEY,
        base_url=AZURE_BASE_URL,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _minimal_fallback(draft_text: str, requirement: RequirementResult) -> Itinerary:
    """LLM 解析失败的兜底:只保留 summary,days 为空,前端降级为纯文本显示。"""
    ent = requirement.entities or {}
    dest = ent.get("destination_city") or "Beijing"
    duration = int(ent.get("duration_days") or 1)
    themes = ent.get("themes") or []
    if isinstance(themes, str):
        themes = [themes]
    return Itinerary(
        itinerary_id=str(uuid.uuid4()),
        destination=dest,
        duration_days=duration,
        themes=list(themes),
        days=[],
        budget=Budget(total=0),
        summary=draft_text[:2000],
        created_at=_now_iso(),
    )


async def _llm_to_json(
    draft_text: str,
    requirement: RequirementResult,
    retry_hint: str = "",
    response_language: str = "en",
) -> dict | None:
    if not (AZURE_API_KEY and AZURE_BASE_URL and AZURE_DEPLOYMENT):
        return None

    client = _build_client()
    ent = requirement.entities or {}
    user_msg: dict = {
        "draft": draft_text,
        "requirement_hints": {
            "destination": ent.get("destination_city"),
            "duration_days": ent.get("duration_days"),
            "themes": ent.get("themes"),
        },
    }
    if response_language == "en":
        user_msg["language_instruction"] = (
            "Write the 'summary' field in English. "
            "Keep stop 'name' values as they appear in the draft (English preferred)."
        )
    if retry_hint:
        user_msg["previous_error"] = retry_hint

    try:
        req: dict[str, object] = {
            "model": AZURE_DEPLOYMENT,
            "messages": [
                {"role": "system", "content": _FORMAT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_msg, ensure_ascii=False)},
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
        logger.warning(f"format LLM 调用失败: {type(e).__name__}: {e}")
        return None


def _coerce_and_validate(data: dict) -> Itinerary:
    """
    把 LLM 返回的 dict 用 Pydantic 校验,补齐 itinerary_id / created_at 这类服务端字段。
    """
    data.setdefault("itinerary_id", str(uuid.uuid4()))
    data.setdefault("created_at", _now_iso())

    # stops 兜底 stop_id
    for d in data.get("days") or []:
        idx = d.get("day_index") or 1
        for i, s in enumerate(d.get("stops") or [], start=1):
            s.setdefault("stop_id", f"d{idx}s{i}")
            s.setdefault("currency", "CNY")

    # budget 兜底
    data.setdefault("budget", {"total": 0})
    data["budget"].setdefault("total", sum(
        float(data["budget"].get(k, 0) or 0)
        for k in ("transport", "accommodation", "food", "tickets", "other")
    ))
    data["budget"].setdefault("currency", "CNY")

    # duration_days 兜底
    if not data.get("duration_days"):
        data["duration_days"] = max(1, len(data.get("days") or []))
    if not data.get("destination"):
        data["destination"] = "Beijing"
    if "themes" not in data or not isinstance(data["themes"], list):
        data["themes"] = []

    return Itinerary(**data)


async def format_itinerary(
    draft_text: str,
    requirement: RequirementResult,
    response_language: str = "en",
) -> Itinerary:
    """
    把行程草稿 → 合法 Itinerary。失败走 fallback。
    response_language: "en" or "zh"
    """
    data = await _llm_to_json(draft_text, requirement, response_language=response_language)
    if data is not None:
        try:
            return _coerce_and_validate(data)
        except ValidationError as e:
            logger.warning(f"第一次 Pydantic 校验失败,将重试: {e.error_count()} errors")
            retry_hint = str(e)[:800]
            data2 = await _llm_to_json(draft_text, requirement, retry_hint=retry_hint, response_language=response_language)
            if data2 is not None:
                try:
                    return _coerce_and_validate(data2)
                except ValidationError as e2:
                    logger.warning(f"重试仍失败,走 fallback: {e2.error_count()} errors")

    return _minimal_fallback(draft_text, requirement)
