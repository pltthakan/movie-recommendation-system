# app/blueprints/pages.py
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from ..services.tmdb import tmdb_get, get_genres
from ..services.events import log_event
from ..services.auth import login_required, current_user
from ..services.recommender import invalidate_user_cache
from ..db import db
from ..services.utils import now_utc
from app import sentiment


bp = Blueprint("pages", __name__)

@bp.get("/")
def home():
    page = int(request.args.get("page", 1))
    yeni = tmdb_get("/movie/now_playing", {"page": page})
    trend = tmdb_get("/trending/movie/week", {"page": 1})
    genres = get_genres()
    years = list(range(datetime.datetime.now().year, 1970, -1))
    return render_template("index.html", yeni=yeni, trend=trend, genres=genres, years=years, user=current_user())

@bp.get("/movie/<int:movie_id>")
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

    tz = current_app.config.get("TZ", "Europe/Istanbul")

    with db() as con, con.cursor() as cur:
        cur.execute(f"""
            SELECT c.id,
                   c.content,
                   c.is_spoiler,
                   c.created_at,
                   to_char(c.created_at AT TIME ZONE %s,'YYYY-MM-DD HH24:MI:SS') AS created_at_str,
                   c.sentiment_label,
                   c.sentiment_score,
                   u.username
            FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.movie_id = %s
            ORDER BY c.id DESC
        """, (tz, movie_id))
        comments = cur.fetchall()

    total = len(comments)
    pos = sum(1 for c in comments if c["sentiment_label"] == "POS")
    neg = sum(1 for c in comments if c["sentiment_label"] == "NEG")
    neu = sum(1 for c in comments if c["sentiment_label"] == "NEU")
    like_pct = round((pos / total * 100.0), 1) if total else None

    my_fav = False
    my_rating = None
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

@bp.post("/movie/<int:movie_id>/comment")
@login_required
def add_comment(movie_id):
    content = (request.form.get("content") or "").strip()
    is_spoiler = True if request.form.get("is_spoiler") == "on" else False
    if not content:
        flash("Yorum boş olamaz.", "error")
        return redirect(url_for("pages.movie_detail", movie_id=movie_id))

    try:
        label, score = sentiment.analyze(content)
    except Exception as e:
        print("[sentiment] ERROR:", e)
        label, score = "NEU", 0.0

    with db() as con, con.cursor() as cur:
        cur.execute("""
            INSERT INTO comments(movie_id,user_id,content,is_spoiler,created_at,sentiment_label,sentiment_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (movie_id, session["user_id"], content, is_spoiler, now_utc(), label, score))
        con.commit()

    log_event("comment_add", {"movie_id": movie_id, "is_spoiler": bool(is_spoiler), "sentiment": label, "score": float(score)})
    flash("Yorumunuz kaydedildi.", "ok")
    return redirect(url_for("pages.movie_detail", movie_id=movie_id))

@bp.get("/search")
def search():
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))

    if q:
        from ..services.utils import sha1
        log_event("search", {"q_hash": sha1(q), "q_len": len(q), "page": page})

    if not q:
        empty = {"results": [], "page": 1, "total_pages": 1}
        return render_template("search.html", q=q, results=empty, user=current_user())

    results = tmdb_get("/search/movie", {"query": q, "page": page, "include_adult": False})
    return render_template("search.html", q=q, results=results, user=current_user())

@bp.get("/favorites")
@login_required
def favorites_page():
    log_event("view_favorites")
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT movie_id FROM favorites WHERE user_id=%s ORDER BY id DESC", (session["user_id"],))
        ids = [r["movie_id"] for r in cur.fetchall()]

    movies = []
    for mid in ids:
        try:
            movies.append(tmdb_get(f"/movie/{mid}"))
        except Exception:
            pass

    return render_template("favorites.html", movies=movies, user=current_user())

@bp.post("/movie/<int:movie_id>/favorite")
@login_required
def toggle_favorite(movie_id):
    removed = False
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT id FROM favorites WHERE user_id=%s AND movie_id=%s", (session["user_id"], movie_id))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM favorites WHERE id=%s", (row["id"],))
            removed = True
        else:
            cur.execute("INSERT INTO favorites(user_id, movie_id, created_at) VALUES (%s,%s,%s)",
                        (session["user_id"], movie_id, now_utc()))
        con.commit()

    if removed:
        log_event("favorite_remove", {"movie_id": movie_id})
        flash("Favorilerden kaldırıldı.", "ok")
    else:
        log_event("favorite_add", {"movie_id": movie_id})
        flash("Favorilere eklendi.", "ok")

    invalidate_user_cache(session["user_id"])
    return redirect(url_for("pages.movie_detail", movie_id=movie_id))

@bp.post("/movie/<int:movie_id>/rate")
@login_required
def rate_movie(movie_id):
    raw = (request.form.get("value") or "").strip()
    if raw not in ("like", "dislike"):
        flash("Geçersiz işlem.", "error")
        return redirect(url_for("pages.movie_detail", movie_id=movie_id))

    val = 1 if raw == "like" else -1
    action = None

    with db() as con, con.cursor() as cur:
        cur.execute("SELECT value FROM ratings WHERE user_id=%s AND movie_id=%s", (session["user_id"], movie_id))
        row = cur.fetchone()
        if row and row["value"] == val:
            cur.execute("DELETE FROM ratings WHERE user_id=%s AND movie_id=%s", (session["user_id"], movie_id))
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
    return redirect(url_for("pages.movie_detail", movie_id=movie_id))
