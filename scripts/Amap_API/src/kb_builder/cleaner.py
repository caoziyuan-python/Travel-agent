import json
from pathlib import Path
from typing import Dict, List, Set, Any

class AmapCleaner:
    def __init__(self, in_dir: Path):
        self.raw_dir = in_dir / "raw"
        self.clean_dir = in_dir / "clean"
        self.clean_dir.mkdir(parents=True, exist_ok=True)
    
    def run_cleaning_pipeline(self) -> None:
        print("5. Cleaning data and building final business tables...")
        pois_data = self._clean_pois()
        routes_data = self._clean_routes()
        
        with (self.clean_dir / "knowledge_base_pois.json").open("w", encoding="utf-8") as f:
            json.dump(pois_data, f, ensure_ascii=False, indent=2)
            
        with (self.clean_dir / "knowledge_base_routes.json").open("w", encoding="utf-8") as f:
            json.dump(routes_data, f, ensure_ascii=False, indent=2)
            
        print(f"Done. Extracted {len(pois_data)} POIs and {len(routes_data)} route strategies.")

    def _clean_pois(self) -> List[Dict]:
        kb_data = []
        files_to_clean = ["poi_detail.jsonl", "poi_around.jsonl"]
        
        for file_name in files_to_clean:
            file_path = self.raw_dir / file_name
            if not file_path.exists(): continue
                
            with file_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                    except Exception: continue
                        
                    pois = record.get("response", {}).get("pois", [])
                    if not isinstance(pois, list): continue
                        
                    for poi in pois:
                        biz_ext = poi.get("biz_ext")
                        if not isinstance(biz_ext, dict): biz_ext = {}
                        poiweight = poi.get("poiweight")
                        if not isinstance(poiweight, dict): poiweight = {}
                        photos = poi.get("photos")
                        if not isinstance(photos, list): photos = []
                        loc_str = poi.get("location", "0,0")
                        if not loc_str or "," not in loc_str: loc_str = "0,0"

                        cost_str = biz_ext.get("cost", "")
                        if isinstance(cost_str, list):
                            cost_val = None
                        else:
                            try:
                                cost_val = float(cost_str)
                            except (ValueError, TypeError):
                                cost_val = None

                        import re
                        open_str = biz_ext.get("opentime2", "") or biz_ext.get("open_time", "")
                        open_structured = {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []}
                        if isinstance(open_str, str) and open_str:
                            times = re.findall(r'\d{2}:\d{2}-\d{2}:\d{2}', open_str)
                            if times:
                                # Deduplicate to avoid multiple ["09:00-17:00", "09:00-17:00"]
                                unique_times = list(dict.fromkeys(times))
                                for key in open_structured:
                                    open_structured[key] = unique_times

                        clean_poi = {
                            "poi_id": poi.get("id"),
                            "name": poi.get("name"),
                            "category": poi.get("type", "").split(";"), 
                            "location": {
                                "lat": float(loc_str.split(",")[1]),
                                "lng": float(loc_str.split(",")[0]),
                                "district": poi.get("adname", "")
                            },
                            "address": poi.get("address") or "",
                            "business_status": "Open" if poiweight.get("business_status") == "1" else "Unknown",
                            "rating": biz_ext.get("rating", "N/A"),
                            "price": cost_val,
                            "open_time_raw": open_str,
                            # "open_time_structured": open_structured,
                            "recommended_visit_time_hours": None,  # Not natively provided by Amap API base response
                            "tel": poi.get("tel") or "",
                            "keywords_tags": poi.get("atag") or "",
                            "photos": [p.get("url") for p in photos[:3] if isinstance(p, dict)]
                        }
                        kb_data.append(clean_poi)

        # Deduplicate
        seen_ids = set()
        unique_kb = []
        for item in kb_data:
            if item["poi_id"] and item["poi_id"] not in seen_ids:
                seen_ids.add(item["poi_id"])
                unique_kb.append(item)
        return unique_kb

    def _clean_routes(self) -> List[Dict]:
        """Clean static route cache, supports driving, walking, transit, bicycling."""
        routes_kb = []
        p = self.raw_dir / "route_static.jsonl"
        if not p.exists(): return routes_kb

        with p.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                    req_key = record.get("req_key", "")
                    resp = record.get("response", {})
                    # Process transit routes
                    if req_key.startswith("transit_"):
                        route_obj = resp.get("route", {})
                        transits = route_obj.get("transits", [])
                        if not transits: continue
                        cost_val = transits[0].get("cost", 0)
                        duration_val = transits[0].get("duration", 0)
                    else:
                        # Process walking, driving, bicycling routes
                        route_obj = resp.get("route", {})
                        paths = route_obj.get("paths", [])
                        if not paths: continue
                        cost_val = paths[0].get("tolls", 0) # Potential tolls
                        duration_val = paths[0].get("duration", 0)

                    mode = req_key.split("_")[0]
                    orig_dest = req_key.split("_", 1)[-1]
                    if "_to_" in orig_dest:
                        orig, dest = orig_dest.split("_to_")
                    else:
                        orig, dest = orig_dest, ""

                    try:
                        est_cost = float(cost_val) if cost_val else 0.0
                    except (ValueError, TypeError):
                        est_cost = 0.0

                    route_data = {
                        "origin": orig,
                        "destination": dest,
                        "mode": mode,
                        "estimated_duration_seconds": int(duration_val) if duration_val else 0,
                        "estimated_cost": est_cost,
                    }
                    
                    # Extract advanced semantic columns
                    if req_key.startswith("transit_"):
                        route_data["distance_meters"] = int(transits[0].get("distance", 0))
                        route_data["walking_distance"] = int(transits[0].get("walking_distance", 0))
                        route_data["nightflag"] = transits[0].get("nightflag", "0")
                        
                        steps = []
                        for segment in transits[0].get("segments", []):
                            walking = segment.get("walking", {})
                            if isinstance(walking, dict) and "distance" in walking:
                                steps.append({"type": "walk", "distance": int(walking.get("distance", 0))})
                            
                            bus_info = segment.get("bus", {})
                            if isinstance(bus_info, dict) and bus_info.get("buslines"):
                                for line in bus_info.get("buslines", []):
                                    steps.append({
                                        "type": line.get("type", "bus"),
                                        "line": line.get("name", "Unknown Line"),
                                        "stations": int(line.get("via_num", 0) if line.get("via_num") else 0)
                                    })
                        route_data["steps"] = steps
                    else:
                        route_data["distance_meters"] = int(paths[0].get("distance", 0)) if paths else 0
                        if req_key.startswith("drive_"):
                            route_data["traffic_lights"] = int(paths[0].get("traffic_lights", 0)) if paths else 0
                            route_data["restriction"] = paths[0].get("restriction", "0") if paths else "0"

                        # Extract discrete steps for driving, walking, bicycling
                        mode_label = "drive" if req_key.startswith("drive_") else ("walk" if req_key.startswith("walk_") else "bike")
                        steps = []
                        if paths:
                            for step_obj in paths[0].get("steps", []):
                                if isinstance(step_obj, dict):
                                    dist = step_obj.get("distance", 0)
                                    instruction = step_obj.get("instruction", "")
                                    action = step_obj.get("action", "")
                                    steps.append({
                                        "type": mode_label,
                                        "distance": int(dist) if dist else 0,
                                        "instruction": instruction,
                                        "action": action
                                    })
                        route_data["steps"] = steps

                    routes_kb.append(route_data)
                except Exception as e:
                    pass
        return routes_kb
