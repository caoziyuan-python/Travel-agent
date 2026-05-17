import os
import requests
from typing import Dict, Any

def get_amap_key() -> str:
    key = os.getenv("AMAP_API_KEY", "")
    if not key:
        raise ValueError("AMAP_API_KEY not found. Please set it in .env file.")
    return key

def get_weather(city_code: str) -> Dict[str, Any]:
    """Dynamic weather API query (Agent Tool)."""
    url = "https://restapi.amap.com/v3/weather/weatherInfo"
    params = {
        "key": get_amap_key(),
        "city": city_code,
        "extensions": "all"
    }
    resp = requests.get(url, params=params, timeout=10).json()
    return resp

def get_transit_route(origin_lnglat: str, dest_lnglat: str, city: str = "北京") -> Dict[str, Any]:
    """Dynamic transit route query (Agent Tool)."""
    url = "https://restapi.amap.com/v3/direction/transit/integrated"
    params = {
        "key": get_amap_key(),
        "origin": origin_lnglat,
        "destination": dest_lnglat,
        "city": city,
        "extensions": "all"
    }
    resp = requests.get(url, params=params, timeout=10).json()
    return resp

def get_driving_route(origin_lnglat: str, dest_lnglat: str) -> Dict[str, Any]:
    """Dynamic driving route query (Agent Tool)."""
    url = "https://restapi.amap.com/v3/direction/driving"
    params = {
        "key": get_amap_key(),
        "origin": origin_lnglat,
        "destination": dest_lnglat,
        "extensions": "all"
    }
    resp = requests.get(url, params=params, timeout=10).json()
    return resp

def get_walking_route(origin_lnglat: str, dest_lnglat: str) -> Dict[str, Any]:
    """Dynamic walking route query (Agent Tool)."""
    url = "https://restapi.amap.com/v3/direction/walking"
    params = {
        "key": get_amap_key(),
        "origin": origin_lnglat,
        "destination": dest_lnglat
    }
    resp = requests.get(url, params=params, timeout=10).json()
    return resp

def get_input_tips(keywords: str, city: str = "北京") -> Dict[str, Any]:
    """Dynamic input suggestion / autocomplete (Agent Tool).
    Helps resolve ambiguous or misspelled user input to exact POI names and locations.
    """
    url = "https://restapi.amap.com/v3/assistant/inputtips"
    params = {
        "key": get_amap_key(),
        "keywords": keywords,
        "city": city,
        "datatype": "all"
    }
    resp = requests.get(url, params=params, timeout=10).json()
    return resp
