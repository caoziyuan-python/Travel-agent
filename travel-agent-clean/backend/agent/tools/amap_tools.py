"""
高德地图工具层
- 提供 async 查询函数（天气、POI、路线）
- 提供 OpenAI function-calling 格式的工具定义
- 提供统一的 call_amap_tool() 调度入口
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import requests

from utils.config import AMAP_API_KEY


def _key() -> str:
    if not AMAP_API_KEY:
        raise ValueError("AMAP_API_KEY 未配置，请在 .env 中设置")
    return AMAP_API_KEY


# ── 同步实现（由 asyncio.to_thread 包装） ────────────────────────────────────

def _get_weather(city_code: str = "110000") -> dict:
    return requests.get(
        "https://restapi.amap.com/v3/weather/weatherInfo",
        params={"key": _key(), "city": city_code, "extensions": "all"},
        timeout=10,
    ).json()


def _search_poi(keywords: str, city: str = "北京") -> dict:
    return requests.get(
        "https://restapi.amap.com/v3/assistant/inputtips",
        params={"key": _key(), "keywords": keywords, "city": city, "datatype": "all"},
        timeout=10,
    ).json()


def _get_transit_route(origin: str, destination: str, city: str = "北京") -> dict:
    return requests.get(
        "https://restapi.amap.com/v3/direction/transit/integrated",
        params={
            "key": _key(), "origin": origin, "destination": destination,
            "city": city, "extensions": "all",
        },
        timeout=10,
    ).json()


def _get_driving_route(origin: str, destination: str) -> dict:
    return requests.get(
        "https://restapi.amap.com/v3/direction/driving",
        params={"key": _key(), "origin": origin, "destination": destination, "extensions": "all"},
        timeout=10,
    ).json()


def _get_taxi_route(origin: str, destination: str) -> dict:
    """
    打车路线（出租车/网约车）：
    复用高德驾车路线接口，并返回 taxi_cost / distance / duration 等关键字段。
    """
    raw = requests.get(
        "https://restapi.amap.com/v3/direction/driving",
        params={"key": _key(), "origin": origin, "destination": destination, "extensions": "all"},
        timeout=10,
    ).json()

    route = raw.get("route", {}) if isinstance(raw, dict) else {}
    paths = route.get("paths", []) if isinstance(route, dict) else []
    first_path = paths[0] if paths and isinstance(paths[0], dict) else {}

    return {
        "provider": "amap_driving_based_taxi_estimate",
        "origin": origin,
        "destination": destination,
        "taxi_cost": route.get("taxi_cost"),
        "distance_meters": first_path.get("distance"),
        "duration_seconds": first_path.get("duration"),
        "tolls": first_path.get("tolls"),
        "raw": raw,
    }


def _get_walking_route(origin: str, destination: str) -> dict:
    return requests.get(
        "https://restapi.amap.com/v3/direction/walking",
        params={"key": _key(), "origin": origin, "destination": destination},
        timeout=10,
    ).json()


# ── 异步封装 ─────────────────────────────────────────────────────────────────

async def get_weather(city_code: str = "110000") -> dict:
    return await asyncio.to_thread(_get_weather, city_code)


async def search_poi(keywords: str, city: str = "北京") -> dict:
    return await asyncio.to_thread(_search_poi, keywords, city)


async def get_transit_route(origin: str, destination: str, city: str = "北京") -> dict:
    return await asyncio.to_thread(_get_transit_route, origin, destination, city)


async def get_driving_route(origin: str, destination: str) -> dict:
    return await asyncio.to_thread(_get_driving_route, origin, destination)


async def get_taxi_route(origin: str, destination: str) -> dict:
    return await asyncio.to_thread(_get_taxi_route, origin, destination)


async def get_walking_route(origin: str, destination: str) -> dict:
    return await asyncio.to_thread(_get_walking_route, origin, destination)


# ── 工具调度（Agent 调用入口） ───────────────────────────────────────────────

_DISPATCH: dict[str, Any] = {
    "get_weather": get_weather,
    "search_poi": search_poi,
    "get_transit_route": get_transit_route,
    "get_driving_route": get_driving_route,
    "get_taxi_route": get_taxi_route,
    "get_walking_route": get_walking_route,
}


async def call_amap_tool(name: str, arguments: dict) -> str:
    func = _DISPATCH.get(name)
    if not func:
        return f"未知高德工具: {name}"
    result = await func(**arguments)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── OpenAI function-calling 工具定义 ─────────────────────────────────────────

AMAP_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询城市天气信息。北京城市编码为 110000。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city_code": {"type": "string", "description": "高德城市编码，北京=110000"},
                },
                "required": ["city_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_poi",
            "description": "搜索景点、餐厅、酒店、购物中心等 POI，返回名称、地址和经纬度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "string", "description": '搜索关键词，如"故宫"、"三里屯餐厅"'},
                    "city": {"type": "string", "description": "城市名，默认北京"},
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transit_route",
            "description": "查询公共交通（地铁/公交）路线。需要先用 search_poi 获取经纬度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发地经纬度，格式：116.397,39.918"},
                    "destination": {"type": "string", "description": "目的地经纬度，格式：116.397,39.918"},
                    "city": {"type": "string", "description": "城市，默认北京"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_driving_route",
            "description": "查询驾车路线，返回时间、距离和导航步骤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发地经纬度"},
                    "destination": {"type": "string", "description": "目的地经纬度"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_taxi_route",
            "description": "查询打车（出租车/网约车）路线，返回预估费用、时长、里程。需要先用 search_poi 获取经纬度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发地经纬度，格式：116.397,39.918"},
                    "destination": {"type": "string", "description": "目的地经纬度，格式：116.397,39.918"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_walking_route",
            "description": "查询步行路线，返回时间、距离和步行导航。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发地经纬度"},
                    "destination": {"type": "string", "description": "目的地经纬度"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
]
