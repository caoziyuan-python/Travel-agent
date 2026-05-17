"""
Session context manager for V3 chat orchestration.

First implementation is in-memory only. The class keeps the public surface small
so the storage can later move to Redis/SQLite without changing routes or
orchestrator flow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from api.schemas import ContextSnapshot, Itinerary
from utils.config import (
    AZURE_API_KEY,
    AZURE_BASE_URL,
    AZURE_DEPLOYMENT,
    CONTEXT_RECENT_MESSAGES,
    CONTEXT_SUMMARY_TRIGGER_MESSAGES,
    OPENAI_TIMEOUT_SECONDS,
    should_send_temperature,
)


@dataclass
class ConversationContext:
    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    entities: dict[str, Any] = field(default_factory=dict)
    last_itinerary: Itinerary | None = None
    itinerary_versions: dict[str, Itinerary] = field(default_factory=dict)
    last_tools: list[str] = field(default_factory=list)
    user_preferences: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _merge_non_empty(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if not _is_empty(value):
            merged[key] = value
    return merged


def _extract_preferences(text: str) -> dict[str, Any]:
    prefs: dict[str, Any] = {}
    low = text.lower()
    if "usd" in low or "美元" in text:
        prefs["currency"] = "USD"
    elif "cny" in low or "人民币" in text or "元" in text:
        prefs["currency"] = "CNY"
    if any(kw in text for kw in ("少走路", "不想走太多路", "少步行", "别太累")):
        prefs["walking_preference"] = "minimize_walking"
    if any(kw in text for kw in ("靠近", "附近", "旁边", "步行可达")):
        prefs["location_preference"] = "near_requested_place"
    return prefs


def _messages_to_lines(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        content = " ".join(content.split())
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{role}: {content}")
    return lines


def _fallback_summary(previous_summary: str, old_messages: list[dict[str, Any]]) -> str:
    lines = _messages_to_lines(old_messages)
    merged = "\n".join(part for part in [previous_summary.strip(), "\n".join(lines)] if part)
    return merged[-4000:]


class ContextManager:
    def __init__(self) -> None:
        self._contexts: dict[str, ConversationContext] = {}

    def get_or_create(self, session_id: str) -> ConversationContext:
        if session_id not in self._contexts:
            self._contexts[session_id] = ConversationContext(session_id=session_id)
        return self._contexts[session_id]

    def reset(self, session_id: str) -> None:
        self._contexts.pop(session_id, None)

    def clear_all(self) -> None:
        self._contexts.clear()

    def update_entities(self, session_id: str, entities: dict[str, Any]) -> ConversationContext:
        ctx = self.get_or_create(session_id)
        ctx.entities = _merge_non_empty(ctx.entities, entities)
        ctx.updated_at = _now_iso()
        return ctx

    def save_itinerary(self, session_id: str, itinerary: Itinerary) -> ConversationContext:
        ctx = self.get_or_create(session_id)
        ctx.last_itinerary = itinerary
        ctx.itinerary_versions[itinerary.itinerary_id] = itinerary
        ctx.updated_at = _now_iso()
        return ctx

    def build_model_messages(self, session_id: str, system_prompt: str) -> list[dict[str, Any]]:
        ctx = self.get_or_create(session_id)
        context_block = self._context_block(ctx)
        system_content = system_prompt if not context_block else f"{system_prompt}\n\n{context_block}"
        return [{"role": "system", "content": system_content}] + list(ctx.messages)

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_reply: str,
        tools_called: list[str] | None = None,
        itinerary: Itinerary | None = None,
        entities: dict[str, Any] | None = None,
        persist_messages: bool = True,
    ) -> ConversationContext:
        ctx = self.get_or_create(session_id)
        if entities:
            ctx.entities = _merge_non_empty(ctx.entities, entities)
        prefs = _extract_preferences(user_message)
        if prefs:
            ctx.user_preferences = _merge_non_empty(ctx.user_preferences, prefs)
        if itinerary is not None:
            ctx.last_itinerary = itinerary
            ctx.itinerary_versions[itinerary.itinerary_id] = itinerary
        ctx.last_tools = list(tools_called or [])
        if persist_messages:
            ctx.messages.append({"role": "user", "content": user_message})
            ctx.messages.append({"role": "assistant", "content": assistant_reply})
            await self._maybe_compact(ctx)
        ctx.updated_at = _now_iso()
        return ctx

    def snapshot(self, session_id: str) -> ContextSnapshot:
        ctx = self.get_or_create(session_id)
        return ContextSnapshot(
            session_id=session_id,
            summary=ctx.summary,
            entities=ctx.entities,
            user_preferences=ctx.user_preferences,
            last_itinerary_id=ctx.last_itinerary.itinerary_id if ctx.last_itinerary else None,
            itinerary_ids=list(ctx.itinerary_versions.keys()),
            recent_message_count=len(ctx.messages),
            last_tools=ctx.last_tools,
        )

    def _context_block(self, ctx: ConversationContext) -> str:
        parts: list[str] = []
        if ctx.summary:
            parts.append(f"[长期上下文摘要]\n{ctx.summary}")
        if ctx.entities:
            parts.append("[已知需求]\n" + json.dumps(ctx.entities, ensure_ascii=False))
        if ctx.user_preferences:
            parts.append("[用户偏好]\n" + json.dumps(ctx.user_preferences, ensure_ascii=False))
        if ctx.last_itinerary:
            parts.append(
                "[最近行程]\n"
                f"itinerary_id={ctx.last_itinerary.itinerary_id}, "
                f"destination={ctx.last_itinerary.destination}, "
                f"duration_days={ctx.last_itinerary.duration_days}"
            )
        return "\n\n".join(parts)

    async def _maybe_compact(self, ctx: ConversationContext) -> None:
        if len(ctx.messages) <= CONTEXT_SUMMARY_TRIGGER_MESSAGES:
            return
        recent_count = max(2, CONTEXT_RECENT_MESSAGES)
        old_messages = ctx.messages[:-recent_count]
        ctx.messages = ctx.messages[-recent_count:]
        llm_summary = await _summarize_with_llm(ctx.summary, old_messages)
        ctx.summary = llm_summary or _fallback_summary(ctx.summary, old_messages)


async def _summarize_with_llm(previous_summary: str, old_messages: list[dict[str, Any]]) -> str | None:
    if not (AZURE_API_KEY and AZURE_BASE_URL and AZURE_DEPLOYMENT and old_messages):
        return None
    try:
        client = AsyncOpenAI(
            api_key=AZURE_API_KEY,
            base_url=AZURE_BASE_URL,
            timeout=OPENAI_TIMEOUT_SECONDS,
            max_retries=0,
        )
        payload = {
            "previous_summary": previous_summary,
            "messages_to_compress": old_messages,
        }
        req: dict[str, Any] = {
            "model": AZURE_DEPLOYMENT,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是旅行助手的上下文压缩器。请保留用户稳定偏好、已确认需求、"
                        "重要约束、最近行程修改意图。输出简洁中文摘要，不要编造。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if should_send_temperature(AZURE_DEPLOYMENT):
            req["temperature"] = 0
        resp = await client.chat.completions.create(**req)
        text = (resp.choices[0].message.content or "").strip()
        return text[-4000:] if text else None
    except Exception:
        return None


context_manager = ContextManager()
