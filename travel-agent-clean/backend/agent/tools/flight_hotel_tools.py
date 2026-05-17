"""
机票 / 酒店工具层

机票：
  实时调用携程 12808 日历 API（无需浏览器，速度快）
  复用 scripts/flight_and_hotel_price_scraper/scraper.py 的 fetch_flight_calendar

酒店：
  支持读取 scripts/flight_and_hotel_price_scraper 离线 JSON 缓存
  也支持 Booking.com 实时抓取（较慢，依赖 Playwright）
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

# 把项目根目录加入 sys.path，以便 import scripts.flight_and_hotel_price_scraper
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.flight_and_hotel_price_scraper.scraper import (  # noqa: E402
    fetch_flight_calendar,
    parse_flight_calendar,
    make_session,
    scrape_hotels,
)
from utils.config import (
    AMAP_API_KEY,
    PRICE_CURRENCY,
    USD_CNY_RATE,
    LIVE_FX_ENABLED,
    FX_TIMEOUT_SECONDS,
)

# 中文城市名 → IATA city code
CITY_TO_IATA: dict[str, str] = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "成都": "CTU",
    "深圳": "SZX", "武汉": "WUH", "西安": "XIY", "杭州": "HGH",
    "重庆": "CKG", "昆明": "KMG", "三亚": "SYX", "厦门": "XMN",
    "南京": "NKG", "青岛": "TAO", "大连": "DLC", "哈尔滨": "HRB",
    "长沙": "CSX", "郑州": "CGO", "天津": "TSN", "济南": "TNA",
    "福州": "FOC", "合肥": "HFE", "南昌": "KHN", "贵阳": "KWE",
    "南宁": "NNG", "兰州": "LHW", "银川": "INC", "西宁": "XNN",
    "乌鲁木齐": "URC", "拉萨": "LXA", "呼和浩特": "HET", "沈阳": "SHE",
    "长春": "CGQ",
}

HOTEL_CACHE_DIR = _PROJECT_ROOT / "data" / "hotels"
_FX_CACHE_TTL_SECONDS = 3600
_fx_cache_rate: float | None = None
_fx_cache_at: float = 0.0


# ── 同步实现 ────────────────────────────────────────────────────────────────

def _resolve_iata(city: str) -> str:
    """中文城市名 → IATA；已是 3 字母大写则原样返回。"""
    if city in CITY_TO_IATA:
        return CITY_TO_IATA[city]
    cleaned = city.strip().upper()
    if len(cleaned) == 3 and cleaned.isalpha():
        return cleaned
    raise ValueError(f"未识别的城市: {city}（请用中文城市名或 3 字母 IATA 码）")


def _normalize_currency(currency: str | None) -> str:
    cur = (currency or PRICE_CURRENCY).strip().upper()
    if cur not in {"CNY", "USD"}:
        raise ValueError(f"不支持的货币: {currency}，仅支持 CNY / USD")
    return cur


def _fetch_usd_cny_rate_live() -> float:
    # Primary source
    r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=FX_TIMEOUT_SECONDS)
    r.raise_for_status()
    data = r.json()
    rate = float(((data.get("rates") or {}).get("CNY")))
    if rate <= 0:
        raise ValueError("invalid CNY rate from live FX source")
    return rate


def _get_usd_cny_rate() -> tuple[float, str]:
    """
    Return (rate, source).
    source: live | live_cache | configured | configured_fallback
    """
    global _fx_cache_rate, _fx_cache_at
    if not LIVE_FX_ENABLED:
        return USD_CNY_RATE, "configured"

    now = time.time()
    if _fx_cache_rate is not None and now - _fx_cache_at < _FX_CACHE_TTL_SECONDS:
        return _fx_cache_rate, "live_cache"

    try:
        rate = _fetch_usd_cny_rate_live()
        _fx_cache_rate = rate
        _fx_cache_at = now
        return rate, "live"
    except Exception:
        return USD_CNY_RATE, "configured_fallback"


def _convert_price(
    price: float | int | None,
    from_currency: str,
    to_currency: str,
    usd_cny_rate: float,
) -> float | None:
    if price is None:
        return None
    p = float(price)
    frm = from_currency.upper()
    to = to_currency.upper()
    if frm == to:
        return round(p, 2)
    if frm == "USD" and to == "CNY":
        return round(p * usd_cny_rate, 2)
    if frm == "CNY" and to == "USD":
        return round(p / usd_cny_rate, 2)
    return None


def _parse_lnglat(location: str | None) -> tuple[float, float] | None:
    if not location or "," not in location:
        return None
    try:
        lng_s, lat_s = location.split(",", 1)
        return float(lng_s), float(lat_s)
    except (TypeError, ValueError):
        return None


def _haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    radius = 6_371_000
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(r_lat1) * math.cos(r_lat2) * math.sin(d_lng / 2) ** 2
    )
    return round(radius * 2 * math.asin(math.sqrt(h)), 1)


def _amap_inputtips(keywords: str, city: str = "北京") -> list[dict]:
    if not (AMAP_API_KEY and keywords):
        return []
    try:
        data = requests.get(
            "https://restapi.amap.com/v3/assistant/inputtips",
            params={"key": AMAP_API_KEY, "keywords": keywords, "city": city, "datatype": "all"},
            timeout=8,
        ).json()
    except Exception:
        return []
    tips = data.get("tips") if isinstance(data, dict) else []
    return tips if isinstance(tips, list) else []


def _first_tip_with_location(keywords: str, city: str = "北京") -> dict | None:
    for tip in _amap_inputtips(keywords, city):
        loc = _parse_lnglat(tip.get("location"))
        if loc:
            return tip
    return None


def _rank_hotels_by_reference_distance(
    hotels: list[dict],
    reference_keyword: str,
    top_n: int,
) -> tuple[list[dict], dict | None]:
    """
    Booking relevance is not geographic distance. Resolve hotel/reference coords
    via AMAP and sort by straight-line distance when possible.
    """
    if not reference_keyword:
        return hotels[:top_n], None

    reference = _first_tip_with_location(reference_keyword, "北京")
    reference_loc = _parse_lnglat((reference or {}).get("location"))
    if not reference_loc:
        return hotels[:top_n], None

    enriched: list[dict] = []
    for hotel in hotels:
        item = dict(hotel)
        query = f"{item.get('name', '')} 北京".strip()
        hotel_tip = _first_tip_with_location(query, "北京")
        hotel_loc = _parse_lnglat((hotel_tip or {}).get("location"))
        if hotel_loc:
            distance = _haversine_meters(reference_loc, hotel_loc)
            item["amap_name"] = hotel_tip.get("name")
            item["amap_address"] = hotel_tip.get("address") or item.get("address") or ""
            item["amap_location"] = hotel_tip.get("location")
            item["distance_to_keyword_meters"] = distance
            item["distance_to_keyword_km"] = round(distance / 1000, 2)
            item["distance_reference"] = reference.get("name") or reference_keyword
        else:
            item["distance_to_keyword_meters"] = None
            item["distance_to_keyword_km"] = None
            item["distance_reference"] = reference.get("name") or reference_keyword
        enriched.append(item)

    enriched.sort(key=lambda h: (
        h.get("distance_to_keyword_meters") is None,
        h.get("distance_to_keyword_meters") or 10**12,
        h.get("price_per_night") or 10**12,
    ))
    return enriched[:top_n], reference


def _search_flights(
    origin_city: str,
    destination_city: str,
    start_date: str = "",
    days: int = 7,
    top_n: int = 10,
    currency: str = PRICE_CURRENCY,
) -> dict:
    """
    实时查询国内机票最低价。
    复用 scraper.py 的 fetch_flight_calendar（携程 12808 接口）。
    """
    if not start_date:
        start_date = (datetime.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        target_currency = _normalize_currency(currency)
    except ValueError as e:
        return {"error": str(e)}
    usd_cny_rate, fx_source = _get_usd_cny_rate()

    try:
        dep = _resolve_iata(origin_city)
        arr = _resolve_iata(destination_city)
    except ValueError as e:
        return {"error": str(e)}

    session = make_session()
    try:
        payload = fetch_flight_calendar(session, dep, arr)
        records = parse_flight_calendar(
            payload, dep, arr, origin_city, destination_city, start_date, days,
        )
    except Exception as e:
        return {
            "error": f"携程 API 调用失败: {type(e).__name__}: {e}",
            "origin": origin_city,
            "destination": destination_city,
            "target_currency": target_currency,
            "fx_rate_usd_cny": USD_CNY_RATE,
        }

    # 按价格升序，取前 top_n
    records.sort(key=lambda r: r["lowest_price_cny"])
    normalized: list[dict] = []
    for r in records[:top_n]:
        price_cny = r.get("lowest_price_cny")
        converted = _convert_price(price_cny, "CNY", target_currency, usd_cny_rate)
        item = dict(r)
        item["currency"] = target_currency
        item["price"] = converted
        normalized.append(item)

    return {
        "origin": origin_city,
        "destination": destination_city,
        "from_date": start_date,
        "days": days,
        "target_currency": target_currency,
        "fx_rate_usd_cny": usd_cny_rate,
        "fx_source": fx_source,
        "found": len(records),
        "cheapest": normalized,
    }


def _list_hotel_caches() -> list[Path]:
    """按修改时间倒序返回酒店缓存 JSON 文件。"""
    if not HOTEL_CACHE_DIR.exists():
        return []
    return sorted(HOTEL_CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _apply_hotel_filters(
    hotels: list[dict],
    max_price: float | None,
    keyword: str,
    top_n: int,
    target_currency: str,
    usd_cny_rate: float,
) -> list[dict]:
    normalized: list[dict] = []
    for h in hotels:
        item = dict(h)
        original_currency = (item.get("currency") or "USD").strip().upper()
        if original_currency not in {"USD", "CNY"}:
            continue
        original_price = item.get("price_per_night")
        converted = _convert_price(original_price, original_currency, target_currency, usd_cny_rate)
        item["original_currency"] = original_currency
        item["original_price_per_night"] = original_price
        item["currency"] = target_currency
        item["price_per_night"] = converted
        normalized.append(item)

    filtered = normalized
    if max_price is not None:
        filtered = [
            h for h in filtered
            if h.get("price_per_night") is not None and h["price_per_night"] <= max_price
        ]
    if keyword:
        kw = keyword.lower()
        filtered = [
            h for h in filtered
            if kw in (h.get("name", "") + " " + h.get("address", "")).lower()
        ]

    # 按评分降序
    filtered.sort(key=lambda h: h.get("score") or 0, reverse=True)
    return filtered[:top_n]


def _search_hotels_from_cache(
    check_in: str = "",
    max_price: float | None = None,
    keyword: str = "",
    top_n: int = 10,
    currency: str = PRICE_CURRENCY,
) -> dict:
    """
    从离线缓存读酒店。需先跑 scripts/flight_and_hotel_price_scraper/scraper.py
    把数据放在 data/hotels/*.json
    """
    try:
        target_currency = _normalize_currency(currency)
    except ValueError as e:
        return {"error": str(e)}
    usd_cny_rate, fx_source = _get_usd_cny_rate()

    files = _list_hotel_caches()
    if not files:
        return {
            "source": "cache",
            "target_currency": target_currency,
            "fx_rate_usd_cny": usd_cny_rate,
            "fx_source": fx_source,
            "found": 0,
            "hotels": [],
            "note": (
                "酒店缓存为空。请先运行："
                "python scripts/flight_and_hotel_price_scraper/scraper.py "
                "--date YYYY-MM-DD --no-flights "
                f"并将输出 hotels_*.json 复制到 {HOTEL_CACHE_DIR}/"
            ),
        }

    src = files[0]
    try:
        with open(src, encoding="utf-8") as f:
            all_hotels = json.load(f)
    except Exception as e:
        return {
            "error": f"读取缓存失败: {e}",
            "source": "cache",
            "source_file": src.name,
            "target_currency": target_currency,
            "fx_rate_usd_cny": usd_cny_rate,
            "fx_source": fx_source,
        }

    filtered = _apply_hotel_filters(
        all_hotels, max_price, keyword, top_n, target_currency, usd_cny_rate
    )

    return {
        "source": "cache",
        "source_file": src.name,
        "check_in_requested": check_in,
        "target_currency": target_currency,
        "fx_rate_usd_cny": usd_cny_rate,
        "fx_source": fx_source,
        "filter": {"max_price": max_price, "keyword": keyword},
        "found": len(filtered),  # 已截断到 top_n
        "hotels": filtered,
    }


def _search_hotels_live(
    check_in: str = "",
    max_price: float | None = None,
    keyword: str = "",
    top_n: int = 10,
    currency: str = PRICE_CURRENCY,
) -> dict:
    """
    实时抓取 Booking.com（较慢，依赖 Playwright）。
    """
    try:
        target_currency = _normalize_currency(currency)
    except ValueError as e:
        return {"error": str(e)}
    usd_cny_rate, fx_source = _get_usd_cny_rate()

    if not check_in:
        check_in = (datetime.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    target = max(10, top_n * 3)
    destination = f"{keyword} 北京" if keyword else "Beijing, China"
    try:
        hotels = scrape_hotels(check_in=check_in, target=target, destination=destination)
    except Exception as e:
        return {
            "source": "live",
            "check_in_requested": check_in,
            "target_currency": target_currency,
            "fx_rate_usd_cny": usd_cny_rate,
            "fx_source": fx_source,
            "filter": {"max_price": max_price, "keyword": keyword, "booking_destination": destination},
            "found": 0,
            "hotels": [],
            "note": f"实时抓取失败: {type(e).__name__}: {e}",
        }

    if not hotels:
        return {
            "source": "live",
            "check_in_requested": check_in,
            "target_currency": target_currency,
            "fx_rate_usd_cny": usd_cny_rate,
            "fx_source": fx_source,
            "filter": {"max_price": max_price, "keyword": keyword, "booking_destination": destination},
            "found": 0,
            "hotels": [],
            "note": (
                "Booking.com 实时抓取未返回酒店数据（可能遭遇反爬拦截或页面结构变化）。"
                "请告知用户直接访问 booking.com 或携程/美团搜索北京酒店，"
                "并根据预算推荐参考价格区间：经济型¥100-300/晚，舒适型¥300-800/晚，豪华¥800+/晚。"
            ),
        }

    filtered = _apply_hotel_filters(
        hotels, max_price, "", max(target, top_n), target_currency, usd_cny_rate
    )
    reference = None
    if keyword:
        filtered, reference = _rank_hotels_by_reference_distance(filtered, keyword, top_n)
    else:
        filtered = filtered[:top_n]
    return {
        "source": "live",
        "check_in_requested": check_in,
        "target_currency": target_currency,
        "fx_rate_usd_cny": usd_cny_rate,
        "fx_source": fx_source,
        "filter": {
            "max_price": max_price,
            "keyword": keyword,
            "booking_destination": destination,
            "distance_reference": (reference or {}).get("name") if reference else None,
        },
        "found": len(filtered),  # 已截断到 top_n
        "hotels": filtered,
    }


def _search_hotels(
    check_in: str = "",
    max_price: float | None = None,
    keyword: str = "",
    top_n: int = 10,
    source: str = "auto",
    currency: str = PRICE_CURRENCY,
) -> dict:
    """
    source:
      - auto  : 优先缓存，缓存不可用时实时抓取
      - cache : 仅离线缓存
      - live  : 仅实时抓取
    """
    mode = (source or "auto").strip().lower()
    if mode not in {"auto", "cache", "live"}:
        return {"error": f"source 参数无效: {source}，可选 auto/cache/live"}

    if mode in {"auto", "cache"}:
        cached = _search_hotels_from_cache(check_in, max_price, keyword, top_n, currency)
        if mode == "cache":
            return cached
        if cached.get("found", 0) > 0:
            return cached

    return _search_hotels_live(check_in, max_price, keyword, top_n, currency)


# ── 异步封装 ────────────────────────────────────────────────────────────────

async def search_flights(
    origin_city: str,
    destination_city: str,
    start_date: str = "",
    days: int = 7,
    top_n: int = 10,
    currency: str = PRICE_CURRENCY,
) -> dict:
    return await asyncio.to_thread(
        _search_flights, origin_city, destination_city, start_date, days, top_n, currency,
    )


async def search_hotels(
    check_in: str = "",
    max_price: float | None = None,
    keyword: str = "",
    top_n: int = 10,
    source: str = "auto",
    currency: str = PRICE_CURRENCY,
) -> dict:
    return await asyncio.to_thread(
        _search_hotels, check_in, max_price, keyword, top_n, source, currency,
    )


# ── 工具调度 ────────────────────────────────────────────────────────────────

_DISPATCH: dict[str, Any] = {
    "search_flights": search_flights,
    "search_hotels": search_hotels,
}


async def call_flight_hotel_tool(name: str, arguments: dict) -> str:
    func = _DISPATCH.get(name)
    if not func:
        return f"未知机票/酒店工具: {name}"
    result = await func(**arguments)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── OpenAI function-calling 工具定义 ─────────────────────────────────────────

FLIGHT_HOTEL_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": (
                "查询国内航线最低机票价格（携程实时数据）。"
                "返回未来若干天每天的最低价。仅做信息查询，不下单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_city": {
                        "type": "string",
                        "description": "出发城市中文名，如 '北京'、'上海'",
                    },
                    "destination_city": {
                        "type": "string",
                        "description": "目的地城市中文名，如 '成都'、'三亚'",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "查询起始日期，格式 YYYY-MM-DD，默认一周后",
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询天数窗口，默认 7",
                        "default": 7,
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回最便宜的前 N 条，默认 10",
                        "default": 10,
                    },
                    "currency": {
                        "type": "string",
                        "description": "输出货币：CNY 或 USD，默认读取 PRICE_CURRENCY",
                        "enum": ["CNY", "USD"],
                    },
                },
                "required": ["origin_city", "destination_city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": (
                "搜索北京酒店（支持 Booking.com 离线缓存或实时抓取）。"
                "source=auto 时优先缓存，缓存不可用则实时抓取。仅做信息查询，不下单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "check_in": {
                        "type": "string",
                        "description": "入住日期，格式 YYYY-MM-DD，可选",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "每晚最高价（按 currency 货币），可选",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "酒店名称或地址关键词，可选",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回前 N 条酒店，默认 10",
                        "default": 10,
                    },
                    "source": {
                        "type": "string",
                        "description": "数据来源：auto/cache/live，默认 auto",
                        "enum": ["auto", "cache", "live"],
                    },
                    "currency": {
                        "type": "string",
                        "description": "输出货币：CNY 或 USD，默认读取 PRICE_CURRENCY",
                        "enum": ["CNY", "USD"],
                    },
                },
                "required": [],
            },
        },
    },
]
