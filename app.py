import os, datetime
from functools import lru_cache, wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import requests
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg
from psycopg.rows import dict_row

import sentiment  # duygu analizi

load_dotenv()

TMDB_KEY = os.getenv("TMDB_API_KEY")
assert TMDB_KEY, "Lütfen TMDB_API_KEY ortam değişkenini ayarlayın."

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("APP_SECRET", "dev-secret-change-me")
TMDB_BASE = "https://api.themoviedb.org/3"


# --------- DB yardımcıları (PostgreSQL) ---------
def _pg_conninfo():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")
    db   = os.getenv("PGDATABASE", "postgres")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

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
            # users & comments
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id            BIGSERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL
            );
            """)
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
            # Kolon güvenlik güncellemeleri
            if not _column_exists(con, "comments", "sentiment_label"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_label TEXT;")
            if not _column_exists(con, "comments", "sentiment_score"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_score DOUBLE PRECISION;")

            # --- Favoriler & Like/Dislike ---
            cur.execute("""
            CREATE TABLE IF NOT EXISTS favorites(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS ratings(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                value      SMALLINT NOT NULL CHECK (value IN (-1, 1)), -- 1=like, -1=dislike
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)
        con.commit()

init_db()


# --------- TMDB yardımcıları ---------
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


# --------- Auth yardımcıları ---------
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


# --------- Sayfalar ---------
@app.route("/")
def home():
    page = int(request.args.get("page", 1))
    yeni = tmdb_get("/movie/now_playing", {"page": page})
    trend = tmdb_get("/trending/movie/week", {"page": 1})
    genres = get_genres()
    years = list(range(datetime.datetime.now().year, 1970, -1))
    return render_template("index.html",
                           yeni=yeni, trend=trend, genres=genres, years=years,
                           user=current_user())

@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    # TR metinler kalsın, videolarda TR + EN + dilsiz kayıtları da getir
    detail = tmdb_get(
        f"/movie/{movie_id}",
        {
            "append_to_response": "videos,credits,release_dates",
            "include_video_language": "tr-TR,en-US,en,null",
        },
    )
    recs = tmdb_get(f"/movie/{movie_id}/recommendations", {"page": 1})

    # --- Video seçim kuralı: Önce TR, yoksa EN ---
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
    my_rating = None  # 1 (like), -1 (dislike) veya None
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

    # Duygu analizi
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
              datetime.datetime.utcnow(), label, score))
        con.commit()

    flash("Yorumunuz kaydedildi.", "ok")
    return redirect(url_for("movie_detail", movie_id=movie_id))


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
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

    # Sadece ihtiyacımız olan alanları dönelim (hız + az payload)
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



# --------- Auth ---------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or not password:
            flash("Tüm alanlar zorunludur.", "error"); return redirect(url_for("register"))
        try:
            with db() as con, con.cursor() as cur:
                cur.execute("""
                    INSERT INTO users(username,email,password_hash,created_at)
                    VALUES (%s,%s,%s,%s)
                    RETURNING id
                """, (username, email, generate_password_hash(password),
                      datetime.datetime.utcnow()))
                user_id = cur.fetchone()["id"]
                con.commit()
        except psycopg.errors.UniqueViolation:
            flash("Kullanıcı adı veya e-posta zaten kayıtlı.", "error")
            return redirect(url_for("register"))

        session["user_id"] = user_id
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
            flash("Geçersiz e-posta veya şifre.", "error"); return redirect(url_for("login"))
        session["user_id"] = u["id"]
        flash("Giriş yapıldı.", "ok")
        nxt = request.args.get("next") or url_for("home")
        return redirect(nxt)
    return render_template("login.html", user=current_user())

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Çıkış yapıldı.", "ok")
    return redirect(url_for("home"))


# --------- Favoriler & Beğeni ---------
@app.post("/movie/<int:movie_id>/favorite")
@login_required
def toggle_favorite(movie_id):
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT id FROM favorites WHERE user_id=%s AND movie_id=%s",
                    (session["user_id"], movie_id))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM favorites WHERE id=%s", (row["id"],))
            flash("Favorilerden kaldırıldı.", "ok")
        else:
            cur.execute("""
                INSERT INTO favorites(user_id, movie_id, created_at)
                VALUES (%s,%s,%s)
            """, (session["user_id"], movie_id, datetime.datetime.utcnow()))
            flash("Favorilere eklendi.", "ok")
        con.commit()
    return redirect(url_for("movie_detail", movie_id=movie_id))

@app.post("/movie/<int:movie_id>/rate")
@login_required
def rate_movie(movie_id):
    raw = (request.form.get("value") or "").strip()
    if raw not in ("like", "dislike"):
        flash("Geçersiz işlem.", "error")
        return redirect(url_for("movie_detail", movie_id=movie_id))

    val = 1 if raw == "like" else -1

    with db() as con, con.cursor() as cur:
        # Aynı değeri tekrar basarsa → sil (toggle), farklıysa → update
        cur.execute("SELECT value FROM ratings WHERE user_id=%s AND movie_id=%s",
                    (session["user_id"], movie_id))
        row = cur.fetchone()
        if row and row["value"] == val:
            cur.execute("DELETE FROM ratings WHERE user_id=%s AND movie_id=%s",
                        (session["user_id"], movie_id))
            flash("Beğeni/oy kaldırıldı.", "ok")
        else:
            cur.execute("""
                INSERT INTO ratings(user_id, movie_id, value, created_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (user_id, movie_id)
                DO UPDATE SET value=EXCLUDED.value, created_at=EXCLUDED.created_at
            """, (session["user_id"], movie_id, val, datetime.datetime.utcnow()))
            flash("Kaydedildi.", "ok")
        con.commit()
    return redirect(url_for("movie_detail", movie_id=movie_id))

@app.route("/favorites")
@login_required
def favorites_page():
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


if __name__ == "__main__":
    # Flask’ın debug reloader’ı çok bağlantı açmasın diye threaded=False tercih edilebilir
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=False)
