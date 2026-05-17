"""
V3 编排层

把"需求理解 → 越界拦截 → 反问 / 规划 / 兜底"串成状态机。
对外只暴露 `handle_message` 和 `reset_state`。

routes.py 用 `handle_message(...)` 替代原先直接调 `run_agent`。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.context_manager import ConversationContext, context_manager
from agent.query_validator import QueryAnalysisResult, QueryValidator
from agent.requirement_agent import analyze_requirement
from agent.planner_agent import draft_itinerary
from agent.output_formatter import format_itinerary
from agent.travel_agent import run_agent
from agent.travel_agent import SYSTEM_PROMPT
from api.schemas import Itinerary, RequirementResult


def reset_state(session_id: str) -> None:
    context_manager.reset(session_id)


# ── 编排输出 ────────────────────────────────────────────────────────────────


class OrchestratorResult(BaseModel):
    reply: str
    tools_called: list[str] = Field(default_factory=list)
    intent: str | None = None
    out_of_scope: bool = False
    escalation: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    itinerary: Itinerary | None = None
    itinerary_id: str | None = None
    context_summary: str | None = None


# ── 主入口 ──────────────────────────────────────────────────────────────────


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


async def handle_message(
    session_id: str,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> OrchestratorResult:
    """
    Args:
        session_id: 会话 ID(上层生成)
        user_message: 当前轮用户消息
        history: 兼容旧测试参数;V3 实际使用 ContextManager 维护上下文

    Returns:
        OrchestratorResult — routes.py 填入 ChatResponse
    """
    ctx = context_manager.get_or_create(session_id)

    # 1. 需求理解(基于累积 entities)
    req = await analyze_requirement(user_message, ctx.entities)
    context_manager.update_entities(session_id, req.entities)
    ctx = context_manager.get_or_create(session_id)

    # 2. 越界 / 情绪检查(仅在有 intent 时)
    escalation: str | None = None
    if req.intent:
        analysis = QueryAnalysisResult(
            original_query=user_message,
            emotion=req.emotion,
            intent=req.intent,
        )
        is_valid, validator_msg, escalation_reason = QueryValidator.validate(analysis)
        escalation = escalation_reason
        if not is_valid:
            return OrchestratorResult(
                reply=validator_msg,
                intent=req.intent,
                out_of_scope=True,
                escalation=escalation,
                missing_fields=req.missing_fields,
                context_summary=ctx.summary,
            )

    # 3. 需求不齐 → 反问
    if not req.ready:
        question = req.clarification_question or "Could you share a bit more about your trip?"
        return OrchestratorResult(
            reply=question,
            intent=req.intent,
            escalation=escalation,
            needs_clarification=True,
            clarification_question=question,
            missing_fields=req.missing_fields,
            context_summary=ctx.summary,
        )

    # 3.5 "修改已有行程"识别:在已有 last_itinerary 的前提下,用户带修改关键词
    # → 覆写 intent 为 modify_itinerary,复用 plan 分支(planner 会收到 previous_itinerary)
    if ctx.last_itinerary is not None and _looks_like_modify(user_message, req.intent):
        req = req.model_copy(update={"intent": "modify_itinerary"})

    # 4. 分支
    if req.intent in ("plan_itinerary", "modify_itinerary"):
        return await _plan_branch(session_id, req, ctx, escalation, user_message)

    # 5. 兜底:老 run_agent(天气/POI/路线/酒店查询/泛问答)
    return await _fallback_branch(session_id, req, user_message, escalation)


_MODIFY_KEYWORDS = (
    "改", "换", "替换", "替掉", "调整", "重排", "删掉", "去掉",
    "不要", "换成", "加上", "增加", "modify", "change", "replace",
    "swap", "remove", "add", "edit",
)


def _looks_like_modify(text: str, intent: str | None) -> bool:
    """
    轻量启发式:已有 last_itinerary 时,用户消息含修改语义的关键词。
    intent=plan_itinerary 也当新规划;intent=inquire/None 才被当修改。
    """
    if intent == "plan_itinerary":
        return False
    low = text.lower()
    return any(kw in text or kw in low for kw in _MODIFY_KEYWORDS)


# ── 分支实现 ────────────────────────────────────────────────────────────────


async def _plan_branch(
    session_id: str,
    req: RequirementResult,
    ctx: ConversationContext,
    escalation: str | None,
    user_message: str = "",
) -> OrchestratorResult:
    lang = "zh" if _has_cjk(user_message) else "en"
    draft, tools = await draft_itinerary(req, previous_itinerary=ctx.last_itinerary, response_language=lang)
    itinerary = await format_itinerary(draft, req, response_language=lang)
    context_manager.save_itinerary(session_id, itinerary)
    ctx = context_manager.get_or_create(session_id)

    reply = itinerary.summary or f"Your {itinerary.duration_days}-day Beijing itinerary is ready! See the plan above."
    return OrchestratorResult(
        reply=reply,
        tools_called=tools,
        intent=req.intent,
        escalation=escalation,
        missing_fields=req.missing_fields,
        itinerary=itinerary,
        itinerary_id=itinerary.itinerary_id,
        context_summary=ctx.summary,
    )


async def _fallback_branch(
    session_id: str,
    req: RequirementResult,
    user_message: str,
    escalation: str | None,
) -> OrchestratorResult:
    convo = context_manager.build_model_messages(session_id, SYSTEM_PROMPT)
    convo = list(convo) + [{"role": "user", "content": user_message}]
    reply, tools = await run_agent(convo)
    ctx = context_manager.get_or_create(session_id)
    return OrchestratorResult(
        reply=reply,
        tools_called=tools,
        intent=req.intent,
        escalation=escalation,
        missing_fields=req.missing_fields,
        context_summary=ctx.summary,
    )
