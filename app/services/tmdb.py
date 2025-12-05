# app/services/tmdb.py
import requests
from functools import lru_cache
from flask import current_app

def tmdb_get(path, params=None):
    params = params or {}
    params["api_key"] = current_app.config["TMDB_API_KEY"]
    params.setdefault("language", "tr-TR")
    base = current_app.config["TMDB_BASE"]
    r = requests.get(f"{base}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

@lru_cache(maxsize=512)
def get_genres():
    return tmdb_get("/genre/movie/list")["genres"]
