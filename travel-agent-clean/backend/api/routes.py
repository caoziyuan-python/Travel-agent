"""
FastAPI 路由 — 前端可接入的 API 接口

所有接口挂载在 /api/v1 前缀下（在 main.py 中注册）。
Swagger 文档：启动后访问 http://localhost:8000/docs
"""
from __future__ import annotations

import asyncio
import json
import uuid

import requests as _http
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent.context_manager import context_manager
from agent.tools.amap_tools import (
    get_weather,
    search_poi,
    get_transit_route,
    get_driving_route,
    get_taxi_route,
    get_walking_route,
)
from agent.tools.xhs_tools import call_xhs_tool
from agent.tools.flight_hotel_tools import search_flights, search_hotels
from agent.xhs_grounded_reply import search_notes_and_answer
from agent.orchestrator import handle_message, reset_state
from api.schemas import ContextSnapshot, Itinerary

router = APIRouter()


# ── Pydantic 模型 ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息", example="推荐故宫附近的午餐，然后告诉我怎么坐地铁去颐和园")
    session_id: str = Field("", description="会话 ID，留空自动生成新会话")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Agent 回答")
    tools_called: list[str] = Field(..., description="本轮调用的工具名列表")
    session_id: str = Field(..., description="会话 ID，用于后续多轮对话")
    intent: str | None = Field(None, description="本次请求识别出的用户意图（需求理解模块）")
    out_of_scope: bool = Field(False, description="请求是否被判定为越界（如订机票/取消等）")
    escalation: str | None = Field(None, description="是否需要客服升级（如检测到愤怒情绪）")
    needs_clarification: bool = Field(False, description="需求信息不全,需要向用户追问")
    clarification_question: str | None = Field(None, description="追问话术;needs_clarification=true 时必填")
    missing_fields: list[str] = Field(default_factory=list, description="缺失字段列表")
    itinerary: Itinerary | None = Field(None, description="结构化行程;plan_itinerary 分支产出")
    itinerary_id: str | None = Field(None, description="结构化行程 ID")
    context_summary: str | None = Field(None, description="当前会话长期上下文摘要")


class SessionResetResponse(BaseModel):
    session_id: str
    message: str


class NotesAnswerRequest(BaseModel):
    query: str = Field(..., description="用户问题或检索关键词", example="北京故宫怎么安排半天行程")
    top_k: int = Field(6, ge=1, le=12, description="送给 GPT 的证据块数量")
    max_chunk_chars: int = Field(900, ge=300, le=2000, description="单块最大字符数")
    overlap_chars: int = Field(120, ge=0, le=400, description="相邻切块重叠字符数")
    search_limit: int = Field(5, ge=3, le=10, description="search_notes 返回条数上限（建议 3-5）")
    extra_prompt: str = Field("", description="额外提示词（可选）")


# ── 主对话接口 ────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, summary="主对话接口（前端主入口）")
async def chat(req: ChatRequest):
    """
    V3 编排流程（orchestrator 内部已串好）：
      需求理解(累积 entities) → 越界拦截 → 反问 / 规划分支 / 兜底 agent
    """
    session_id = req.session_id or str(uuid.uuid4())

    result = await handle_message(
        session_id=session_id,
        user_message=req.message,
    )

    # 越界请求不写入对话历史；反问/正常回复会写入,供下一轮补字段或修改行程使用。
    await context_manager.record_turn(
        session_id=session_id,
        user_message=req.message,
        assistant_reply=result.reply,
        tools_called=result.tools_called,
        itinerary=result.itinerary,
        persist_messages=not result.out_of_scope,
    )
    snapshot = context_manager.snapshot(session_id)

    return ChatResponse(
        reply=result.reply,
        tools_called=result.tools_called,
        session_id=session_id,
        intent=result.intent,
        out_of_scope=result.out_of_scope,
        escalation=result.escalation,
        needs_clarification=result.needs_clarification,
        clarification_question=result.clarification_question,
        missing_fields=result.missing_fields,
        itinerary=result.itinerary,
        itinerary_id=result.itinerary_id,
        context_summary=snapshot.summary or result.context_summary,
    )


@router.delete("/chat/{session_id}", response_model=SessionResetResponse, summary="重置会话")
async def reset_session(session_id: str):
    """清除指定会话的对话历史与编排器累积状态。"""
    reset_state(session_id)
    return SessionResetResponse(session_id=session_id, message="会话已重置")


@router.get("/context/{session_id}", response_model=ContextSnapshot, summary="查看会话上下文")
async def get_context(session_id: str):
    """返回当前 session 的上下文快照,用于调试前端多轮状态。"""
    return context_manager.snapshot(session_id)


@router.delete("/context/{session_id}", response_model=SessionResetResponse, summary="清空会话上下文")
async def reset_context(session_id: str):
    """清除指定会话的上下文。"""
    reset_state(session_id)
    return SessionResetResponse(session_id=session_id, message="上下文已重置")


# ── 高德直连接口（前端可独立调用，无需经过 Agent） ───────────────────────────

@router.get("/weather", summary="实时天气查询")
async def weather_query(
    city_code: str = Query("110000", description="高德城市编码，北京=110000"),
):
    """查询城市实时天气，直接返回高德原始数据。"""
    return await get_weather(city_code)


@router.get("/pois", summary="POI 搜索")
async def poi_search(
    keywords: str = Query(..., description='搜索关键词，如"故宫"、"三里屯餐厅"'),
    city: str = Query("北京", description="城市名"),
):
    """搜索景点、餐厅、酒店等 POI，返回名称、地址和经纬度。"""
    return await search_poi(keywords, city)


@router.get("/route", summary="路线规划")
async def route_query(
    origin: str = Query(..., description="出发地经纬度，格式：116.397,39.918"),
    destination: str = Query(..., description="目的地经纬度，格式：116.397,39.918"),
    mode: str = Query("transit", description="交通方式：transit（公交）/ driving（驾车）/ taxi（打车）/ walking（步行）"),
    city: str = Query("北京", description="城市名（公交模式必填）"),
):
    """
    规划路线，支持公交、驾车、步行三种模式。
    经纬度可通过 /pois 接口获取。
    """
    if mode == "transit":
        return await get_transit_route(origin, destination, city)
    elif mode == "driving":
        return await get_driving_route(origin, destination)
    elif mode == "taxi":
        return await get_taxi_route(origin, destination)
    elif mode == "walking":
        return await get_walking_route(origin, destination)
    else:
        raise HTTPException(status_code=400, detail="mode 必须为 transit / driving / taxi / walking")


@router.get("/taxi", summary="打车路线与费用预估")
async def taxi_query(
    origin: str = Query(..., description="出发地经纬度，格式：116.397,39.918"),
    destination: str = Query(..., description="目的地经纬度，格式：116.397,39.918"),
):
    """
    独立打车接口：
    - 返回高德驾车路线基础上的打车预估（taxi_cost）
    - 同时返回时长、里程等信息，便于前端直接展示
    """
    return await get_taxi_route(origin, destination)


# ── 机票 / 酒店直连接口 ──────────────────────────────────────────────────────

@router.get("/flights", summary="机票最低价查询（携程实时）")
async def flights_query(
    origin: str = Query(..., description="出发城市中文名，如 '北京'"),
    destination: str = Query(..., description="目的地城市中文名，如 '成都'"),
    start_date: str = Query("", description="查询起始日期 YYYY-MM-DD，默认一周后"),
    days: int = Query(7, ge=1, le=30, description="查询天数窗口"),
    top_n: int = Query(10, ge=1, le=30, description="返回最便宜的前 N 条"),
):
    """
    查询国内航线最低机票价格。
    - 数据源：携程 12808 日历 API
    - 仅信息查询，不下单
    """
    return await search_flights(origin, destination, start_date, days, top_n)


@router.get("/hotels", summary="北京酒店搜索（缓存）")
async def hotels_query(
    check_in: str = Query("", description="入住日期 YYYY-MM-DD（可选）"),
    max_price: float | None = Query(None, description="每晚最高价（可选）"),
    keyword: str = Query("", description="名称或地址关键词（可选）"),
    top_n: int = Query(10, ge=1, le=50, description="返回前 N 条"),
    source: str = Query("auto", description="数据来源：auto/cache/live"),
):
    """
    搜索北京酒店（基于 Booking.com 离线缓存）。
    - 数据源：scripts/flight_and_hotel_price_scraper 抓取的 JSON
    - 缓存路径：data/hotels/*.json（按修改时间取最新）
    - 仅信息查询，不下单
    """
    return await search_hotels(check_in, max_price, keyword, top_n, source)


# ── 小红书直连接口 ────────────────────────────────────────────────────────────

@router.get("/notes", summary="小红书攻略搜索")
async def notes_search(
    keyword: str = Query(..., description='搜索关键词，如"北京故宫攻略"、"北京必吃美食"'),
    tool: str = Query("search_notes", description="rednote-mcp 工具名，默认 search_notes"),
):
    """
    调用 scripts/xiaohongshu/local_rednote_agent 搜索小红书笔记。
    tool 参数可指定具体工具名（以 /tools 接口返回的列表为准）。
    """
    try:
        if tool != "search_notes":
            raise HTTPException(
                status_code=400,
                detail="为降低风控风险，/notes 当前仅允许 tool=search_notes，不支持详情/评论链式抓取。",
            )

        arguments = {"keywords": keyword}

        result = await call_xhs_tool(tool, arguments)
        return {"keyword": keyword, "tool": tool, "arguments": arguments, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XHS tool call failed: {type(e).__name__}: {e}")


@router.get("/tools", summary="查看已加载的 XHS 工具列表")
async def list_xhs_tools():
    """返回 rednote-mcp 暴露的所有工具名，用于确认 /notes 可用的 tool 参数值。"""
    from agent.tools.xhs_tools import get_xhs_tool_names, get_xhs_tool_definitions
    return {
        "xhs_tools": get_xhs_tool_names(),
        "definitions": get_xhs_tool_definitions(),
    }


@router.get("/static-map", summary="Amap static map proxy")
async def static_map_proxy(
    stops: str = Query(..., description='JSON array of {lat, lng, type, name}'),
    size: str = Query("580x260", description="Image size WxH"),
):
    """
    Proxies Amap Static Map API. Accepts stop coordinates, returns a PNG
    with labelled markers and a route polyline. API key stays server-side.
    """
    from utils.config import AMAP_API_KEY
    if not AMAP_API_KEY:
        raise HTTPException(status_code=503, detail="Amap API key not configured")

    try:
        stop_list: list[dict] = json.loads(stops)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid stops JSON")

    valid = [s for s in stop_list if s.get("lat") and s.get("lng")]
    if not valid:
        raise HTTPException(status_code=400, detail="No stops with coordinates")

    TYPE_COLORS = {
        "attraction": "0xC41E3A",
        "restaurant":  "0xFF6B00",
        "hotel":       "0x1565C0",
        "transit":     "0x555555",
        "other":       "0x7B1FA2",
    }

    marker_parts = []
    for i, s in enumerate(valid[:26]):
        label = chr(65 + i)
        color = TYPE_COLORS.get(s.get("type", "other"), "0xC41E3A")
        marker_parts.append(f"mid,{color},{label}:{s['lng']},{s['lat']}")

    path_coords = ";".join(f"{s['lng']},{s['lat']}" for s in valid)
    path = f"5,0xC41E3A,0.8,,:{path_coords}"

    avg_lat = sum(s["lat"] for s in valid) / len(valid)
    avg_lng = sum(s["lng"] for s in valid) / len(valid)

    params = {
        "key": AMAP_API_KEY,
        "location": f"{avg_lng:.6f},{avg_lat:.6f}",
        "zoom": "13",
        "size": size.replace("x", "*"),
        "scale": "2",
        "markers": "|".join(marker_parts),
        "paths": path,
    }

    def _fetch() -> _http.Response:
        return _http.get("https://restapi.amap.com/v3/staticmap", params=params, timeout=8)

    try:
        r = await asyncio.to_thread(_fetch)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Amap returned {r.status_code}")
        return Response(content=r.content, media_type="image/png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Map fetch failed: {exc}")


@router.post("/notes/answer", summary="小红书检索 + 切块 + GPT 生成回答")
async def notes_grounded_answer(req: NotesAnswerRequest):
    """
    面向前端的一体化接口：
    1) search_notes 检索
    2) 结果解析与切块
    3) 相关片段送入 GPT
    4) 返回可直接展示的回答 + 来源列表
    """
    try:
        return await search_notes_and_answer(
            query=req.query,
            top_k=req.top_k,
            max_chunk_chars=req.max_chunk_chars,
            overlap_chars=req.overlap_chars,
            search_limit=req.search_limit,
            extra_prompt=req.extra_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"notes/answer failed: {type(e).__name__}: {e}")
