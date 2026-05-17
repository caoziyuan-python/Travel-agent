import json
import time
import math
from pathlib import Path
from typing import Any, Dict, List, Set, Optional

import requests

class AmapCollector:
    def __init__(self, api_key: str, out_root: Path, sleep_sec: float = 0.2):
        if not api_key:
            raise ValueError("AMAP_API_KEY is empty. Set it in .env.")
        self.api_key = api_key
        self.base_url = "https://restapi.amap.com"
        self.out_root = out_root
        self.raw_dir = self.out_root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.sleep_sec = sleep_sec
        self.session = requests.Session()
        
        # Track seen logs to enable resumable downloads
        self.seen_logs: Dict[str, Set[str]] = {}

    def _get_seen_keys(self, dataset: str) -> Set[str]:
        if dataset in self.seen_logs:
            return self.seen_logs[dataset]
        
        seen = set()
        p = self.raw_dir / f"{dataset}.jsonl"
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if "req_key" in record:
                            seen.add(record["req_key"])
                    except json.JSONDecodeError:
                        pass
        self.seen_logs[dataset] = seen
        return seen

    def _calculate_distance(self, loc1: str, loc2: str) -> float:
        """Approximate distance in kilometers between two lng,lat strings."""
        try:
            lng1, lat1 = map(float, loc1.split(','))
            lng2, lat2 = map(float, loc2.split(','))
            R = 6371.0 # Earth radius in km
            dlng = math.radians(lng2 - lng1)
            dlat = math.radians(lat2 - lat1)
            a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2)**2
            return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
        except Exception:
            return 999.0 # Fallback distance if coordinate string is invalid

    def _request_and_save(self, dataset: str, endpoint: str, req_key: str, params: dict[str, Any]) -> dict[str, Any]:
        """Generic request with auto-save and duplicate checks."""
        seen = self._get_seen_keys(dataset)
        # Skip if already requested
        if req_key in seen:
            return {}

        url = f"{self.base_url}{endpoint}"
        full_params = {"key": self.api_key, **params}
        try:
            r = self.session.get(url, params=full_params, timeout=30)
            payload = r.json()
            record = {
                "req_key": req_key,
                "dataset": dataset,
                "endpoint": endpoint,
                "params": full_params,
                "response": payload
            }
            # Append locally instantly to prevent data loss
            with (self.raw_dir / f"{dataset}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
            seen.add(req_key) # Update memory
            time.sleep(self.sleep_sec)
            return payload
        except Exception as e:
            print(f"Request Error on {endpoint} [{req_key}]: {e}")
            return {}

    def collect_pois_by_keywords(self, city: str, keywords: List[str], pages: int = 5) -> List[Dict]:
        print(f"1. Collecting base POIs ({len(keywords)} keywords)")
        for kw in keywords:
            for page in range(1, pages + 1):
                req_key = f"{city}_{kw}_p{page}"
                res = self._request_and_save("poi_search", "/v3/place/text", req_key, {
                    "keywords": kw, "city": city, "offset": 20, "page": page, "extensions": "all"
                })
        
        # Load from file to pass to next step
        return self._load_pois_from_log("poi_search")

    def collect_poi_details(self, pois: List[Dict]) -> None:
        """Fetch details for all POIs."""
        valid_pois = [p for p in pois if p.get("id")]
        total = len(valid_pois)
        print(f"2. Collecting details for {total} POIs...")
        for i, poi in enumerate(valid_pois, 1):
            pid = poi["id"]
            print(f"   Progress: {i}/{total} POI details processed...", end="\r", flush=True)
            self._request_and_save("poi_detail", "/v3/place/detail", pid, {"id": pid})
        print("\n   Done.")

    def collect_around_facilities(self, target_pois: List[Dict], radius: int = 1500) -> None:
        """Search surrounding facilities (dining/shopping/leisure/etc.) for core POIs."""
        target_types = ["050000", "060000", "080000", "070000", "150000"]
        
        # Deduplicate to strictly ensure no redundant requests, even if taxonomy overlaps
        unique_pois = {p["id"]: p for p in target_pois if p.get("id") and p.get("location")}
        valid_pois = list(unique_pois.values())
        
        total_calls = len(valid_pois) * len(target_types)
        print(f"3. Searching around {len(valid_pois)} core POIs ({total_calls} maximum calls)...")
        
        count = 0
        for poi in valid_pois:
            location = poi.get("location")
            pid = poi.get("id", location)
            
            for t in target_types:
                count += 1
                req_key = f"around_{pid}_{t}"
                print(f"   Progress: {count}/{total_calls} around searches processed...", end="\r", flush=True)
                self._request_and_save("poi_around", "/v3/place/around", req_key, {
                    "location": location, "types": t,
                    "radius": radius, "offset": 20, "extensions": "base"
                })
        print("\n   Done.")

    def collect_static_routes(self, hotels: List[Dict], attractions: List[Dict], dining: List[Dict], shopping: List[Dict], hubs: List[Dict], city: str) -> None:
        """Build matrix of static routes between origin/dest pairs."""
        
        pairs = []
        # 0. Hub <-> Hotel (Airport/Station transfer to Hotel, round trip)
        for h in hotels:
            for hub in hubs:
                pairs.append((hub, h))  # Arrival
                pairs.append((h, hub))  # Departure
                
        # 1. Hotel -> Attraction (Morning departure)
        for h in hotels:
            for a in attractions:
                pairs.append((h, a))
                
        # 2. Attraction -> Dining / Shopping (Lunch or afternoon activities)
        for a in attractions:
            for d in dining:
                pairs.append((a, d))
            for s in shopping:
                pairs.append((a, s))
                
        # 3. Dining / Shopping -> Hotel (Evening return)
        for d in dining:
            for h in hotels:
                pairs.append((d, h))
        for s in shopping:
            for h in hotels:
                pairs.append((s, h))

        valid_pairs = [(o, d) for o, d in pairs if o.get("id") != d.get("id") and o.get("location") and d.get("location")]
        total_pairs = len(valid_pairs)
        print(f"4. Building static routes matrix for {total_pairs} destination pairs using smart distance pruning...")

        for i, (o, d) in enumerate(valid_pairs, 1):
            o_loc = o.get("location")
            d_loc = d.get("location")
            route_key_base = f"{o.get('id')}_to_{d.get('id')}"
            dist_km = self._calculate_distance(o_loc, d_loc)
            
            print(f"   Progress: {i}/{total_pairs} route pairs processed...", end="\r", flush=True)
            
            # Distance-based pruning: Only query sensible modes for the distance
            # Driving and Transit are almost always sensible inside the same city
            self._request_and_save("route_static", "/v3/direction/driving", f"drive_{route_key_base}", {"origin": o_loc, "destination": d_loc})
            self._request_and_save("route_static", "/v3/direction/transit/integrated", f"transit_{route_key_base}", {"origin": o_loc, "destination": d_loc, "city": city})
            
            # Walking is only requested if distance is under 5km (nobody walks > 5km normally)
            if dist_km <= 5.0:
                self._request_and_save("route_static", "/v3/direction/walking", f"walk_{route_key_base}", {"origin": o_loc, "destination": d_loc})
            
            # Bicycling is only requested if distance is under 15km
            if dist_km <= 15.0:
                self._request_and_save("route_static", "/v4/direction/bicycling", f"bike_{route_key_base}", {"origin": o_loc, "destination": d_loc})
        print("\n   Done.")

    def _load_pois_from_log(self, dataset: str) -> List[Dict]:
        pois = []
        p = self.raw_dir / f"{dataset}.jsonl"
        if not p.exists(): return pois
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    record = json.loads(line)
                    response = record.get("response", {})
                    pois.extend(response.get("pois", []))
                except Exception: pass
        return pois