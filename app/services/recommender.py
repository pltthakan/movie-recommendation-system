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
        cur.execute("SELECT MAX(updated_at) AS m, BOOL_AND(data ? 'overview' AND data ? 'popularity') AS enriched FROM candidate_movies")
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
            "popularity": m.get("popularity") or 0.0,
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


def _min_max(values):
    values = np.asarray(values, dtype=np.float32)
    if not len(values):
        return values
    low, high = float(values.min()), float(values.max())
    if high - low < 1e-8:
        return np.zeros_like(values)
    return (values - low) / (high - low)


def _engagement_scores(cur, candidate_ids):
    """Global positive engagement is a lightweight behavioural ranking signal."""
    cur.execute(
        """
        SELECT movie_id, SUM(weight) AS score
        FROM (
            SELECT movie_id, 2.0::float AS weight FROM favorites
            UNION ALL
            SELECT movie_id, CASE WHEN value=1 THEN 1.5 ELSE -1.0 END::float FROM ratings
            UNION ALL
            SELECT movie_id, 0.4::float AS weight FROM trailer_events
        ) interactions
        WHERE movie_id = ANY(%s)
        GROUP BY movie_id
        """,
        (candidate_ids,),
    )
    return {row["movie_id"]: max(float(row["score"]), 0.0) for row in cur.fetchall()}


def _collaborative_scores(cur, uid: int, candidate_ids):
    """Find candidate films liked by users who overlap with this user's likes."""
    cur.execute(
        """
        WITH own_positive AS (
            SELECT movie_id FROM favorites WHERE user_id=%s
            UNION
            SELECT movie_id FROM ratings WHERE user_id=%s AND value=1
        ), positive_interactions AS (
            SELECT user_id, movie_id FROM favorites
            UNION
            SELECT user_id, movie_id FROM ratings WHERE value=1
        )
        SELECT peer.movie_id, COUNT(DISTINCT peer.user_id)::float AS score
        FROM positive_interactions seed
        JOIN own_positive own ON own.movie_id=seed.movie_id
        JOIN positive_interactions peer ON peer.user_id=seed.user_id
        WHERE seed.user_id<>%s AND peer.movie_id<>ALL(%s)
          AND peer.movie_id=ANY(%s)
        GROUP BY peer.movie_id
        """,
        (uid, uid, uid, candidate_ids, candidate_ids),
    )
    return {row["movie_id"]: float(row["score"]) for row in cur.fetchall()}


def mmr_rerank(candidate_indices, base_scores, embedding_matrix, metadata, top_n: int,
               diversity_lambda: float = 0.78):
    """Maximal Marginal Relevance prevents near-duplicate recommendation rows."""
    remaining = sorted(candidate_indices, key=lambda idx: float(base_scores[idx]), reverse=True)[:40]
    selected, selected_genres = [], set()
    while remaining and len(selected) < top_n:
        best_idx, best_value = None, -float("inf")
        for idx in remaining:
            duplicate_similarity = max(
                (float(embedding_matrix[idx] @ embedding_matrix[other]) for other in selected),
                default=0.0,
            )
            genres = set((metadata.get(idx) or {}).get("genre_ids") or [])
            genre_bonus = 0.025 if genres and not genres.issubset(selected_genres) else 0.0
            value = diversity_lambda * float(base_scores[idx]) - (1 - diversity_lambda) * duplicate_similarity + genre_bonus
            if value > best_value:
                best_idx, best_value = idx, value
        selected.append(best_idx)
        selected_genres.update((metadata.get(best_idx) or {}).get("genre_ids") or [])
        remaining.remove(best_idx)
    return selected


def build_user_recommendations(uid: int, sig: str, user_vec, candidate_cache, top_n: int = 12):
    """Hybrid-score prepared candidates, diversify them, then persist the cache."""
    mat, ids, meta = candidate_cache["mat"], candidate_cache["ids"], candidate_cache["meta"]
    if mat is None or not ids or user_vec is None:
        return []

    seen = set()
    with db() as con, con.cursor() as cur:
        for table in ("favorites", "ratings", "trailer_events"):
            cur.execute(f"SELECT movie_id FROM {table} WHERE user_id=%s", (uid,))
            seen.update(row["movie_id"] for row in cur.fetchall())

        content_scores = _min_max(mat @ user_vec)
        engagement_map = _engagement_scores(cur, ids)
        collaborative_map = _collaborative_scores(cur, uid, ids)
        engagement_scores = _min_max([engagement_map.get(mid, 0.0) for mid in ids])
        collaborative_scores = _min_max([collaborative_map.get(mid, 0.0) for mid in ids])
        vote_scores = np.asarray([
            min(max(float((meta.get(mid) or {}).get("vote_average") or 0.0) / 10.0, 0.0), 1.0)
            for mid in ids
        ], dtype=np.float32)
        popularity_scores = _min_max([
            float((meta.get(mid) or {}).get("popularity") or 0.0) for mid in ids
        ])
        quality_scores = 0.70 * vote_scores + 0.30 * popularity_scores

        if any(collaborative_map.values()):
            hybrid_scores = (0.62 * content_scores + 0.20 * collaborative_scores +
                             0.10 * engagement_scores + 0.08 * quality_scores)
        else:
            hybrid_scores = 0.78 * content_scores + 0.14 * engagement_scores + 0.08 * quality_scores

        available = [index for index, movie_id in enumerate(ids) if movie_id not in seen]
        index_metadata = {index: meta.get(movie_id) or {} for index, movie_id in enumerate(ids)}
        selected_indices = mmr_rerank(available, hybrid_scores, mat, index_metadata, top_n)
        results = []
        now = now_utc()
        for index in selected_indices:
            mid, score = ids[index], float(hybrid_scores[index])
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
