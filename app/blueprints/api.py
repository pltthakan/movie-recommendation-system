# app/blueprints/api.py
import json
import logging
from flask import Blueprint, request, jsonify, session

from ..services.tmdb import tmdb_get
from ..services.auth import login_required
from ..services.events import log_event
from ..services.recommender import (
    invalidate_user_cache,
    get_or_build_user_profile,
    get_candidate_cache,
    user_signals_hash
)
from ..services.embeddings import SentenceTransformer
from ..db import db
from ..services.utils import now_utc

bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)   # ← LOGGING EKLENDİ


# ----------------------------------------------------------
#  /api/discover
# ----------------------------------------------------------
@bp.get("/api/discover")
def api_discover():
    logger.info(
        "API /api/discover called ip=%s params=%s",
        request.remote_addr, dict(request.args)
    )

    genre_id = request.args.get("genre_id")
    year = request.args.get("year")
    sort_by = request.args.get("sort_by", "popularity.desc")
    vote_gte = request.args.get("vote_gte")
    page = int(request.args.get("page", 1))

    params = {
        "sort_by": sort_by,
        "page": page,
        "with_original_language": "en|tr"
    }
    if genre_id: params["with_genres"] = genre_id
    if year: params["primary_release_year"] = year
    if vote_gte: params["vote_average.gte"] = vote_gte

    try:
        data = tmdb_get("/discover/movie", params)
        logger.info("API /api/discover success results=%s", len(data.get("results", [])))
        return jsonify(data)
    except Exception as e:
        logger.exception("API /api/discover FAILED: %s", e)
        return jsonify({"error": "tmdb_failed"}), 500


# ----------------------------------------------------------
#  /api/search_suggest
# ----------------------------------------------------------
@bp.get("/api/search_suggest")
def api_search_suggest():
    q = (request.args.get("q") or "").strip()
    logger.debug("API /api/search_suggest q='%s' ip=%s", q, request.remote_addr)

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

    logger.debug("API /api/search_suggest returned %d items", len(results))
    return jsonify({"results": results})


# ----------------------------------------------------------
#  /api/featured
# ----------------------------------------------------------
@bp.get("/api/featured")
def api_featured():
    logger.info("API /api/featured called ip=%s", request.remote_addr)

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

    logger.info("API /api/featured success returned=%s", len(results))
    return jsonify({"results": results})


# ----------------------------------------------------------
#  /api/trailer_event
# ----------------------------------------------------------
@bp.post("/api/trailer_event")
@login_required
def api_trailer_event():
    data = request.get_json(silent=True) or {}
    mid = data.get("movie_id") or request.form.get("movie_id")

    logger.info(
        "API /api/trailer_event called user_id=%s movie_id=%s ip=%s",
        session["user_id"], mid, request.remote_addr
    )

    try:
        mid = int(mid)
    except Exception:
        logger.warning("API trailer_event FAILED invalid_movie_id")
        return jsonify({"ok": False, "error": "invalid_movie_id"}), 400

    with db() as con, con.cursor() as cur:
        cur.execute("""
            INSERT INTO trailer_events(user_id, movie_id, event_type, created_at)
            VALUES (%s,%s,'watch_trailer',%s)
        """, (session["user_id"], mid, now_utc()))
        con.commit()

    log_event("watch_trailer", {"movie_id": mid})
    invalidate_user_cache(session["user_id"])

    logger.info("API trailer_event SUCCESS user_id=%s movie_id=%s", session["user_id"], mid)
    return jsonify({"ok": True})


# ----------------------------------------------------------
#  /api/personalized
# ----------------------------------------------------------
@bp.get("/api/personalized")
@login_required
def api_personalized():
    uid = session["user_id"]
    logger.info("API /api/personalized called user_id=%s ip=%s", uid, request.remote_addr)

    if SentenceTransformer is None:
        logger.error("SentenceTransformer missing — personalized unavailable")
        log_event("personalized", {"note": "sentence_transformers_missing"})
        return jsonify({"results": [], "note": "sentence_transformers_missing"}), 503

    sig, user_vec = get_or_build_user_profile(uid)

    if user_vec is None:
        logger.warning("Personalized FAILED: no user profile user_id=%s", uid)
        log_event("personalized", {"note": "no_signals"})
        return jsonify({"results": [], "note": "no_signals"})

    # ------------------------------------------------------
    # Cache kontrollü öneri alma
    # ------------------------------------------------------
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

        logger.info("API personalized from_cache user_id=%s count=%s", uid, len(results))
        log_event("personalized", {"note": "from_cache", "top_n": len(results)})
        return jsonify({"results": results, "note": "from_cache"})

    # ------------------------------------------------------
    # Fresh computation
    # ------------------------------------------------------
    cand = get_candidate_cache(force=False, limit=240)
    mat, ids, meta = cand["mat"], cand["ids"], cand["meta"]

    if mat is None or not ids:
        logger.error("Personalized FAILED: no candidates")
        log_event("personalized", {"note": "no_candidates"})
        return jsonify({"results": [], "note": "no_candidates"})

    # Kullanıcının daha önce gördüğü filmleri elimine et
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

    logger.info(
        "API personalized fresh user_id=%s generated=%s",
        uid, len(results)
    )
    log_event("personalized", {"note": "fresh", "top_n": len(results)})

    return jsonify({"results": results, "note": "fresh"})
