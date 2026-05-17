import os

def get_amap_key() -> str:
    key = os.getenv("AMAP_API_KEY", "")
    if not key:
        raise ValueError("AMAP_API_KEY not found. Please set it in .env file.")
    return key