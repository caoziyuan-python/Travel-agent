"""
V3 结构化行程数据契约 + 需求理解结果契约。

前后端约定:Itinerary 是行程的唯一真源,stop_id 稳定,用于 PATCH 定位。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 行程结构 ────────────────────────────────────────────────────────────────


class Location(BaseModel):
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None


StopType = Literal["attraction", "restaurant", "hotel", "transit", "other"]


class ItineraryStop(BaseModel):
    stop_id: str = Field(..., description="形如 d{day}s{idx},稳定且可用于 PATCH 定位")
    type: StopType
    name: str
    start_time: str | None = Field(None, description="HH:MM,可选")
    duration_min: int | None = None
    location: Location | None = None
    cost: float | None = Field(None, description="单项花费,按 currency 货币")
    currency: str = "CNY"
    notes: str | None = None


class ItineraryDay(BaseModel):
    day_index: int = Field(..., ge=1, description="1-based")
    date: str | None = Field(None, description="YYYY-MM-DD")
    stops: list[ItineraryStop]
    daily_total: float | None = None


class Budget(BaseModel):
    transport: float = 0
    accommodation: float = 0
    food: float = 0
    tickets: float = 0
    other: float = 0
    total: float = 0
    currency: str = "CNY"


class Itinerary(BaseModel):
    itinerary_id: str
    destination: str
    duration_days: int = Field(..., ge=1)
    themes: list[str] = Field(default_factory=list)
    days: list[ItineraryDay]
    budget: Budget
    summary: str | None = None
    created_at: str = Field(..., description="ISO8601")


# ── 需求理解结果 ────────────────────────────────────────────────────────────


class RequirementResult(BaseModel):
    """
    requirement_agent 的输出,也是 orchestrator 做分支的依据。

    ready=False 时:missing_fields 列出缺什么,clarification_question 给一句追问话术。
    ready=True  时:上面两个为空/None,intent + entities 直接给 planner 或兜底 agent。
    """

    intent: str | None
    emotion: str = "neutral"
    entities: dict[str, Any] = Field(default_factory=dict)
    ready: bool
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ContextSnapshot(BaseModel):
    session_id: str
    summary: str = ""
    entities: dict[str, Any] = Field(default_factory=dict)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    last_itinerary_id: str | None = None
    itinerary_ids: list[str] = Field(default_factory=list)
    recent_message_count: int = 0
    last_tools: list[str] = Field(default_factory=list)
