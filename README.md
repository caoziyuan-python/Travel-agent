# Beijing AI Travel Agent

A multi-agent travel assistant for Beijing built on Azure OpenAI native tool calling. It combines a static RAG knowledge base (31,678 POIs + 30,450 pre-computed routes), live Amap map data, Xiaohongshu (小红书) user reviews, and real-time flight and hotel search to produce detailed, timetabled itineraries.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
   - [Agent Orchestration](#agent-orchestration)
   - [Tool Layer](#tool-layer)
   - [Data Layer](#data-layer)
3. [Directory Structure](#directory-structure)
4. [Code Overview](#code-overview)
   - [Orchestrator](#orchestrator)
   - [Requirement Agent](#requirement-agent)
   - [Planner Agent](#planner-agent)
   - [General Travel Agent (Fallback)](#general-travel-agent-fallback)
   - [Output Formatter](#output-formatter)
   - [RAG Tools](#rag-tools)
   - [Amap Tools](#amap-tools)
   - [Flight & Hotel Tools](#flight--hotel-tools)
   - [Xiaohongshu Tools](#xiaohongshu-tools)
5. [Quick Start](#quick-start)
   - [Environment Variables](#environment-variables)
   - [Run the Backend](#run-the-backend)
   - [Build the RAG Index](#build-the-rag-index)
6. [API Reference](#api-reference)
7. [How to Test](#how-to-test)
   - [CLI Testing](#cli-testing)
   - [HTTP API Testing](#http-api-testing)
   - [Unit / Integration Tests](#unit--integration-tests)
   - [System Check Script](#system-check-script)
8. [Tech Stack](#tech-stack)

---

## Features

- **Detailed multi-day itineraries** — specific timetable with opening hours, transit legs, and cost breakdown for each stop
- **RAG-powered POI search** — semantic search over 31,678 Beijing POIs with scores, prices, district, and hours
- **Pre-computed route lookup** — 30,450 transit/walking/driving route pairs with duration and fare, fetched from a local SQLite database
- **Nearby recommendations** — finds restaurants, cafes, bookstores, and leisure spots within a configurable radius of any POI
- **Live Amap fallback** — for routes not in the knowledge base, falls back to Amap REST API in real time
- **Real-time weather** — Amap weather integrated into schedule planning (outdoor spots deprioritised on rain days)
- **Flight & hotel search** — Ctrip calendar prices + Booking.com hotel listings with currency conversion (CNY ↔ USD)
- **Xiaohongshu review grounding** — searches real user notes for honest tips, recent crowd feedback, and photo spot recommendations
- **Multi-turn requirement gathering** — asks clarifying questions until destination, duration, and themes are all confirmed before planning begins

---

## Architecture

### Agent Orchestration

```
User message
    │
    ▼
[Context Manager]          accumulates session entities across turns
    │
    ▼
[Requirement Agent]        extracts intent, entities, validates completeness
    │ ready? ──No──► return clarification question
    │ Yes
    ▼
[Query Validator]          rejects out-of-scope requests (booking, cancellation)
    │
    ▼
    ├─ intent = plan_itinerary ──► [Planner Agent]  →  [Output Formatter]  →  structured Itinerary JSON
    │
    └─ all other intents ────────► [Travel Agent]  (general Q&A, weather, POI, route lookups)
```

The orchestrator is a **state machine**, not a free-form agent. A request only reaches the planner once all required fields (destination, duration, themes) are present. Until then the user receives targeted clarification questions.

### Tool Layer

Tools are exposed to LLMs in **OpenAI function-calling format**. Each agent receives only the tools it is authorised to use — the planner never sees Xiaohongshu tools to prevent off-topic detours, and the general agent never sees planner-internal tools.

```
RAG Tools (offline, preferred)
  search_pois_rag          ChromaDB semantic search — returns POI name, type, district,
                           rating, price, hours, coordinates
  get_route_rag            SQLite exact lookup by POI ID pair — returns duration, fare,
                           distance, transit steps
  get_poi_details_rag      Full detail record for a single POI ID
  search_pois_nearby_rag   Haversine-filtered nearby POI search centred on an anchor POI

Amap Live Tools (fallback)
  get_weather              Current + forecast by city code
  search_poi               Keyword POI search with category filter
  get_transit_route        Public transit directions (duration, fare, steps)
  get_driving_route        Driving directions
  get_walking_route        Walking directions
  get_taxi_route           Taxi estimate (fare range + time)

Flight & Hotel Tools
  search_flights           Ctrip calendar — cheapest fares per day in a date window
  search_hotels            Booking.com — property list with price, score, location

Xiaohongshu Tools (general agent only)
  search_notes             Searches user-generated notes by keyword via rednote-mcp MCP
```

### Data Layer

| Source | Format | How it is used |
|--------|--------|----------------|
| Amap API (offline pipeline) | JSON — `data/Amap_data/clean/` | Ingested by `scripts/build_rag_index.py` to build ChromaDB + SQLite |
| ChromaDB vector index | `data/rag_index/chroma/` | Semantic POI search (text-embedding-3-small) |
| SQLite route database | `data/rag_index/routes.db` | Exact route lookup by `(origin_id, dest_id, mode)` |
| Amap REST API | Real-time HTTP | Live route / weather fallback when not in index |
| Ctrip calendar API | Real-time HTTP | Flight prices |
| Booking.com (Playwright) | Real-time scrape | Hotel listings |
| Xiaohongshu (rednote-mcp) | Real-time MCP subprocess | User reviews and tips |

---

## Directory Structure

```
Travel-Agent/
├── backend/
│   ├── main.py                        FastAPI entry point (lifespan, CORS)
│   ├── cli.py                         Interactive CLI for local testing
│   ├── requirements.txt
│   │
│   ├── api/
│   │   ├── routes.py                  All /api/v1/* endpoints
│   │   └── schemas.py                 Pydantic models (Itinerary, ItineraryStop,
│   │                                  RequirementResult, ContextSnapshot, …)
│   │
│   ├── agent/
│   │   ├── orchestrator.py            V3 state machine
│   │   ├── requirement_agent.py       Multi-turn entity extraction + field validation
│   │   ├── requirement_extractor.py   Single-turn LLM extraction helper
│   │   ├── query_validator.py         Out-of-scope / escalation detection
│   │   ├── planner_agent.py           Itinerary planning agent (RAG + live tools)
│   │   ├── travel_agent.py            General Q&A fallback agent
│   │   ├── output_formatter.py        Draft text → structured Itinerary JSON
│   │   ├── context_manager.py         Per-session entity store + message history
│   │   ├── customer_query_understanding.py
│   │   ├── xhs_grounded_reply.py      One-shot XHS note retrieval + grounded answer
│   │   │
│   │   └── tools/
│   │       ├── rag_tools.py           ChromaDB + SQLite retrieval tools
│   │       ├── amap_tools.py          Amap REST API wrappers
│   │       ├── flight_hotel_tools.py  Flight (Ctrip) + hotel (Booking.com) tools
│   │       └── xhs_tools.py           Xiaohongshu MCP client wrapper
│   │
│   ├── utils/
│   │   └── config.py                  Env var loader + Azure URL builder
│   │
│   └── tests/
│       └── test_v3.py
│
├── scripts/
│   ├── build_rag_index.py             One-time: build ChromaDB + SQLite from clean JSON
│   ├── run_v3_checks.py               Dependency + env + API health check
│   │
│   ├── Amap_API/                      Amap offline data pipeline
│   │   └── src/
│   │       ├── run_pipeline.py        Entry: fetch, clean, export to data/Amap_data/
│   │       ├── kb_builder/
│   │       │   ├── collector.py       Raw Amap API fetch
│   │       │   └── cleaner.py        Normalise + deduplicate POIs and routes
│   │       └── utils/amap_api.py      Low-level Amap REST wrapper
│   │
│   ├── xiaohongshu/
│   │   └── local_rednote_agent/
│   │       └── chat_with_rednote.py   RedNoteMCPClient (MCP ↔ OpenAI bridge)
│   │
│   ├── flight_and_hotel_price_scraper/
│   │   └── scraper.py                 Ctrip + Booking.com scraper (Playwright)
│   │
│   └── xhs_data/                      Offline Xiaohongshu JSON snapshots
│       ├── xhs_beijing_food.json
│       ├── xhs_beijing_shopping.json
│       ├── xhs_beijing_spot.json
│       └── xhs_beijing_stay.json
│
├── data/
│   ├── Amap_data/
│   │   ├── raw/                       Raw API responses (.jsonl)
│   │   └── clean/
│   │       ├── knowledge_base_pois.json     31,678 cleaned POI records
│   │       └── knowledge_base_routes.json   30,450 pre-computed route pairs
│   │
│   └── rag_index/
│       ├── chroma/                    ChromaDB vector store (POI embeddings)
│       └── routes.db                  SQLite route lookup table
│
├── docs/
│   ├── functional_requirements.md
│   ├── required_apis.md
│   └── v3_runtime_test_checklist.md
│
└── frontend/                          Placeholder — UI not yet implemented
```

---

## Code Overview

### Orchestrator

**`backend/agent/orchestrator.py`**

The central controller. Every `/api/v1/chat` request flows through here.

```
analyze_requirement()     →  RequirementResult (intent, entities, ready flag)
QueryValidator.validate() →  is_out_of_scope, needs_escalation
route to agent:
  plan_itinerary intent   →  planner_agent.draft_itinerary()
                             output_formatter.format_itinerary()
  all other intents       →  travel_agent.run_agent()
```

Key design choice: **no planning happens until `ready=True`**. The requirement agent accumulates entities across multiple turns and generates targeted clarification questions for any missing required field.

---

### Requirement Agent

**`backend/agent/requirement_agent.py`**

Extracts structured travel requirements from conversational input.

Required fields per intent:

| Intent | Required fields |
|--------|----------------|
| `plan_itinerary` | `destination_city`, `duration_days`, `themes` |
| `search_flights` | `origin_city`, `destination_city`, `travel_date` |
| `search_hotels` | `destination_city`, `check_in_date` |
| `get_transit_route` | `origin_city`, `destination_city` |

If any field is missing the agent returns `ready=False` plus a single clarification question (generated by LLM, with a rule-based fallback template). Missing fields and values already provided are merged with session-level accumulated entities so the user never has to repeat themselves.

---

### Planner Agent

**`backend/agent/planner_agent.py`**

A dedicated itinerary-planning agent that runs up to **14 tool rounds** using a three-phase strategy:

**Phase 1 — Discovery** (batch, prefer RAG)
- `search_pois_rag` × 2 — theme-matched attractions + restaurants
- `search_hotels` or `search_pois_rag` — accommodation options

**Phase 2 — Route verification**
- `get_route_rag` for each adjacent POI pair (falls back to live Amap route tools if not in index)

**Phase 3 — Nearby enrichment**
- `search_pois_nearby_rag` — lunch spots within 1 km of each attraction
- `search_pois_nearby_rag` — cafe / bookstore / leisure stop at midday
- `get_weather` — adjust outdoor/indoor scheduling

Output is a **semi-structured natural-language draft** (one paragraph per day, stops in `-` list format). The draft is then parsed by the output formatter into strict JSON. Semi-structured output is intentional — LLMs produce more accurate itineraries in natural language than when forced to emit raw JSON directly.

The system prompt enforces:
- Transit legs between every pair of stops (🚇 / 🚶 / 🚕 format with line, duration, fare)
- No internal tool language ("已查到", "POI_ID", "RAG", etc.)
- Specific restaurant recommendations (name, per-head price, rating, signature dishes)
- Specific leisure recommendations (name, type, distance, price range)
- Single plan — no "Option A / Option B" hedging
- Budget summary table at the end
- Rednote follow-up prompt at the very end

---

### General Travel Agent (Fallback)

**`backend/agent/travel_agent.py`**

Handles everything that is not a full itinerary plan: weather queries, single-POI lookups, route questions, hotel/flight searches, and Xiaohongshu note searches.

Notable implementation details:
- **Tool result truncation** — each tool result is capped at 3,000 characters before being appended to the message history. This prevents large hotel/POI dumps from exceeding Azure's payload limit and causing `APIConnectionError` on the final composition call.
- **XHS rate limiting** — Xiaohongshu search is limited to one call per conversation turn; detail/comment endpoints are blocked to avoid triggering anti-scrape rules.
- **Hotel deduplication** — `search_hotels` is capped at one call per turn; subsequent model requests for hotels return the cached result.
- **XHS degradation** — if the MCP subprocess returns an error, the agent switches to a non-XHS tool set for the remainder of the turn.
- **Explicit timeout** — the Azure OpenAI client is built with `timeout=OPENAI_TIMEOUT_SECONDS` (default 60 s) and `max_retries=0` so failures surface immediately rather than hanging.

---

### Output Formatter

**`backend/agent/output_formatter.py`**

Converts the planner's natural-language draft into a validated `Itinerary` Pydantic model.

- Parses day headers, stop times, costs, and transport legs from the draft text
- Assigns `stop_id` values (`d1s1`, `d1s2`, …)
- Rolls up per-day and total budget figures
- Validates against the schema — any field that cannot be parsed is set to a sensible default rather than failing hard

---

### RAG Tools

**`backend/agent/tools/rag_tools.py`**

Four tools backed by a local ChromaDB collection and SQLite database built by `scripts/build_rag_index.py`.

| Tool | Backend | Description |
|------|---------|-------------|
| `search_pois_rag` | ChromaDB | Semantic search over all 31,678 POIs; returns top-k with full metadata |
| `get_route_rag` | SQLite | Exact lookup `(origin_id, dest_id, mode)` → duration, fare, steps |
| `get_poi_details_rag` | ChromaDB | Full record for one POI by ID |
| `search_pois_nearby_rag` | ChromaDB + Haversine | Two-stage: semantic candidates → Haversine filter by radius, sorted by distance |

All four are thin wrappers around synchronous library calls, bridged to `async` via `asyncio.to_thread`. The ChromaDB collection and embedding client are lazy-loaded with `@lru_cache(maxsize=1)` — first call pays the startup cost, subsequent calls are instant.

**POI text representation used for embedding:**
```
{name} | 类型:{categories} | 地区:{district} | {address} | 评分:{rating} | 人均:{price}元 | 营业:{hours} | 标签:{tags}
```

All four tools return a `"索引未构建"` message gracefully if the index files are absent, so the agent falls back to live Amap tools automatically.

---

### Amap Tools

**`backend/agent/tools/amap_tools.py`**

Thin async wrappers around the Amap REST API v3:

| Tool | Amap endpoint | Notes |
|------|--------------|-------|
| `get_weather` | `/weather/weatherInfo` | Returns current + 3-day forecast |
| `search_poi` | `/place/text` | Keyword + category search, top-20 results |
| `get_transit_route` | `/direction/transit/integrated` | Public transit — duration, fare, detailed steps |
| `get_driving_route` | `/direction/driving` | Driving — duration, distance, tolls |
| `get_walking_route` | `/direction/walking` | Walking — duration, distance |
| `get_taxi_route` | `/direction/driving` + taxi estimation | Fare range and travel time |

---

### Flight & Hotel Tools

**`backend/agent/tools/flight_hotel_tools.py`**

- **`search_flights`** — calls Ctrip's public calendar API to find the cheapest one-way fare for each day in a given window. Returns top-N results sorted by price. Maps Chinese city names to IATA codes via a built-in lookup table.
- **`search_hotels`** — scrapes Booking.com using Playwright (headless Chromium). Handles GDPR cookie consent banners, retries with four different card selectors, logs page title on failure. Returns a list of properties with price, rating, location, and URL.

Both tools support CNY / USD currency selection (auto-detected from whether the user message contains Chinese characters).

---

### Xiaohongshu Tools

**`backend/agent/tools/xhs_tools.py`**

Wraps `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py`, which manages a `rednote-mcp` MCP subprocess. The wrapper:
- Translates MCP tool descriptions into OpenAI function-calling format at runtime
- Restricts the available surface to `search_notes`-style tools only (no detail or comment chains)
- Handles subprocess startup failure gracefully (returns a user-friendly error; orchestrator degrades to non-XHS tools)

---

## Quick Start

### Environment Variables

Create `.env` in the project root:

```env
# Required
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_DEPLOYMENT=gpt-5.4          # your chat deployment name
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small  # for RAG index build
AMAP_API_KEY=your_amap_key

# Optional — shown with defaults
AGENT_TEMPERATURE=0.2
MAX_TOOL_ROUNDS=8
OPENAI_TIMEOUT_SECONDS=60
PRICE_CURRENCY=CNY
USD_CNY_RATE=7.2
LIVE_FX_ENABLED=1
CONTEXT_RECENT_MESSAGES=12
CONTEXT_SUMMARY_TRIGGER_MESSAGES=20
CONTEXT_STORE=memory

# Xiaohongshu MCP (optional)
MCP_COMMAND=npx
MCP_ARGS=rednote-mcp --stdio
```

### Run the Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

CLI (terminal chat loop):
```bash
cd backend
python cli.py
```

### Build the RAG Index

Only needed once. Requires the cleaned Amap data at `data/Amap_data/clean/`.

```bash
# Build offline knowledge base from Amap (if not already present)
cd scripts/Amap_API
pip install -r requirements.txt
python src/run_pipeline.py --date 2026-04-13   # outputs to data/Amap_data/clean/

# Build ChromaDB vector index + SQLite route table
cd ../..
python scripts/build_rag_index.py
# → data/rag_index/chroma/   (POI embeddings, ~31k documents)
# → data/rag_index/routes.db (route lookup table, ~30k rows)
```

---

## API Reference

All endpoints are under `/api/v1`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Main conversational endpoint — returns reply, tools called, and optional structured `Itinerary` |
| `DELETE` | `/chat/{session_id}` | Clear session history and accumulated entities |
| `GET` | `/context/{session_id}` | Debug: inspect current session context snapshot |
| `GET` | `/weather` | Direct Amap weather query (`city_code` param, e.g. `110000` for Beijing) |
| `GET` | `/pois` | Direct POI search (`keywords`, `city` params) |
| `GET` | `/route` | Multi-mode route (`origin`, `destination`, `mode` params) |
| `GET` | `/flights` | Flight calendar search |
| `GET` | `/hotels` | Hotel listing search |
| `GET` | `/notes` | Xiaohongshu note search |
| `POST` | `/notes/answer` | XHS-grounded answer: search notes → chunk → LLM compose |
| `GET` | `/health` | Health check |

**Example chat request:**
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "message": "帮我规划一个北京3天2夜的行程，主要想看历史古迹"
  }'
```

---

## How to Test

### CLI Testing

The fastest way to test the full agent stack end-to-end is the built-in CLI:

```bash
cd backend
python cli.py
```

The CLI starts an interactive chat loop connected to the same orchestrator as the HTTP API. You can observe which tools are called after each response. Suggested test prompts:

```
# Trigger multi-turn requirement gathering
帮我规划北京旅行

# Full 3-day itinerary (all RAG + route + nearby tools)
帮我规划一个北京3天2夜的行程，主要是历史古迹和北京特色美食，2个人

# Route lookup
从故宫到颐和园怎么走？

# Weather
北京明天天气怎么样？

# Hotel search
北京有什么好的酒店，预算500以内

# Flight search
从香港飞北京最便宜是哪天？

# Xiaohongshu notes
帮我用小红书搜一下故宫的拍照攻略
```

### HTTP API Testing

With the server running (`uvicorn main:app --reload --port 8000`):

```bash
# Health check
curl http://localhost:8000/health

# Start a conversation
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","message":"帮我规划北京3天历史主题行程，2个人"}'

# Continue the same session
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","message":"住宿预算500以内"}'

# Check session state
curl http://localhost:8000/api/v1/context/s1

# Direct weather query
curl "http://localhost:8000/api/v1/weather?city_code=110000"

# Direct POI search
curl "http://localhost:8000/api/v1/pois?keywords=故宫&city=北京"
```

The Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs) provides a browser-based form for all endpoints.

### Unit / Integration Tests

```bash
cd backend
pytest tests/test_v3.py -v
```

`tests/test_v3.py` covers the V3 orchestrator flow: requirement extraction, query validation, planner routing, and output formatting. Tests run against the real LLM by default — set `MOCK_LLM=1` in `.env` to use fixture responses (if mock mode is implemented).

### System Check Script

Validates the environment before running the server:

```bash
python scripts/run_v3_checks.py            # checks deps, env vars, module imports
python scripts/run_v3_checks.py --api      # also runs live API calls to Amap + Azure
```

This script will report which environment variables are missing, which Python packages are not installed, and whether the Azure OpenAI and Amap endpoints respond correctly.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Azure OpenAI (GPT-5 family) via native function calling |
| Embeddings | Azure OpenAI `text-embedding-3-small` |
| Vector store | ChromaDB (local, persistent) |
| Route database | SQLite (via Python `sqlite3`) |
| Backend framework | FastAPI + Uvicorn |
| HTTP client | `httpx` (async), `aiohttp` |
| Browser scraping | Playwright (headless Chromium) |
| XHS integration | `rednote-mcp` via MCP subprocess |
| Map & routing | Amap (高德地图) REST API v3 |
| Flight data | Ctrip calendar API |
| Hotel data | Booking.com (Playwright scrape) |
| Config | `python-dotenv` |
| Data validation | Pydantic v2 |
| Language | Python 3.11+ |
