import argparse
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import os

from utils.amap_api import get_amap_key
from kb_builder.collector import AmapCollector
from kb_builder.cleaner import AmapCleaner

def main():
    load_dotenv()
    api_key = get_amap_key()
    city_code = "110000"
    
    # Allow manual date specification via command line
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().date().isoformat())
    args = parser.parse_args()

    out_root = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge_base" / f"dt={args.date}"
    out_root.mkdir(parents=True, exist_ok=True)

    collector = AmapCollector(api_key, out_root)
    cleaner = AmapCleaner(out_root)

    # 1. Fetch base POI keywords (expanded to 7 categories, 5 pages each), estimated calls = 7 categories * 5 pages = 35 calls.
    keywords = ["北京 景点", "北京 博物馆", "北京 餐厅", "北京 酒店", "北京 购物", "北京 休闲", "北京 生活服务"]
    pois_from_search = collector.collect_pois_by_keywords(city=city_code, keywords=keywords, pages=5)
    
    # 1.5 Fetch major Transportation Hubs explicitly
    hub_keywords = ["北京首都国际机场", "北京大兴国际机场", "北京南站", "北京西站", "北京站", "北京朝阳站"]
    hubs_from_search = collector.collect_pois_by_keywords(city=city_code, keywords=hub_keywords, pages=1)

    # Merge results and deduplicate by POI id to fix 1400+ redundant processing
    unique_pois_dict = {}
    for p in pois_from_search + hubs_from_search:
        if p.get("id"):
            unique_pois_dict[p["id"]] = p
    all_raw_pois = list(unique_pois_dict.values())
    
    # Filter using high-level taxonomy/type matching (or specific names for hubs)
    attractions = [p for p in all_raw_pois if "风景名胜" in p.get("type", "") or "博物馆" in p.get("type", "")]
    hotels = [p for p in all_raw_pois if "酒店" in p.get("type", "")]
    dining = [p for p in all_raw_pois if "餐饮" in p.get("type", "")]
    shopping = [p for p in all_raw_pois if "购物" in p.get("type", "")]
    hubs = [p for p in hubs_from_search if "交通" in p.get("type", "") or "机场" in p.get("name", "") or "站" in p.get("name", "")]

    # 2. Fetch detailed POI data, estimated calls = number of POIs (up to 7 categories * 5 pages * 20 per page = 700) + hubs (6) = ~706 calls.
    collector.collect_poi_details(all_raw_pois)
    
    # 3. Full surrounding search (around ALL attractions, hotels, and hubs), estimated calls = (attractions + hotels + dining + shopping + hubs) * 5 types (dining/shopping/leisure/life/transport) = (400+6)*5 = 2030 calls.
    collector.collect_around_facilities(attractions + hotels + dining + shopping + hubs, radius=1500)
    
    # 4. Build a matrix of static routes
    # Randomly sample 50 from each category to perform combinatorial routing << (3+2+1) * 2500 *4 = 75,000
    random.seed(42) # For reproducible results
    sampled_attractions = random.sample(attractions, min(50, len(attractions)))
    sampled_hotels = random.sample(hotels, min(50, len(hotels)))
    sampled_dining = random.sample(dining, min(50, len(dining)))
    sampled_shopping = random.sample(shopping, min(50, len(shopping)))
    sampled_hubs = random.sample(hubs, min(10, len(hubs))) # Hubs are usually just top 4-6

    collector.collect_static_routes(
        sampled_hotels, 
        sampled_attractions, 
        sampled_dining, 
        sampled_shopping, 
        sampled_hubs, 
        city=city_code
    )

    # 5. Clean data and build consolidated knowledge base
    cleaner.run_cleaning_pipeline()



    # # test case
    # keywords = ["北京 景点", "北京 餐厅", "北京 酒店"]
    # pois_from_search = collector.collect_pois_by_keywords(city=city_code, keywords=keywords, pages=1)  # 3 calls
    
    # attractions = [p for p in pois_from_search if "风景名胜" in p.get("type", "") or "博物馆" in p.get("type", "")]
    # hotels = [p for p in pois_from_search if "酒店" in p.get("type", "")]
    # dining = [p for p in pois_from_search if "餐饮" in p.get("type", "")]

    # collector.collect_poi_details(pois_from_search) # 3 * 1 * 20 = 60 calls

    # collector.collect_around_facilities(attractions, radius=1500) # 20 attractions * 5 types = 100 calls

    # random.seed(42)
    # sampled_attractions = random.sample(attractions, min(5, len(attractions)))
    # sampled_hotels = random.sample(hotels, min(5, len(hotels)))
    # sampled_dining = random.sample(dining, min(5, len(dining)))
    # collector.collect_static_routes(sampled_hotels, sampled_attractions, sampled_dining, city=city_code) # 5*5*3*4 = 300 calls

    # cleaner.run_cleaning_pipeline()


if __name__ == "__main__":
    main()