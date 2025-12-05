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
_mem_lock = threading.Lock()
_mem_cand = {"ts": 0.0, "ids": [], "meta": {}, "mat": None}

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
        cur.execute("SELECT MAX(updated_at) AS m FROM candidate_movies")
        row = cur.fetchone()
        last = row["m"]

    age = 1e9 if last is None else (now_utc() - last).total_seconds()
    if (not force) and age < CAND_TTL_SEC:
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
    pool += grab("/movie/popular", 3)
    pool += grab("/movie/top_rated", 3)
    pool += grab("/trending/movie/week", 3)
    pool += grab("/movie/now_playing", 2)

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

def get_candidate_cache(force: bool = False, limit: int = 240):
    global _mem_cand
    with _mem_lock:
        if (not force) and _mem_cand["mat"] is not None and (time.time() - _mem_cand["ts"] < CAND_TTL_SEC):
            return _mem_cand

        refresh_candidate_pool(force=force)

        with db() as con, con.cursor() as cur:
            cur.execute(
                "SELECT movie_id, data FROM candidate_movies ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()

        ids = [r["movie_id"] for r in rows]
        meta = {r["movie_id"]: r["data"] for r in rows}
        emb_map = ensure_embeddings(ids)

        mats, ok_ids = [], []
        for mid in ids:
            v = emb_map.get(mid)
            if v is None:
                continue
            ok_ids.append(mid)
            mats.append(v)

        mat = np.vstack(mats) if mats else None
        _mem_cand = {"ts": time.time(), "ids": ok_ids, "meta": meta, "mat": mat}
        return _mem_cand

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
