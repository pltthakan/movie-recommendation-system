# app/blueprints/api.py
import json
from flask import Blueprint, request, jsonify, session
from ..services.tmdb import tmdb_get
from ..services.auth import login_required
from ..services.events import log_event
from ..services.recommender import invalidate_user_cache, get_or_build_user_profile, get_candidate_cache, user_signals_hash
from ..services.embeddings import SentenceTransformer  # optional
from ..db import db
from ..services.utils import now_utc

bp = Blueprint("api", __name__)

@bp.get("/api/discover")
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

    return jsonify(tmdb_get("/discover/movie", params))

@bp.get("/api/search_suggest")
def api_search_suggest():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})

    data = tmdb_get("/search/movie", {"query": q, "page": 1, "include_adult": False})
    results = []
    for m in (data.get("results") or [])[:8]:
        results.append({
            "id": m.get("id"),
            "title": m.get("title"),
            "poster_path": m.get("poster_path"),
            "vote_average": m.get("vote_average"),
            "release_date": m.get("release_date"),
        })
    return jsonify({"results": results})

@bp.get("/api/featured")
def api_featured():
    popular = tmdb_get("/movie/popular", {"page": 1})
    nowp    = tmdb_get("/movie/now_playing", {"page": 1})

    seen, results = set(), []
    for lst in (popular.get("results", []) + nowp.get("results", [])):
        mid = lst.get("id")
        if mid and mid not in seen and (lst.get("backdrop_path") or lst.get("poster_path")):
            seen.add(mid)
            results.append(lst)
        if len(results) >= 20:
            break
    return jsonify({"results": results})

@bp.post("/api/trailer_event")
@login_required
def api_trailer_event():
    data = request.get_json(silent=True) or {}
    mid = data.get("movie_id") or request.form.get("movie_id")
    try:
        mid = int(mid)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_movie_id"}), 400

    with db() as con, con.cursor() as cur:
        cur.execute("""
            INSERT INTO trailer_events(user_id, movie_id, event_type, created_at)
            VALUES (%s,%s,'watch_trailer',%s)
        """, (session["user_id"], mid, now_utc()))
        con.commit()

    log_event("watch_trailer", {"movie_id": mid})
    invalidate_user_cache(session["user_id"])
    return jsonify({"ok": True})

@bp.get("/api/personalized")
@login_required
def api_personalized():
    if SentenceTransformer is None:
        log_event("personalized", {"note": "sentence_transformers_missing"})
        return jsonify({"results": [], "note": "sentence_transformers_missing"}), 503

    uid = session["user_id"]
    sig, user_vec = get_or_build_user_profile(uid)
    if user_vec is None:
        log_event("personalized", {"note": "no_signals"})
        return jsonify({"results": [], "note": "no_signals"})

    with db() as con, con.cursor() as cur:
        cur.execute("""
            SELECT data, score
            FROM user_recommendations
            WHERE user_id=%s AND signals_hash=%s
            ORDER BY score DESC
            LIMIT 12
        """, (uid, sig))
        rows = cur.fetchall()

    if rows:
        results = []
        for r in rows:
            d = r["data"]
            d["sim"] = round(float(r["score"]), 4)
            results.append(d)
        log_event("personalized", {"note": "from_cache", "top_n": len(results)})
        return jsonify({"results": results, "note": "from_cache"})

    cand = get_candidate_cache(force=False, limit=240)
    mat, ids, meta = cand["mat"], cand["ids"], cand["meta"]
    if mat is None or not ids:
        log_event("personalized", {"note": "no_candidates"})
        return jsonify({"results": [], "note": "no_candidates"})

    seen = set()
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT movie_id FROM favorites WHERE user_id=%s", (uid,))
        seen.update([r["movie_id"] for r in cur.fetchall()])
        cur.execute("SELECT movie_id FROM ratings WHERE user_id=%s", (uid,))
        seen.update([r["movie_id"] for r in cur.fetchall()])
        cur.execute("SELECT movie_id FROM trailer_events WHERE user_id=%s", (uid,))
        seen.update([r["movie_id"] for r in cur.fetchall()])

    scores = mat @ user_vec
    pairs = [(float(scores[i]), mid) for i, mid in enumerate(ids) if mid not in seen]
    pairs.sort(reverse=True, key=lambda x: x[0])
    top = pairs[:12]

    results = []
    now = now_utc()
    with db() as con, con.cursor() as cur:
        for score, mid in top:
            d = meta.get(mid) or {"id": mid}
            item = {
                "id": d.get("id", mid),
                "title": d.get("title"),
                "poster_path": d.get("poster_path"),
                "vote_average": d.get("vote_average"),
                "release_date": d.get("release_date"),
            }
            results.append({**item, "sim": round(score, 4)})

            cur.execute("""
                INSERT INTO user_recommendations(user_id, movie_id, score, data, signals_hash, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, movie_id)
                DO UPDATE SET score=EXCLUDED.score,
                              data=EXCLUDED.data,
                              signals_hash=EXCLUDED.signals_hash,
                              updated_at=EXCLUDED.updated_at
            """, (uid, mid, score, json.dumps(item), sig, now))
        con.commit()

    log_event("personalized", {"note": "fresh", "top_n": len(results)})
    return jsonify({"results": results, "note": "fresh"})
