import os
import sys
import time
import json
import uuid
import hashlib
import datetime
import threading
import warnings
from functools import lru_cache, wraps

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, flash, g
)
import requests
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg
from psycopg.rows import dict_row

import numpy as np

import sentiment  # duygu analizi

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

load_dotenv()

TMDB_KEY = os.getenv("TMDB_API_KEY")
assert TMDB_KEY, "Lütfen TMDB_API_KEY ortam değişkenini ayarlayın."

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("APP_SECRET", "dev-secret-change-me")
TMDB_BASE = "https://api.themoviedb.org/3"

warnings.filterwarnings(
    "ignore",
    message="`clean_up_tokenization_spaces` was not set.*",
    category=FutureWarning,
)

# -------------------- Zaman (TZ-aware) --------------------
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

# -------------------- DB yardımcıları (PostgreSQL) --------------------
def _pg_conninfo():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")
    dbn  = os.getenv("PGDATABASE", "postgres")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{dbn}"

def db():
    return psycopg.connect(_pg_conninfo(), row_factory=dict_row)

def _column_exists(con, table, column):
    with con.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            LIMIT 1
        """, (table, column))
        return cur.fetchone() is not None

def init_db():
    with db() as con:
        with con.cursor() as cur:
            # users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id            BIGSERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL
            );
            """)

            # comments
            cur.execute("""
            CREATE TABLE IF NOT EXISTS comments(
                id              BIGSERIAL PRIMARY KEY,
                movie_id        INTEGER NOT NULL,
                user_id         BIGINT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content         TEXT    NOT NULL,
                is_spoiler      BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL,
                sentiment_label TEXT,
                sentiment_score DOUBLE PRECISION
            );
            """)
            # kolon migrate
            if not _column_exists(con, "comments", "sentiment_label"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_label TEXT;")
            if not _column_exists(con, "comments", "sentiment_score"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_score DOUBLE PRECISION;")

            # favorites
            cur.execute("""
            CREATE TABLE IF NOT EXISTS favorites(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)

            # ratings like/dislike
            cur.execute("""
            CREATE TABLE IF NOT EXISTS ratings(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                value      SMALLINT NOT NULL CHECK (value IN (-1, 1)),
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)

            # trailer events (weak signal)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS trailer_events(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'watch_trailer',
                created_at TIMESTAMPTZ NOT NULL
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trailer_events_user ON trailer_events(user_id, created_at DESC);")

            # SBERT movie embedding cache
            cur.execute("""
            CREATE TABLE IF NOT EXISTS movie_embeddings(
                movie_id   INTEGER PRIMARY KEY,
                text_hash  TEXT,
                embedding  JSONB,
                updated_at TIMESTAMPTZ
            );
            """)
            if not _column_exists(con, "movie_embeddings", "text_hash"):
                cur.execute("ALTER TABLE movie_embeddings ADD COLUMN text_hash TEXT;")
            if not _column_exists(con, "movie_embeddings", "updated_at"):
                cur.execute("ALTER TABLE movie_embeddings ADD COLUMN updated_at TIMESTAMPTZ;")

            # candidate movies cache
            cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_movies(
                movie_id    INTEGER PRIMARY KEY,
                data        JSONB NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL
            );
            """)

            # user profile cache (embedding)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles(
                user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                signals_hash TEXT NOT NULL,
                embedding    JSONB NOT NULL,
                updated_at   TIMESTAMPTZ NOT NULL
            );
            """)

            # user recommendations cache
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_recommendations(
                user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id     INTEGER NOT NULL,
                score        DOUBLE PRECISION NOT NULL,
                data         JSONB NOT NULL,
                signals_hash TEXT NOT NULL,
                updated_at   TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(user_id, movie_id)
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_recs_user ON user_recommendations(user_id, updated_at DESC);")

            # speed indexes (opsiyonel)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, id DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id, id DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_movie ON ratings(movie_id, value);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_movie ON comments(movie_id, id DESC);")

            # ---------- USER EVENTS (analytics / logging) ----------
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_events(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
                session_id TEXT,
                event_type TEXT NOT NULL,
                path       TEXT,
                method     TEXT,
                status     INTEGER,
                ip_hash    TEXT,
                ua_hash    TEXT,
                referrer   TEXT,
                payload    JSONB,
                created_at TIMESTAMPTZ NOT NULL
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_events_user ON user_events(user_id, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_events_type ON user_events(event_type, created_at DESC);")

        con.commit()

init_db()

# -------------------- User event logging helpers --------------------
def _sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()

def _session_id():
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    return sid

def log_event(event_type: str, payload: dict | None = None, status: int | None = None,
              path: str | None = None, method: str | None = None):
    """
    Privacy-aware event log:
      - IP and User-Agent are hashed
      - payload should avoid secrets (passwords etc.)
    """
    try:
        uid = session.get("user_id")
        sid = _session_id()
        ip  = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
        ua  = request.headers.get("User-Agent", "") or ""
        ref = request.headers.get("Referer", "") or ""

        with db() as con, con.cursor() as cur:
            cur.execute("""
                INSERT INTO user_events(user_id, session_id, event_type, path, method, status,
                                        ip_hash, ua_hash, referrer, payload, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                uid,
                sid,
                event_type,
                path or request.path,
                method or request.method,
                status,
                _sha1(ip),
                _sha1(ua),
                ref[:300],
                json.dumps(payload or {}),
                now_utc(),
            ))
            con.commit()
    except Exception as e:
        # log sistemi app'i düşürmesin
        print("[user_events] ERROR:", e)

@app.before_request
def _ev_before():
    g._t0 = time.monotonic()

@app.after_request
def _ev_after(resp):
    # static dosyaları loglama
    if request.path.startswith("/static"):
        return resp

    # hassas endpointlerde payload loglama
    sensitive = (request.path in ("/login", "/register")) and request.method == "POST"

    ms = int((time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000)
    payload = {"ms": ms}

    if not sensitive:
        payload["qs"] = {k: (v[:80] if isinstance(v, str) else v) for k, v in request.args.items()}

    log_event("http_request", payload=payload, status=resp.status_code)
    return resp

# -------------------- TMDB yardımcıları --------------------
def tmdb_get(path, params=None):
    params = params or {}
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "tr-TR")
    r = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

@lru_cache(maxsize=512)
def get_genres():
    return tmdb_get("/genre/movie/list")["genres"]

# -------------------- SBERT yardımcıları --------------------
@lru_cache(maxsize=1)
def sbert():
    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers kurulu değil. `pip install sentence-transformers numpy`")
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def _hash_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()

@lru_cache(maxsize=8192)
def _movie_text_en(movie_id: int) -> str:
    """
    Embedding için EN metin: title + overview + genres
    """
    d = tmdb_get(f"/movie/{movie_id}", {"language": "en-US"})
    title = (d.get("title") or d.get("original_title") or "").strip()
    overview = (d.get("overview") or "").strip()
    genres = ", ".join([g.get("name","") for g in (d.get("genres") or []) if g.get("name")])
    parts = [title, overview, genres]
    return " [SEP] ".join([p for p in parts if p])

def _embed_texts(texts):
    vecs = sbert().encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)

def ensure_embeddings(movie_ids):
    movie_ids = [int(x) for x in set(movie_ids or []) if x]
    if not movie_ids:
        return {}

    with db() as con:
        existing = {}
        with con.cursor() as cur:
            cur.execute(
                "SELECT movie_id, text_hash, embedding FROM movie_embeddings WHERE movie_id = ANY(%s)",
                (movie_ids,),
            )
            for r in cur.fetchall():
                existing[r["movie_id"]] = r

        need = []
        texts = []
        for mid in movie_ids:
            text = _movie_text_en(mid)
            h = _hash_text(text)
            row = existing.get(mid)
            if (row is None) or (row.get("text_hash") != h) or (row.get("embedding") is None):
                need.append((mid, h, text))
                texts.append(text)

        if need:
            vecs = _embed_texts(texts)
            with con.cursor() as cur:
                for (mid, h, _text), vec in zip(need, vecs):
                    cur.execute(
                        """
                        INSERT INTO movie_embeddings(movie_id, text_hash, embedding, updated_at)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (movie_id)
                        DO UPDATE SET text_hash=EXCLUDED.text_hash,
                                      embedding=EXCLUDED.embedding,
                                      updated_at=EXCLUDED.updated_at
                        """,
                        (mid, h, json.dumps(vec.tolist()), now_utc()),
                    )
            con.commit()

        out = {}
        with con.cursor() as cur:
            cur.execute(
                "SELECT movie_id, embedding FROM movie_embeddings WHERE movie_id = ANY(%s)",
                (movie_ids,),
            )
            for r in cur.fetchall():
                if r["embedding"] is not None:
                    out[r["movie_id"]] = np.asarray(r["embedding"], dtype=np.float32)
        return out

# -------------------- Cache helpers (Netflix benzeri) --------------------
CAND_TTL_SEC = 60 * 60  # 1 saat
_mem_lock = threading.Lock()
_mem_cand = {"ts": 0.0, "ids": [], "meta": {}, "mat": None}

def user_signals_hash(uid: int) -> str:
    """
    favorites/ratings/trailer_events -> count + max(created_at)
    """
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

    if last is None:
        age = 1e9
    else:
        age = (now_utc() - last).total_seconds()

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

        mats = []
        ok_ids = []
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
    """
    Kullanıcı profil vektörü (SBERT):
    favorites/ratings/trailer_events sinyallerine göre ağırlıklı embedding toplamı.
    Time Decay (Zaman Aşımı) var.
    """
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
        # A) FAVORİLER
        cur.execute(
            "SELECT movie_id, created_at FROM favorites WHERE user_id=%s ORDER BY id DESC LIMIT 60",
            (uid,),
        )
        for r in cur.fetchall():
            mid = r["movie_id"]
            decay = calculate_decay(r["created_at"])
            final_score = 2.5 * decay
            weights[mid] = weights.get(mid, 0.0) + final_score

        # B) RATINGS
        cur.execute(
            "SELECT movie_id, value, created_at FROM ratings WHERE user_id=%s ORDER BY id DESC LIMIT 250",
            (uid,),
        )
        for r in cur.fetchall():
            mid = r["movie_id"]
            decay = calculate_decay(r["created_at"])
            base_val = 2.0 if r["value"] == 1 else -2.0
            final_score = base_val * decay
            weights[mid] = weights.get(mid, 0.0) + final_score

        # C) TRAILER EVENTS
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
            final_score = base_score * decay
            weights[mid] = weights.get(mid, 0.0) + final_score

    if not weights:
        return sig, None

    mids = list(weights.keys())
    emb_map = ensure_embeddings(mids)

    num = None
    denom = 0.0

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
            DO
            UPDATE SET signals_hash=EXCLUDED.signals_hash,
                embedding=EXCLUDED.embedding,
                updated_at=EXCLUDED.updated_at
            """,
            (uid, sig, json.dumps(user_vec.tolist()), now_utc()),
        )
        con.commit()

    return sig, user_vec

# -------------------- Auth yardımcıları --------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Bu işlem için giriş yapmalısınız.", "warn")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session:
        return None
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT id, username, email FROM users WHERE id=%s", (session["user_id"],))
        return cur.fetchone()

# -------------------- Sayfalar --------------------
@app.route("/")
def home():
    page = int(request.args.get("page", 1))
    yeni = tmdb_get("/movie/now_playing", {"page": page})
    trend = tmdb_get("/trending/movie/week", {"page": 1})
    genres = get_genres()
    years = list(range(datetime.datetime.now().year, 1970, -1))
    return render_template(
        "index.html",
        yeni=yeni,
        trend=trend,
        genres=genres,
        years=years,
        user=current_user(),
    )

@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    log_event("view_movie", {"movie_id": movie_id})

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

    # --- Yorumlar ---
    with db() as con, con.cursor() as cur:
        cur.execute("""
            SELECT c.id,
                   c.content,
                   c.is_spoiler,
                   c.created_at,
                   to_char(c.created_at AT TIME ZONE 'Europe/Istanbul','YYYY-MM-DD HH24:MI:SS') AS created_at_str,
                   c.sentiment_label,
                   c.sentiment_score,
                   u.username
            FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.movie_id = %s
            ORDER BY c.id DESC
        """, (movie_id,))
        comments = cur.fetchall()

    total = len(comments)
    pos = sum(1 for c in comments if c["sentiment_label"] == "POS")
    neg = sum(1 for c in comments if c["sentiment_label"] == "NEG")
    neu = sum(1 for c in comments if c["sentiment_label"] == "NEU")
    like_pct = round((pos / total * 100.0), 1) if total else None

    # --- Favori / Rating durumu ve sayaçlar ---
    my_fav = False
    my_rating = None
    likes = 0
    dislikes = 0
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM ratings WHERE movie_id=%s AND value=1", (movie_id,))
        likes = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ratings WHERE movie_id=%s AND value=-1", (movie_id,))
        dislikes = cur.fetchone()["c"]

        if "user_id" in session:
            cur.execute("SELECT 1 FROM favorites WHERE user_id=%s AND movie_id=%s",
                        (session["user_id"], movie_id))
            my_fav = cur.fetchone() is not None

            cur.execute("SELECT value FROM ratings WHERE user_id=%s AND movie_id=%s",
                        (session["user_id"], movie_id))
            r = cur.fetchone()
            my_rating = r["value"] if r else None

    return render_template(
        "detail.html",
        movie=detail,
        recs=recs,
        comments=comments,
        stats={"total": total, "pos": pos, "neg": neg, "neu": neu, "like_pct": like_pct},
        fav_state={"is_favorite": my_fav},
        rating_state={"my": my_rating, "likes": likes, "dislikes": dislikes},
        user=current_user(),
    )

@app.route("/movie/<int:movie_id>/comment", methods=["POST"])
@login_required
def add_comment(movie_id):
    content = (request.form.get("content") or "").strip()
    is_spoiler = True if request.form.get("is_spoiler") == "on" else False
    if not content:
        flash("Yorum boş olamaz.", "error")
        return redirect(url_for("movie_detail", movie_id=movie_id))

    try:
        label, score = sentiment.analyze(content)
    except Exception as e:
        print("[sentiment] ERROR:", e)
        label, score = "NEU", 0.0

    with db() as con, con.cursor() as cur:
        cur.execute("""
            INSERT INTO comments(movie_id,user_id,content,is_spoiler,created_at,sentiment_label,sentiment_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (movie_id, session["user_id"], content, is_spoiler,
              now_utc(), label, score))
        con.commit()

    log_event("comment_add", {
        "movie_id": movie_id,
        "is_spoiler": bool(is_spoiler),
        "sentiment": label,
        "score": float(score),
    })

    flash("Yorumunuz kaydedildi.", "ok")
    return redirect(url_for("movie_detail", movie_id=movie_id))

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))

    if q:
        log_event("search", {"q_hash": _sha1(q), "q_len": len(q), "page": page})

    if not q:
        empty = {"results": [], "page": 1, "total_pages": 1}
        return render_template("search.html", q=q, results=empty, user=current_user())

    results = tmdb_get("/search/movie", {"query": q, "page": page, "include_adult": False})
    return render_template("search.html", q=q, results=results, user=current_user())

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

@app.route("/api/search_suggest")
def api_search_suggest():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})

    data = tmdb_get("/search/movie", {
        "query": q,
        "page": 1,
        "include_adult": False
    })

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

@app.route("/api/featured")
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

# -------------------- Trailer event API (weak signal) --------------------
@app.post("/api/trailer_event")
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

# -------------------- Kişiselleştirilmiş öneri --------------------
@app.get("/api/personalized")
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
    mat = cand["mat"]
    ids = cand["ids"]
    meta = cand["meta"]
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
    pairs = []
    for i, mid in enumerate(ids):
        if mid in seen:
            continue
        pairs.append((float(scores[i]), mid))

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

# -------------------- Auth --------------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or not password:
            flash("Tüm alanlar zorunludur.", "error")
            return redirect(url_for("register"))

        try:
            with db() as con, con.cursor() as cur:
                cur.execute("""
                    INSERT INTO users(username,email,password_hash,created_at)
                    VALUES (%s,%s,%s,%s)
                    RETURNING id
                """, (username, email, generate_password_hash(password), now_utc()))
                user_id = cur.fetchone()["id"]
                con.commit()
        except psycopg.errors.UniqueViolation:
            log_event("register_fail", {"reason": "unique_violation", "email_hash": _sha1(email)})
            flash("Kullanıcı adı veya e-posta zaten kayıtlı.", "error")
            return redirect(url_for("register"))

        session["user_id"] = user_id
        log_event("register_success", {"user_id": user_id})

        flash("Kayıt başarılı, hoş geldiniz!", "ok")
        return redirect(url_for("home"))

    return render_template("register.html", user=current_user())

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        with db() as con, con.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            u = cur.fetchone()

        if not u or not check_password_hash(u["password_hash"], password):
            log_event("login_fail", {"email_hash": _sha1(email)})
            flash("Geçersiz e-posta veya şifre.", "error")
            return redirect(url_for("login"))

        session["user_id"] = u["id"]
        log_event("login_success", {"user_id": u["id"]})

        flash("Giriş yapıldı.", "ok")
        nxt = request.args.get("next") or url_for("home")
        return redirect(nxt)

    return render_template("login.html", user=current_user())

@app.route("/logout")
def logout():
    uid = session.get("user_id")
    session.pop("user_id", None)
    log_event("logout", {"user_id": uid})

    flash("Çıkış yapıldı.", "ok")
    return redirect(url_for("home"))

# -------------------- Favoriler & Beğeni --------------------
@app.post("/movie/<int:movie_id>/favorite")
@login_required
def toggle_favorite(movie_id):
    removed = False
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT id FROM favorites WHERE user_id=%s AND movie_id=%s",
                    (session["user_id"], movie_id))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM favorites WHERE id=%s", (row["id"],))
            removed = True
        else:
            cur.execute("""
                INSERT INTO favorites(user_id, movie_id, created_at)
                VALUES (%s,%s,%s)
            """, (session["user_id"], movie_id, now_utc()))
        con.commit()

    if removed:
        log_event("favorite_remove", {"movie_id": movie_id})
        flash("Favorilerden kaldırıldı.", "ok")
    else:
        log_event("favorite_add", {"movie_id": movie_id})
        flash("Favorilere eklendi.", "ok")

    invalidate_user_cache(session["user_id"])
    return redirect(url_for("movie_detail", movie_id=movie_id))

@app.post("/movie/<int:movie_id>/rate")
@login_required
def rate_movie(movie_id):
    raw = (request.form.get("value") or "").strip()
    if raw not in ("like", "dislike"):
        flash("Geçersiz işlem.", "error")
        return redirect(url_for("movie_detail", movie_id=movie_id))

    val = 1 if raw == "like" else -1

    action = None  # rate_like / rate_dislike / rate_remove
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT value FROM ratings WHERE user_id=%s AND movie_id=%s",
                    (session["user_id"], movie_id))
        row = cur.fetchone()
        if row and row["value"] == val:
            cur.execute("DELETE FROM ratings WHERE user_id=%s AND movie_id=%s",
                        (session["user_id"], movie_id))
            action = "rate_remove"
        else:
            cur.execute("""
                INSERT INTO ratings(user_id, movie_id, value, created_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (user_id, movie_id)
                DO UPDATE SET value=EXCLUDED.value, created_at=EXCLUDED.created_at
            """, (session["user_id"], movie_id, val, now_utc()))
            action = "rate_like" if val == 1 else "rate_dislike"
        con.commit()

    log_event(action, {"movie_id": movie_id, "value": val})

    flash("Kaydedildi.", "ok")
    invalidate_user_cache(session["user_id"])
    return redirect(url_for("movie_detail", movie_id=movie_id))

@app.route("/favorites")
@login_required
def favorites_page():
    log_event("view_favorites")

    with db() as con, con.cursor() as cur:
        cur.execute("SELECT movie_id FROM favorites WHERE user_id=%s ORDER BY id DESC",
                    (session["user_id"],))
        ids = [r["movie_id"] for r in cur.fetchall()]

    movies = []
    for mid in ids:
        try:
            m = tmdb_get(f"/movie/{mid}")
            movies.append(m)
        except Exception:
            pass

    return render_template("favorites.html", movies=movies, user=current_user())

# -------------------- Warmup --------------------
def warmup_full():
    print("[warmup] loading SBERT model...")
    if SentenceTransformer is None:
        print("[warmup] sentence-transformers yok, atlandı.")
        return
    _ = sbert()
    print("[warmup] building candidate cache & embeddings...")
    _ = get_candidate_cache(force=True, limit=240)
    print("[warmup] done.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "warmup":
        warmup_full()
        raise SystemExit(0)

    if os.getenv("AUTO_WARMUP") == "1":
        try:
            warmup_full()
        except Exception as e:
            print("[warmup] ERROR:", e)

    app.run(host="0.0.0.0", port=5000, debug=True, threaded=False)
