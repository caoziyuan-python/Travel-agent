# Amap Travel Knowledge Base & Agent Tools Builder

This project is a scalable data pipeline designed to build a highly structured Knowledge Base containing Hotels, Attractions, Restaurants, and their interconnecting transit matrices (Driving, Walking, Cycling, Public Transit). It also includes dynamic tool functions meant to be directly attached to an LLM Travel Agent (e.g. OpenAI, LangChain).

## 1) Architecture

The project has been refactored into modular components to support scalability and resumable network downloads:

- `src/run_pipeline.py`: The main controller script. It builds a matrix of 30,000 combinatorial route permutations (50x50 pairs) and dumps all data into clean JSONs.
- `src/kb_builder/collector.py`: A robust scraper with built-in resumable file logging (`.jsonl`). If it crashes or you hit rate limits, re-running the script picks up exactly where it left off.
- `src/kb_builder/cleaner.py`: Transforms nested raw JSON outputs from Amap into a flat, RAG-friendly database entity array. Extracts advanced AI semantic features like `traffic_lights`, `steps`, and `walking_distance`.
- `src/agent_tools/amap_tools.py`: Contains strictly real-time and dynamic wrappers like `get_weather`, `get_transit_route`, and `get_input_tips`. These shouldn't be put into the static database but given to your Agent as `Function Calling` tools.
- `src/utils/amap_api.py`: Small helper to fetch the API Key.

## 2) Prerequisites & Setup

1. Make sure you have your `.env` file set up in the root directory:
```env
AMAP_API_KEY=your_key_here
```

2. Install requirements:
```bash
pip install -r requirements.txt
```

## 3) How to run the Data Pipeline

Simply invoke the main pipeline script. You can optionally supply it a `--date` argument for data versioning:

```bash
python src/run_pipeline.py --date 2026-04-09
```

This pipeline will do the following:
1. Search via keywords to fetch seed POIs (Hotels, Views, Dining, Shopping, etc.)
2. Download detailed reviews and operational hours for the POIs.
3. Perform an "Around Search" to discover restaurants and transportation precisely radiating from those main POIs in a 1.5km radius.
4. Calculate and cache all `Transit`, `Driving`, `Walking`, and `Cycling` parameters between randomly sampled spots (50 attractions x 50 hotels x 50 restaurants).
5. Clean the `raw/*.jsonl` data dumps and structure the final AI-ready array.

## 4) Output Data Formats

Once the pipeline successfully finishes its run, you can find the refined data securely stored inside `../data/knowledge_base/dt=<YYYY-MM-DD>/clean/`:

- `knowledge_base_pois.json`: Features `lat/lng` bounds, exact price level, operation times, review rating, phone numbers and semantic tags (`keywords_tags`).
- `knowledge_base_routes.json`: A route connection table showing accurate `estimated_duration_seconds`, real-world distances, public transit `segment_count` (transfers), and `traffic_lights`. Load this directly into your local database to construct real itineraries without experiencing LLM Hallucinations!
