# app/services/tmdb.py
import requests
from functools import lru_cache
from flask import current_app

def tmdb_get(path, params=None):
    params = dict(params or {})
    params.setdefault("language", "tr-TR")
    base = current_app.config["TMDB_BASE"]
    access_token = current_app.config.get("TMDB_READ_ACCESS_TOKEN")
    api_key = current_app.config.get("TMDB_API_KEY")

    # TMDB v4 Read Access Tokens are JWTs and must be sent as a Bearer header.
    # Supporting the JWT in the old variable keeps existing local .env files
    # working while users migrate to TMDB_READ_ACCESS_TOKEN.
    if access_token or (api_key and api_key.startswith("eyJ") and api_key.count(".") == 2):
        token = access_token or api_key
        headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    else:
        headers = None
        params["api_key"] = api_key

    r = requests.get(f"{base}{path}", params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

@lru_cache(maxsize=512)
def get_genres():
    return tmdb_get("/genre/movie/list")["genres"]
