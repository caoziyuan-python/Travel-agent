"""
One-time script to build the RAG index from the Amap knowledge base.

Run from the project root:
    python scripts/build_rag_index.py

Outputs:
    data/rag_index/chroma/   — ChromaDB POI vector index (semantic search)
    data/rag_index/routes.db — SQLite route lookup (exact ID-based)

Requirements (install separately from backend deps):
    pip install chromadb>=0.5.0 openai>=1.0.0 python-dotenv

Env vars (same .env as the backend):
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT  (e.g. text-embedding-3-small)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import chromadb
except ImportError:
    sys.exit("ERROR: chromadb not installed. Run: pip install chromadb>=0.5.0")

try:
    from openai import OpenAI
except ImportError:
    sys.exit("ERROR: openai not installed. Run: pip install openai>=1.0.0")

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "Amap_data" / "clean"
INDEX_DIR = ROOT / "data" / "rag_index"
POIS_FILE = DATA_DIR / "knowledge_base_pois.json"
ROUTES_FILE = DATA_DIR / "knowledge_base_routes.json"
CHROMA_DIR = INDEX_DIR / "chroma"
ROUTES_DB = INDEX_DIR / "routes.db"

# ── Azure OpenAI config ────────────────────────────────────────────────────────
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
EMBED_BATCH = 100  # safe batch size for Azure OpenAI embedding API


def _build_embed_client() -> OpenAI:
    if not AZURE_API_KEY or not AZURE_ENDPOINT:
        sys.exit("ERROR: Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env")
    base_url = AZURE_ENDPOINT
    if not base_url.endswith("/openai/v1"):
        base_url = base_url + "/openai/v1/"
    else:
        base_url = base_url + "/"
    return OpenAI(api_key=AZURE_API_KEY, base_url=base_url)


def _embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=AZURE_EMBEDDING_DEPLOYMENT, input=texts)
    return [item.embedding for item in resp.data]


# ── POI text representation ────────────────────────────────────────────────────

def _poi_to_text(poi: dict) -> str:
    """Compact text fed to the embedding model."""
    loc = poi.get("location") or {}
    cats = poi.get("category") or []
    rating = poi.get("rating", "") or ""
    if isinstance(rating, list):
        rating = ""
    price = poi.get("price")
    parts = [poi.get("name") or ""]
    if cats:
        parts.append("类型:" + "、".join(cats[:3]))
    if loc.get("district"):
        parts.append("地区:" + loc["district"])
    if poi.get("address"):
        parts.append(poi["address"])
    if rating:
        parts.append("评分:" + str(rating))
    if price:
        parts.append(f"人均:{price}元")
    if poi.get("open_time_raw"):
        parts.append("营业:" + poi["open_time_raw"])
    if poi.get("keywords_tags"):
        parts.append("标签:" + poi["keywords_tags"])
    return " | ".join(parts)


def _poi_metadata(poi: dict) -> dict:
    """Flat metadata stored alongside each ChromaDB document."""
    loc = poi.get("location") or {}
    cats = poi.get("category") or []
    rating = poi.get("rating", "") or ""
    if isinstance(rating, list):
        rating = ""
    price = poi.get("price")
    return {
        "poi_id": poi.get("poi_id") or "",
        "name": poi.get("name") or "",
        "district": loc.get("district") or "",
        "lat": float(loc.get("lat") or 0),
        "lng": float(loc.get("lng") or 0),
        "category_main": cats[0] if cats else "",
        "rating": str(rating),
        "price": float(price) if price else -1.0,
        "address": poi.get("address") or "",
        "open_time_raw": poi.get("open_time_raw") or "",
        "business_status": poi.get("business_status") or "",
        "tel": poi.get("tel") or "",
    }


# ── Index builders ─────────────────────────────────────────────────────────────

def build_poi_index(client: OpenAI) -> None:
    if not POIS_FILE.exists():
        sys.exit(f"ERROR: POI file not found: {POIS_FILE}")

    print(f"Loading POIs from {POIS_FILE} ...")
    with open(POIS_FILE, encoding="utf-8") as f:
        pois: list[dict] = json.load(f)
    total = len(pois)
    print(f"  {total} POIs loaded.")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Always rebuild from scratch for a clean index
    try:
        chroma.delete_collection("pois")
        print("  Dropped existing 'pois' collection.")
    except Exception:
        pass
    collection = chroma.create_collection("pois", metadata={"hnsw:space": "cosine"})

    added = 0
    for i in range(0, total, EMBED_BATCH):
        batch = pois[i : i + EMBED_BATCH]
        texts = [_poi_to_text(p) for p in batch]
        ids = [p.get("poi_id") or f"poi_{i + j}" for j, p in enumerate(batch)]
        metas = [_poi_metadata(p) for p in batch]

        for attempt in range(2):
            try:
                embeddings = _embed_batch(client, texts)
                break
            except Exception as exc:
                if attempt == 0:
                    print(f"  Embedding error (batch {i}): {exc} — retrying in 5s...")
                    time.sleep(5)
                else:
                    sys.exit(f"ERROR: Embedding failed twice at batch {i}: {exc}")

        collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
        added += len(batch)
        if added % 2000 == 0 or added == total:
            print(f"  [{added}/{total}] POIs embedded and indexed")

    print(f"POI index complete: {added} documents in {CHROMA_DIR}\n")


def build_route_index() -> None:
    if not ROUTES_FILE.exists():
        sys.exit(f"ERROR: Routes file not found: {ROUTES_FILE}")

    print(f"Loading routes from {ROUTES_FILE} ...")
    with open(ROUTES_FILE, encoding="utf-8") as f:
        routes: list[dict] = json.load(f)
    total = len(routes)
    print(f"  {total} routes loaded.")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROUTES_DB)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS routes")
    cur.execute("""
        CREATE TABLE routes (
            origin       TEXT NOT NULL,
            destination  TEXT NOT NULL,
            mode         TEXT NOT NULL,
            duration_sec INTEGER,
            cost_cny     REAL,
            distance_m   INTEGER,
            walk_m       INTEGER,
            steps_json   TEXT,
            PRIMARY KEY (origin, destination, mode)
        )
    """)
    cur.execute("CREATE INDEX idx_od ON routes(origin, destination)")

    rows = [
        (
            r.get("origin") or "",
            r.get("destination") or "",
            r.get("mode") or "",
            r.get("estimated_duration_seconds"),
            r.get("estimated_cost"),
            r.get("distance_meters"),
            r.get("walking_distance"),
            json.dumps(r.get("steps") or [], ensure_ascii=False),
        )
        for r in routes
    ]
    cur.executemany("INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    print(f"Route index complete: {len(rows)} routes in {ROUTES_DB}\n")


def main() -> None:
    print("=" * 60)
    print("Building Amap RAG Index")
    print(f"  Embedding deployment : {AZURE_EMBEDDING_DEPLOYMENT}")
    print(f"  POI source           : {POIS_FILE}")
    print(f"  Route source         : {ROUTES_FILE}")
    print(f"  Output               : {INDEX_DIR}")
    print("=" * 60 + "\n")

    client = _build_embed_client()

    build_poi_index(client)
    build_route_index()

    print("=" * 60)
    print("RAG index ready. Start the backend and the agent can use:")
    print("  search_pois_rag  — semantic POI search")
    print("  get_route_rag    — pre-computed route lookup")
    print("=" * 60)


if __name__ == "__main__":
    main()
