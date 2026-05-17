"""
V3 离线烟雾测试:不依赖 Azure / 高德实际调用。

跑法(从 backend/ 目录):
    python tests/test_v3.py

用 monkeypatch 风格替换 LLM/工具调用,只验状态机与数据流。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.schemas import (  # noqa: E402
    Budget, Itinerary, ItineraryDay, ItineraryStop, RequirementResult,
)
from agent import orchestrator  # noqa: E402
from agent.context_manager import context_manager  # noqa: E402
from agent import requirement_agent  # noqa: E402
from agent import planner_agent  # noqa: E402
from agent import output_formatter  # noqa: E402
from agent import travel_agent  # noqa: E402
from api import routes  # noqa: E402


# ── mock 工厂 ────────────────────────────────────────────────────────────────


def make_req(**kw):
    kw.setdefault("intent", "plan_itinerary")
    kw.setdefault("emotion", "neutral")
    kw.setdefault("entities", {"destination_city": "北京"})
    kw.setdefault("ready", True)
    kw.setdefault("missing_fields", [])
    kw.setdefault("clarification_question", None)
    return RequirementResult(**kw)


def make_itinerary(days: int = 2) -> Itinerary:
    return Itinerary(
        itinerary_id="it-1",
        destination="北京",
        duration_days=days,
        themes=["美食"],
        days=[
            ItineraryDay(day_index=i, stops=[
                ItineraryStop(stop_id=f"d{i}s1", type="attraction", name=f"景点{i}", cost=50),
            ]) for i in range(1, days + 1)
        ],
        budget=Budget(tickets=50 * days, total=50 * days),
        summary="已生成",
        created_at="2026-04-23T00:00:00Z",
    )


# ── 测试 ─────────────────────────────────────────────────────────────────────


async def test_clarification_flow():
    context_manager.clear_all()

    async def fake_analyze(msg, ent):
        return make_req(
            intent="plan_itinerary",
            entities={"destination_city": "北京"},
            ready=False,
            missing_fields=["duration_days", "themes"],
            clarification_question="请问几天?喜欢什么主题?",
        )
    orchestrator.analyze_requirement = fake_analyze

    r = await orchestrator.handle_message("s1", "帮我规划北京行程", [])
    assert r.needs_clarification is True, r
    assert r.clarification_question == "请问几天?喜欢什么主题?"
    assert r.itinerary is None
    assert r.out_of_scope is False
    print("PASS clarification_flow")


async def test_plan_flow():
    context_manager.clear_all()

    async def fake_analyze(msg, ent):
        return make_req(entities={"destination_city": "北京", "duration_days": 2, "themes": ["美食"]})
    orchestrator.analyze_requirement = fake_analyze

    async def fake_draft(req, previous_itinerary=None):
        return "## 第1天\n- 故宫...\n## 第2天\n- 颐和园...", ["search_poi", "get_transit_route"]
    orchestrator.draft_itinerary = fake_draft

    async def fake_format(draft, req):
        return make_itinerary(2)
    orchestrator.format_itinerary = fake_format

    r = await orchestrator.handle_message("s2", "规划两天", [])
    assert r.itinerary is not None
    assert r.itinerary.duration_days == 2
    assert "search_poi" in r.tools_called
    assert r.needs_clarification is False
    # last_itinerary 已存
    ctx = context_manager.get_or_create("s2")
    assert ctx.last_itinerary is not None
    assert "it-1" in ctx.itinerary_versions
    print("PASS plan_flow")


async def test_out_of_scope():
    context_manager.clear_all()

    async def fake_analyze(msg, ent):
        return make_req(intent="book_flight", ready=True, entities={})
    orchestrator.analyze_requirement = fake_analyze

    r = await orchestrator.handle_message("s3", "帮我订机票", [])
    assert r.out_of_scope is True
    assert r.itinerary is None
    print("PASS out_of_scope")


async def test_fallback_branch():
    context_manager.clear_all()

    async def fake_analyze(msg, ent):
        return make_req(intent="get_weather", ready=True, entities={"destination_city": "北京"})
    orchestrator.analyze_requirement = fake_analyze

    async def fake_run_agent(messages):
        return "今天北京晴,20°C", ["get_weather"]
    orchestrator.run_agent = fake_run_agent

    r = await orchestrator.handle_message("s4", "北京今天天气", [])
    assert r.itinerary is None
    assert "get_weather" in r.tools_called
    assert "晴" in r.reply
    print("PASS fallback_branch")


async def test_modify_flow():
    context_manager.clear_all()
    context_manager.update_entities("s5", {"destination_city": "北京"})
    context_manager.save_itinerary("s5", make_itinerary(2))

    async def fake_analyze(msg, ent):
        # 抽取器把"换"当泛问答
        return make_req(intent="inquire", ready=True, entities=ent)
    orchestrator.analyze_requirement = fake_analyze

    captured = {}

    async def fake_draft(req, previous_itinerary=None):
        captured["prev"] = previous_itinerary
        captured["intent"] = req.intent
        return "## 第1天 改后\n- 新景点\n## 第2天\n- 颐和园", ["search_poi"]
    orchestrator.draft_itinerary = fake_draft

    async def fake_format(draft, req):
        return make_itinerary(2)
    orchestrator.format_itinerary = fake_format

    r = await orchestrator.handle_message("s5", "把第二天换成别的", [])
    assert r.itinerary is not None, r
    assert captured["intent"] == "modify_itinerary", captured
    assert captured["prev"] is not None
    assert captured["prev"].itinerary_id == "it-1"
    print("PASS modify_flow")


async def test_context_manager_flow():
    context_manager.clear_all()

    ctx = context_manager.get_or_create("ctx1")
    assert ctx.session_id == "ctx1"
    assert ctx.messages == []

    context_manager.update_entities("ctx1", {"destination_city": "北京", "duration_days": None})
    context_manager.update_entities("ctx1", {"duration_days": 3, "themes": ["历史"]})
    ctx = context_manager.get_or_create("ctx1")
    assert ctx.entities["destination_city"] == "北京"
    assert ctx.entities["duration_days"] == 3
    assert ctx.entities["themes"] == ["历史"]

    await context_manager.record_turn(
        "ctx1",
        "以后价格都用美元，少走路",
        "好的",
        tools_called=["search_hotels"],
    )
    snap = context_manager.snapshot("ctx1")
    assert snap.user_preferences["currency"] == "USD"
    assert snap.user_preferences["walking_preference"] == "minimize_walking"
    assert snap.last_tools == ["search_hotels"]

    context_manager.save_itinerary("ctx1", make_itinerary(1))
    snap = context_manager.snapshot("ctx1")
    assert snap.last_itinerary_id == "it-1"
    assert "it-1" in snap.itinerary_ids

    context_manager.reset("ctx1")
    snap = context_manager.snapshot("ctx1")
    assert snap.entities == {}
    assert snap.itinerary_ids == []
    print("PASS context_manager_flow")


async def test_context_compaction_fallback():
    context_manager.clear_all()
    import agent.context_manager as cm

    old_trigger = cm.CONTEXT_SUMMARY_TRIGGER_MESSAGES
    old_recent = cm.CONTEXT_RECENT_MESSAGES
    old_summarizer = cm._summarize_with_llm

    async def fake_summarizer(previous_summary, old_messages):
        return None

    cm.CONTEXT_SUMMARY_TRIGGER_MESSAGES = 4
    cm.CONTEXT_RECENT_MESSAGES = 2
    cm._summarize_with_llm = fake_summarizer
    try:
        for i in range(3):
            await context_manager.record_turn("ctx2", f"用户{i}", f"助手{i}")
        ctx = context_manager.get_or_create("ctx2")
        assert len(ctx.messages) == 2
        assert "用户0" in ctx.summary
        assert "助手1" in ctx.summary
        print("PASS context_compaction_fallback")
    finally:
        cm.CONTEXT_SUMMARY_TRIGGER_MESSAGES = old_trigger
        cm.CONTEXT_RECENT_MESSAGES = old_recent
        cm._summarize_with_llm = old_summarizer


async def test_chat_route_context_snapshot():
    context_manager.clear_all()
    old_handle = routes.handle_message

    async def fake_handle_message(session_id, user_message):
        return orchestrator.OrchestratorResult(
            reply="请问几天?",
            tools_called=[],
            intent="plan_itinerary",
            needs_clarification=True,
            clarification_question="请问几天?",
            missing_fields=["duration_days"],
        )

    routes.handle_message = fake_handle_message
    try:
        resp = await routes.chat(routes.ChatRequest(message="帮我规划北京行程"))
        assert resp.needs_clarification is True
        assert resp.missing_fields == ["duration_days"]
        snap = await routes.get_context(resp.session_id)
        assert snap.recent_message_count == 2
        assert snap.itinerary_ids == []
        reset = await routes.reset_context(resp.session_id)
        assert reset.session_id == resp.session_id
        snap2 = context_manager.snapshot(resp.session_id)
        assert snap2.recent_message_count == 0
        print("PASS chat_route_context_snapshot")
    finally:
        routes.handle_message = old_handle


async def test_formatter_coerce_and_fallback():
    # 验证:就算 LLM 完全不可用,format_itinerary 走 fallback 也能出合法 Itinerary
    async def fake_llm(*a, **kw):
        return None
    output_formatter._llm_to_json = fake_llm

    req = make_req(entities={"destination_city": "北京", "duration_days": 3, "themes": ["历史"]})
    it = await output_formatter.format_itinerary("纯文本草稿没能解析", req)
    assert it.destination == "北京"
    assert it.duration_days == 3
    assert it.summary and "纯文本" in it.summary
    assert it.days == []
    print("PASS formatter_fallback")


async def main():
    await test_clarification_flow()
    await test_plan_flow()
    await test_out_of_scope()
    await test_fallback_branch()
    await test_modify_flow()
    await test_context_manager_flow()
    await test_context_compaction_fallback()
    await test_chat_route_context_snapshot()
    await test_formatter_coerce_and_fallback()
    print("\nAll V3 orchestrator tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
