# app/blueprints/auth.py
import psycopg
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from ..db import db
from ..services.utils import now_utc, sha1
from ..services.events import log_event
from ..services.auth import current_user

bp = Blueprint("auth", __name__)

@bp.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or not password:
            flash("Tüm alanlar zorunludur.", "error")
            return redirect(url_for("auth.register"))

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
            log_event("register_fail", {"reason": "unique_violation", "email_hash": sha1(email)})
            flash("Kullanıcı adı veya e-posta zaten kayıtlı.", "error")
            return redirect(url_for("auth.register"))

        session["user_id"] = user_id
        log_event("register_success", {"user_id": user_id})
        flash("Kayıt başarılı, hoş geldiniz!", "ok")
        return redirect(url_for("pages.home"))

    return render_template("register.html", user=current_user())

@bp.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        with db() as con, con.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            u = cur.fetchone()

        if not u or not check_password_hash(u["password_hash"], password):
            log_event("login_fail", {"email_hash": sha1(email)})
            flash("Geçersiz e-posta veya şifre.", "error")
            return redirect(url_for("auth.login"))

        session["user_id"] = u["id"]
        log_event("login_success", {"user_id": u["id"]})
        flash("Giriş yapıldı.", "ok")

        nxt = request.args.get("next") or url_for("pages.home")
        return redirect(nxt)

    return render_template("login.html", user=current_user())

@bp.get("/logout")
def logout():
    uid = session.get("user_id")
    session.pop("user_id", None)
    log_event("logout", {"user_id": uid})
    flash("Çıkış yapıldı.", "ok")
    return redirect(url_for("pages.home"))
