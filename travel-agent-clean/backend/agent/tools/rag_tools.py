"""
Static Amap knowledge base RAG tools.

Two tools for the travel agent:
  search_pois_rag — semantic POI search via ChromaDB vector index
  get_route_rag   — exact pre-computed route lookup via SQLite

Build the index first (one-time):
    python scripts/build_rag_index.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

# Lazy imports so the backend starts even if chromadb isn't installed yet
try:
    import chromadb as _chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False

from openai import OpenAI
from utils.config import AZURE_BASE_URL, AZURE_API_KEY

EMBEDDING_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

# project_root/data/rag_index/  (4 levels up from this file)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_INDEX_DIR = _ROOT / "data" / "rag_index"
_CHROMA_DIR = _INDEX_DIR / "chroma"
_ROUTES_DB = _INDEX_DIR / "routes.db"

_MODE_LABEL = {"drive": "驾车", "transit": "公交/地铁", "walk": "步行", "bike": "骑行"}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Lazy singletons ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _embed_client() -> OpenAI:
    return OpenAI(api_key=AZURE_API_KEY, base_url=AZURE_BASE_URL)


@lru_cache(maxsize=1)
def _chroma_collection():
    if not _CHROMADB_AVAILABLE:
        raise RuntimeError("chromadb not installed. Run: pip install chromadb>=0.5.0")
    if not _CHROMA_DIR.exists():
        raise RuntimeError(f"RAG index not found at {_CHROMA_DIR}. Run: python scripts/build_rag_index.py")
    client = _chromadb.PersistentClient(path=str(_CHROMA_DIR))
    return client.get_collection("pois")


def _embed_query(text: str) -> list[float]:
    resp = _embed_client().embeddings.create(model=EMBEDDING_DEPLOYMENT, input=[text])
    return resp.data[0].embedding


def _routes_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_ROUTES_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ── Tool implementations ───────────────────────────────────────────────────────

def search_pois_rag(query: str, district: str | None = None, top_k: int = 8) -> str:
    """Semantic POI search against the static Amap knowledge base."""
    if not _CHROMADB_AVAILABLE:
        return "RAG工具不可用：chromadb未安装，请运行 pip install chromadb>=0.5.0"
    if not _CHROMA_DIR.exists():
        return "RAG索引未构建，请先运行: python scripts/build_rag_index.py"

    try:
        collection = _chroma_collection()
        top_k = min(max(1, int(top_k)), 20)
        q_vec = _embed_query(query)

        where = {"district": district} if district else None
        results = collection.query(
            query_embeddings=[q_vec],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        ids = results.get("ids", [[]])[0]
        if not ids:
            return f"知识库中未找到与{query}相关的地点。"

        lines = [f"【静态知识库】搜索: {query}" + (f" | 地区: {district}" if district else "") + "\n"]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for meta, dist in zip(metas, dists):
            rating = meta.get("rating") or "无"
            price = meta.get("price", -1)
            price_str = f"{price:.0f}元" if price and price > 0 else "未知"
            lat, lng = meta.get("lat", 0), meta.get("lng", 0)
            lines.append(
                f"● {meta['name']}  (POI_ID: {meta['poi_id']})\n"
                f"  类型: {meta.get('category_main', '')} | 地区: {meta.get('district', '')}\n"
                f"  地址: {meta.get('address', '')}\n"
                f"  评分: {rating} | 人均: {price_str} | 营业: {meta.get('open_time_raw', '')}\n"
                f"  坐标: {lat},{lng} | 相关度: {1 - dist:.2f}\n"
            )

        lines.append("提示: 使用 get_route_rag(origin_poi_id, dest_poi_id) 查询两地间预计算路线。")
        return "\n".join(lines)

    except Exception as exc:
        return f"POI搜索失败: {exc}"


def get_route_rag(origin_poi_id: str, dest_poi_id: str, mode: str | None = None) -> str:
    """Exact lookup of a pre-computed route between two POI IDs."""
    if not _ROUTES_DB.exists():
        return "路线索引未构建，请先运行: python scripts/build_rag_index.py"

    try:
        conn = _routes_conn()
        cur = conn.cursor()
        if mode:
            cur.execute(
                "SELECT * FROM routes WHERE origin=? AND destination=? AND mode=?",
                (origin_poi_id, dest_poi_id, mode),
            )
        else:
            cur.execute(
                "SELECT * FROM routes WHERE origin=? AND destination=?",
                (origin_poi_id, dest_poi_id),
            )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return (
                f"知识库中未找到 {origin_poi_id} → {dest_poi_id} 的预计算路线"
                + (f"（{_MODE_LABEL.get(mode, mode)}）" if mode else "")
                + "。路线可能超出采样范围，请使用实时路线工具（get_transit_route / get_driving_route）。"
            )

        lines = [f"【静态路线数据】{origin_poi_id} → {dest_poi_id}\n"]
        for row in rows:
            mins = int((row["duration_sec"] or 0) / 60)
            dist_km = (row["distance_m"] or 0) / 1000
            label = _MODE_LABEL.get(row["mode"], row["mode"])
            cost = row["cost_cny"] or 0
            summary = f"● {label}: 约{mins}分钟 | {dist_km:.1f}km | 费用:{cost:.1f}元"
            if row["walk_m"] and row["mode"] == "transit":
                summary += f" | 步行换乘:{row['walk_m']}m"
            lines.append(summary)

            steps: list[dict] = json.loads(row["steps_json"] or "[]")
            for step in steps[:5]:
                step_type = step.get("type", "")
                if step.get("line"):
                    lines.append(f"    → 乘{step['line']}（{step.get('stations', '')}站）")
                elif step.get("instruction"):
                    lines.append(f"    → {step['instruction']}")
                elif step_type in ("walk", "drive", "bike") and step.get("distance"):
                    lines.append(f"    → {step_type} {step['distance']}m")
            lines.append("")

        return "\n".join(lines)

    except Exception as exc:
        return f"路线查询失败: {exc}"


def get_poi_details_rag(poi_id: str) -> str:
    """Fetch full details of a single POI by its ID from the static knowledge base."""
    if not _CHROMADB_AVAILABLE:
        return "RAG工具不可用：chromadb未安装"
    if not _CHROMA_DIR.exists():
        return "RAG索引未构建，请先运行: python scripts/build_rag_index.py"
    try:
        collection = _chroma_collection()
        result = collection.get(ids=[poi_id], include=["metadatas"])
        if not result["ids"]:
            return f"知识库中未找到 POI_ID={poi_id}"
        meta = result["metadatas"][0]
        rating = meta.get("rating") or "无"
        price = meta.get("price", -1)
        price_str = f"{price:.0f}元/人" if price and price > 0 else "未知"
        return (
            f"【POI详情】{meta['name']} (ID: {poi_id})\n"
            f"  类型: {meta.get('category_main', '')} | 地区: {meta.get('district', '')}\n"
            f"  地址: {meta.get('address', '')}\n"
            f"  评分: {rating} | 人均: {price_str}\n"
            f"  营业时间: {meta.get('open_time_raw', '未知')}\n"
            f"  电话: {meta.get('tel', '无')}\n"
            f"  坐标: {meta.get('lat', 0)},{meta.get('lng', 0)}\n"
            f"  状态: {meta.get('business_status', '未知')}"
        )
    except Exception as exc:
        return f"POI详情查询失败: {exc}"


def search_pois_nearby_rag(poi_id: str, category_query: str, radius_km: float = 1.5, top_k: int = 6) -> str:
    """Find POIs near a given POI (by ID) within radius_km matching the category query.

    Useful for finding lunch spots near an attraction, or leisure stops midway between two places.
    """
    if not _CHROMADB_AVAILABLE:
        return "RAG工具不可用：chromadb未安装"
    if not _CHROMA_DIR.exists():
        return "RAG索引未构建，请先运行: python scripts/build_rag_index.py"
    try:
        collection = _chroma_collection()

        # Step 1: get center coordinates from the anchor POI
        anchor = collection.get(ids=[poi_id], include=["metadatas"])
        if not anchor["ids"]:
            return f"未找到锚点 POI_ID={poi_id}，无法执行附近搜索"
        anchor_meta = anchor["metadatas"][0]
        center_lat = float(anchor_meta.get("lat") or 0)
        center_lng = float(anchor_meta.get("lng") or 0)
        district = anchor_meta.get("district", "")

        if center_lat == 0 and center_lng == 0:
            return f"POI {poi_id} 缺少坐标信息，无法执行附近搜索"

        # Step 2: semantic search with category query + district filter to get candidates
        q_vec = _embed_query(category_query)
        where = {"district": district} if district else None
        candidates = collection.query(
            query_embeddings=[q_vec],
            n_results=60,
            where=where,
            include=["metadatas", "distances"],
        )

        # Step 3: filter by haversine distance, exclude the anchor POI itself
        ranked: list[tuple[float, dict]] = []
        for meta, _ in zip(candidates["metadatas"][0], candidates["distances"][0]):
            if meta.get("poi_id") == poi_id:
                continue
            plat = float(meta.get("lat") or 0)
            plng = float(meta.get("lng") or 0)
            if plat == 0 and plng == 0:
                continue
            d_km = _haversine(center_lat, center_lng, plat, plng)
            if d_km <= radius_km:
                ranked.append((d_km, meta))

        ranked.sort(key=lambda x: x[0])
        top = ranked[: min(top_k, len(ranked))]

        if not top:
            return (
                f"在 {anchor_meta['name']} 附近 {radius_km}km 内未找到与[{category_query}]相关的地点。\n"
                f"可尝试扩大 radius_km 或换用 search_pois_rag 在整个城区搜索。"
            )

        lines = [
            f"【附近地点】{anchor_meta['name']} 周边 {radius_km}km | 查询: {category_query}\n"
        ]
        for dist_km, meta in top:
            rating = meta.get("rating") or "无"
            price = meta.get("price", -1)
            price_str = f"{price:.0f}元" if price and price > 0 else "未知"
            walk_min = int(dist_km / 0.08)  # rough 80m/min walking speed
            lines.append(
                f"● {meta['name']}  (POI_ID: {meta['poi_id']}) — 距离{dist_km:.2f}km (~步行{walk_min}min)\n"
                f"  类型: {meta.get('category_main', '')} | 地址: {meta.get('address', '')}\n"
                f"  评分: {rating} | 人均: {price_str} | 营业: {meta.get('open_time_raw', '')}\n"
            )
        return "\n".join(lines)

    except Exception as exc:
        return f"附近搜索失败: {exc}"


# ── OpenAI tool definitions ────────────────────────────────────────────────────

RAG_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_pois_rag",
            "description": (
                "从本地静态知识库语义搜索北京POI（景点/餐厅/酒店/购物/服务）。"
                "返回最相关地点列表，含POI_ID、坐标、评分、价格、营业时间。"
                "无需外部API调用，速度快。返回的POI_ID可传入 get_route_rag 查询两地间路线。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": '搜索关键词，如"胡同咖啡馆"、"免费博物馆"、"四合院餐厅"、"亲子游景点"',
                    },
                    "district": {
                        "type": "string",
                        "description": '可选：按行政区过滤，如"东城区"、"朝阳区"、"海淀区"、"西城区"',
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数，默认8，最多20",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_rag",
            "description": (
                "从本地静态知识库查询两个POI之间的预计算路线（驾车/公交/步行/骑行）。"
                "需要 origin_poi_id 和 dest_poi_id（从 search_pois_rag 结果中获取）。"
                "返回各交通方式的时间、距离、费用和分段步骤。"
                "若知识库中无此路线对，提示改用实时路线工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_poi_id": {
                        "type": "string",
                        "description": "出发地的POI_ID（由 search_pois_rag 返回）",
                    },
                    "dest_poi_id": {
                        "type": "string",
                        "description": "目的地的POI_ID（由 search_pois_rag 返回）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["drive", "transit", "walk", "bike"],
                        "description": "可选：指定交通方式；不填则返回所有可用方式",
                    },
                },
                "required": ["origin_poi_id", "dest_poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_poi_details_rag",
            "description": (
                "从本地知识库获取某个POI的完整详情（营业时间、评分、人均价格、电话、坐标）。"
                "需要POI_ID（由 search_pois_rag 返回）。用于核实开放时间或获取精确门票/价格信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {
                        "type": "string",
                        "description": "POI的唯一ID（由 search_pois_rag 返回）",
                    },
                },
                "required": ["poi_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_pois_nearby_rag",
            "description": (
                "在某个POI周边 radius_km 范围内，语义搜索匹配 category_query 的地点。"
                "适合：在景点附近找午餐餐厅、在两地中途找休闲/购物场所。"
                "需要锚点的 poi_id（由 search_pois_rag 返回），内部自动读取其坐标。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "poi_id": {
                        "type": "string",
                        "description": "锚点POI的ID，在其附近搜索",
                    },
                    "category_query": {
                        "type": "string",
                        "description": '搜索类别关键词，如"北京菜餐厅 小吃"、"咖啡馆 休闲"、"购物中心 超市"',
                    },
                    "radius_km": {
                        "type": "number",
                        "description": "搜索半径（公里），默认1.5，最大5",
                        "default": 1.5,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数，默认6，最多15",
                        "default": 6,
                    },
                },
                "required": ["poi_id", "category_query"],
            },
        },
    },
]

_DISPATCH: dict[str, Any] = {
    "search_pois_rag": search_pois_rag,
    "get_route_rag": get_route_rag,
    "get_poi_details_rag": get_poi_details_rag,
    "search_pois_nearby_rag": search_pois_nearby_rag,
}

RAG_TOOL_NAMES: frozenset[str] = frozenset(_DISPATCH)


async def call_rag_tool(name: str, arguments: dict) -> str:
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"未知RAG工具: {name}"
    return await asyncio.to_thread(fn, **arguments)
