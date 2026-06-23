# app/services/recommender.py
import json
import time
import threading
import hashlib
import numpy as np
from ..db import db
from .utils import now_utc
from .tmdb import tmdb_get
from .embeddings import ensure_embeddings

CAND_TTL_SEC = 60 * 60
CANDIDATE_LIMIT = 60
_mem_lock = threading.Lock()
_mem_cand = {"ts": 0.0, "ids": [], "meta": {}, "mat": None, "limit": 0}

def user_signals_hash(uid: int) -> str:
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c, MAX(created_at) AS m FROM favorites WHERE user_id=%s", (uid,))
        fav = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS c, MAX(created_at) AS m FROM ratings WHERE user_id=%s", (uid,))
        rat = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS c, MAX(created_at) AS m FROM trailer_events WHERE user_id=%s", (uid,))
        trl = cur.fetchone()

    def fmt(row):
        c = row["c"] or 0
        m = row["m"] or "0"
        return f"{c}:{m}"

    raw = f"fav:{fmt(fav)}|rat:{fmt(rat)}|trl:{fmt(trl)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def invalidate_user_cache(uid: int):
    with db() as con, con.cursor() as cur:
        cur.execute("DELETE FROM user_profiles WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM user_recommendations WHERE user_id=%s", (uid,))
        con.commit()

def refresh_candidate_pool(force: bool = False):
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT MAX(updated_at) AS m, BOOL_AND(data ? 'overview') AS enriched FROM candidate_movies")
        row = cur.fetchone()
        last = row["m"]
        enriched = bool(row["enriched"])

    age = 1e9 if last is None else (now_utc() - last).total_seconds()
    if (not force) and age < CAND_TTL_SEC and enriched:
        return

    def grab(path, pages):
        out = []
        for p in range(1, pages + 1):
            try:
                out.extend((tmdb_get(path, {"page": p, "language": "en-US"}).get("results") or []))
            except Exception:
                pass
        return out

    pool = []
    # A compact, varied pool is deliberately prepared by the worker. Fetching
    # hundreds of movie details in a browser request makes first use unusable.
    pool += grab("/movie/popular", 1)
    pool += grab("/movie/top_rated", 1)
    pool += grab("/trending/movie/week", 1)
    pool += grab("/movie/now_playing", 1)

    cand = {}
    for m in pool:
        mid = m.get("id")
        if not mid:
            continue
        if not (m.get("poster_path") or m.get("backdrop_path")):
            continue
        cand[mid] = {
            "id": mid,
            "title": m.get("title"),
            "overview": m.get("overview"),
            "genre_ids": m.get("genre_ids") or [],
            "poster_path": m.get("poster_path"),
            "vote_average": m.get("vote_average"),
            "release_date": m.get("release_date"),
        }

    now = now_utc()
    with db() as con, con.cursor() as cur:
        for mid, data in cand.items():
            cur.execute(
                """
                INSERT INTO candidate_movies(movie_id, data, updated_at)
                VALUES (%s,%s,%s)
                ON CONFLICT (movie_id)
                DO UPDATE SET data=EXCLUDED.data,
                              updated_at=EXCLUDED.updated_at
                """,
                (mid, json.dumps(data), now),
            )
        con.commit()

def _candidate_cache_from_rows(rows, emb_map):
    ids = [r["movie_id"] for r in rows]
    meta = {r["movie_id"]: r["data"] for r in rows}
    mats, ok_ids = [], []
    for mid in ids:
        vector = emb_map.get(mid)
        if vector is None:
            continue
        ok_ids.append(mid)
        mats.append(vector)
    return {"ts": time.time(), "ids": ok_ids, "meta": meta,
            "mat": np.vstack(mats) if mats else None, "limit": len(ids)}


def _candidate_rows(limit: int):
    with db() as con, con.cursor() as cur:
        cur.execute(
            "SELECT movie_id, data FROM candidate_movies ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


def _candidate_text(data: dict) -> str:
    """Build candidate text from the inexpensive TMDB list response."""
    title = (data.get("title") or "").strip()
    overview = (data.get("overview") or "").strip()
    genres = " ".join(str(genre_id) for genre_id in (data.get("genre_ids") or []))
    return " [SEP] ".join(part for part in (title, overview, genres) if part)


def get_candidate_cache(force: bool = False, limit: int = CANDIDATE_LIMIT):
    global _mem_cand
    with _mem_lock:
        if (not force) and _mem_cand["mat"] is not None and _mem_cand["limit"] >= limit and (time.time() - _mem_cand["ts"] < CAND_TTL_SEC):
            return _mem_cand

        refresh_candidate_pool(force=force)
        rows = _candidate_rows(limit)
        ids = [r["movie_id"] for r in rows]
        texts = {r["movie_id"]: _candidate_text(r["data"]) for r in rows}
        emb_map = ensure_embeddings(ids, text_overrides=texts)
        _mem_cand = _candidate_cache_from_rows(rows, emb_map)
        return _mem_cand


def get_ready_candidate_cache(limit: int = CANDIDATE_LIMIT):
    """Return only prepared DB data; never contacts TMDB or loads a model."""
    global _mem_cand
    with _mem_lock:
        if _mem_cand["mat"] is not None and _mem_cand["limit"] >= limit and (time.time() - _mem_cand["ts"] < CAND_TTL_SEC):
            return _mem_cand

        rows = _candidate_rows(limit)
        if not rows:
            return None
        ids = [r["movie_id"] for r in rows]
        with db() as con, con.cursor() as cur:
            cur.execute("SELECT movie_id, embedding FROM movie_embeddings WHERE movie_id = ANY(%s)", (ids,))
            emb_map = {
                r["movie_id"]: np.asarray(r["embedding"], dtype=np.float32)
                for r in cur.fetchall() if r["embedding"] is not None
            }
        cache = _candidate_cache_from_rows(rows, emb_map)
        if cache["mat"] is None:
            return None
        _mem_cand = cache
        return cache

def get_or_build_user_profile(uid: int):
    sig = user_signals_hash(uid)

    with db() as con, con.cursor() as cur:
        cur.execute("SELECT signals_hash, embedding FROM user_profiles WHERE user_id=%s", (uid,))
        row = cur.fetchone()

    if row and row["signals_hash"] == sig and row["embedding"] is not None:
        return sig, np.asarray(row["embedding"], dtype=np.float32)

    weights = {}
    now = now_utc()

    def calculate_decay(event_date):
        if not event_date:
            return 0.5
        delta = (now - event_date).days
        if delta < 0:
            delta = 0
        return 0.985 ** delta

    with db() as con, con.cursor() as cur:
        cur.execute(
            "SELECT movie_id, created_at FROM favorites WHERE user_id=%s ORDER BY id DESC LIMIT 60",
            (uid,),
        )
        for r in cur.fetchall():
            mid = r["movie_id"]
            decay = calculate_decay(r["created_at"])
            weights[mid] = weights.get(mid, 0.0) + (2.5 * decay)

        cur.execute(
            "SELECT movie_id, value, created_at FROM ratings WHERE user_id=%s ORDER BY id DESC LIMIT 250",
            (uid,),
        )
        for r in cur.fetchall():
            mid = r["movie_id"]
            decay = calculate_decay(r["created_at"])
            base_val = 2.0 if r["value"] == 1 else -2.0
            weights[mid] = weights.get(mid, 0.0) + (base_val * decay)

        cur.execute(
            """
            SELECT movie_id, COUNT(*) AS c, MAX(created_at) as last_watch
            FROM trailer_events
            WHERE user_id = %s
            GROUP BY movie_id
            ORDER BY MAX(created_at) DESC LIMIT 250
            """,
            (uid,),
        )
        for r in cur.fetchall():
            mid = r["movie_id"]
            c = int(r["c"] or 0)
            base_score = min(0.8 * (1 + np.log1p(c)), 2.0)
            decay = calculate_decay(r["last_watch"])
            weights[mid] = weights.get(mid, 0.0) + (base_score * decay)

    if not weights:
        return sig, None

    mids = list(weights.keys())
    emb_map = ensure_embeddings(mids)

    num, denom = None, 0.0
    for mid, w in weights.items():
        v = emb_map.get(mid)
        if v is None:
            continue
        if num is None:
            num = np.zeros_like(v)
        num += (w * v)
        denom += abs(w)

    if num is None or denom == 0:
        return sig, None

    user_vec = num / denom
    user_vec = user_vec / (np.linalg.norm(user_vec) + 1e-9)

    with db() as con, con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_profiles(user_id, signals_hash, embedding, updated_at)
            VALUES (%s, %s, %s, %s) ON CONFLICT (user_id)
            DO UPDATE SET signals_hash=EXCLUDED.signals_hash,
                embedding=EXCLUDED.embedding,
                updated_at=EXCLUDED.updated_at
            """,
            (uid, sig, json.dumps(user_vec.tolist()), now_utc()),
        )
        con.commit()

    return sig, user_vec


def get_cached_user_profile(uid: int):
    """Read a prepared profile without triggering embedding generation."""
    sig = user_signals_hash(uid)
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT signals_hash, embedding FROM user_profiles WHERE user_id=%s", (uid,))
        row = cur.fetchone()
    if not row or row["signals_hash"] != sig or row["embedding"] is None:
        return sig, None
    return sig, np.asarray(row["embedding"], dtype=np.float32)


def get_cached_recommendations(uid: int, sig: str, top_n: int = 12):
    with db() as con, con.cursor() as cur:
        cur.execute(
            """SELECT data, score FROM user_recommendations
               WHERE user_id=%s AND signals_hash=%s ORDER BY score DESC LIMIT %s""",
            (uid, sig, top_n),
        )
        rows = cur.fetchall()
    results = []
    for row in rows:
        item = dict(row["data"])
        item["sim"] = round(float(row["score"]), 4)
        results.append(item)
    return results


def build_user_recommendations(uid: int, sig: str, user_vec, candidate_cache, top_n: int = 12):
    """Score prepared candidates and persist the resulting recommendation cache."""
    mat, ids, meta = candidate_cache["mat"], candidate_cache["ids"], candidate_cache["meta"]
    if mat is None or not ids or user_vec is None:
        return []

    seen = set()
    with db() as con, con.cursor() as cur:
        for table in ("favorites", "ratings", "trailer_events"):
            cur.execute(f"SELECT movie_id FROM {table} WHERE user_id=%s", (uid,))
            seen.update(row["movie_id"] for row in cur.fetchall())

        scores = mat @ user_vec
        pairs = sorted(
            ((float(scores[i]), mid) for i, mid in enumerate(ids) if mid not in seen),
            reverse=True, key=lambda pair: pair[0],
        )[:top_n]
        results = []
        now = now_utc()
        for score, mid in pairs:
            data = meta.get(mid) or {"id": mid}
            item = {
                "id": data.get("id", mid), "title": data.get("title"),
                "poster_path": data.get("poster_path"), "vote_average": data.get("vote_average"),
                "release_date": data.get("release_date"),
            }
            results.append({**item, "sim": round(score, 4)})
            cur.execute(
                """INSERT INTO user_recommendations(user_id, movie_id, score, data, signals_hash, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, movie_id) DO UPDATE SET
                     score=EXCLUDED.score, data=EXCLUDED.data,
                     signals_hash=EXCLUDED.signals_hash, updated_at=EXCLUDED.updated_at""",
                (uid, mid, score, json.dumps(item), sig, now),
            )
        con.commit()
    return results
