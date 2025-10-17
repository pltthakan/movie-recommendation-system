import os
import datetime
from functools import lru_cache

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
import requests



load_dotenv()

TMDB_KEY = os.getenv("TMDB_API_KEY")
assert TMDB_KEY, "Lütfen TMDB_API_KEY ortam değişkenini ayarlayın."

app = Flask(__name__, template_folder="templates", static_folder="static")

TMDB_BASE = "https://api.themoviedb.org/3"



# --------- TMDB yardımcıları (Değişiklik yok) ---------
def tmdb_get(path, params=None):
    params = params or {}
    params["api_key"] = TMDB_KEY
    params["language"] = params.get("language", "tr-TR")
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


@lru_cache(maxsize=512)
def get_genres():
    return tmdb_get("/genre/movie/list")["genres"]


# --------- Sayfalar ---------
@app.route("/")
def home():
    page = int(request.args.get("page", 1))
    yeni = tmdb_get("/movie/now_playing", {"page": page})
    trend = tmdb_get("/trending/movie/week", {"page": 1})
    genres = get_genres()
    years = list(range(datetime.datetime.now().year, 1970, -1))
    # render_template çağrısından 'user' parametresi kaldırıldı.
    return render_template("index.html",
                           yeni=yeni, trend=trend, genres=genres, years=years)


@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    detail = tmdb_get(
        f"/movie/{movie_id}",
        {
            "append_to_response": "videos,credits,release_dates",
            "include_video_language": "tr-TR,en-US,en,null",
        },
    )
    recs = tmdb_get(f"/movie/{movie_id}/recommendations", {"page": 1})

    def _pref_list(vs):
        allowed = {"Trailer", "Teaser", "Clip"}
        vs = [v for v in vs if v.get("type") in allowed]
        tr = [v for v in vs if v.get("iso_639_1") == "tr"]
        en = [v for v in vs if (v.get("iso_639_1") in ("en", "en-US", None))]
        if tr: return tr
        if en: return en
        return vs

    videos_all = (detail.get("videos") or {}).get("results") or []
    chosen = _pref_list(videos_all)
    if not chosen:
        en_only = tmdb_get(f"/movie/{movie_id}/videos", {"language": "en-US"}).get("results", [])
        chosen = _pref_list(en_only)
    detail.setdefault("videos", {})["results"] = chosen


    return render_template(
        "detail.html",
        movie=detail,
        recs=recs,
    )




@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    if not q:
        empty = {"results": [], "page": 1, "total_pages": 1}

        return render_template("search.html", q=q, results=empty)
    results = tmdb_get("/search/movie", {"query": q, "page": page, "include_adult": False})

    return render_template("search.html", q=q, results=results)


@app.route("/api/discover")
def api_discover():
    genre_id = request.args.get("genre_id")
    year = request.args.get("year")
    sort_by = request.args.get("sort_by", "popularity.desc")
    vote_gte = request.args.get("vote_gte")
    page = int(request.args.get("page", 1))

    params = {"sort_by": sort_by, "page": page, "with_original_language": "en|tr"}
    if genre_id: params["with_genres"] = genre_id
    if year: params["primary_release_year"] = year
    if vote_gte: params["vote_average.gte"] = vote_gte

    data = tmdb_get("/discover/movie", params)
    return jsonify(data)




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)