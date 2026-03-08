# app/blueprints/auth.py
import logging
import psycopg
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

from ..db import db
from ..services.utils import now_utc, sha1
from ..services.events import log_event
from ..services.auth import current_user

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------
#  REGISTER
# ---------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        # ------------------------------
        # Input validation
        # ------------------------------
        if not username or not email or not password:
            flash("Tüm alanlar zorunludur.", "error")
            logger.warning(
                "Register FAILED (missing fields): username=%s email=%s ip=%s",
                username, email, request.remote_addr
            )
            return redirect(url_for("auth.register"))

        # ------------------------------
        # DB Insert
        # ------------------------------
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
            # Hem event log’a hem dosya loguna yazıyoruz
            log_event("register_fail", {
                "reason": "unique_violation",
                "email_hash": sha1(email)
            })
            logger.warning(
                "Register FAILED (duplicate): username=%s email=%s ip=%s",
                username, email, request.remote_addr
            )
            flash("Kullanıcı adı veya e-posta zaten kayıtlı.", "error")
            return redirect(url_for("auth.register"))

        # ------------------------------
        # Success
        # ------------------------------
        session["user_id"] = user_id
        log_event("register_success", {"user_id": user_id})

        logger.info(
            "Register SUCCESS user_id=%s username=%s email=%s ip=%s",
            user_id, username, email, request.remote_addr
        )

        flash("Kayıt başarılı, hoş geldiniz!", "ok")
        return redirect(url_for("pages.home"))

    # GET isteği
    return render_template("register.html", user=current_user())


# ---------------------------------------------------------
#  LOGIN
# ---------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        # Kullanıcıyı DB’den çek
        with db() as con, con.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            u = cur.fetchone()

        # ------------------------------
        # INVALID LOGIN
        # ------------------------------
        if not u or not check_password_hash(u["password_hash"], password):
            log_event("login_fail", {"email_hash": sha1(email)})
            logger.warning(
                "Login FAILED email=%s ip=%s",
                email, request.remote_addr
            )
            flash("Geçersiz e-posta veya şifre.", "error")
            return redirect(url_for("auth.login"))

        # ------------------------------
        # SUCCESS LOGIN
        # ------------------------------
        session["user_id"] = u["id"]
        log_event("login_success", {"user_id": u["id"]})

        logger.info(
            "Login SUCCESS user_id=%s email=%s ip=%s",
            u["id"], email, request.remote_addr
        )

        nxt = request.args.get("next") or url_for("pages.home")
        return redirect(nxt)

    return render_template("login.html", user=current_user())


# ---------------------------------------------------------
#  LOGOUT
# ---------------------------------------------------------
@bp.get("/logout")
def logout():
    uid = session.get("user_id")
    session.pop("user_id", None)

    log_event("logout", {"user_id": uid})
    logger.info(
        "Logout user_id=%s ip=%s",
        uid, request.remote_addr
    )

    flash("Çıkış yapıldı.", "ok")
    return redirect(url_for("pages.home"))
