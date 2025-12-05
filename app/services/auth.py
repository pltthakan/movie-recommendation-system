# app/services/auth.py
from functools import wraps
from flask import session, flash, redirect, url_for, request
from ..db import db

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Bu işlem için giriş yapmalısınız.", "warn")
            return redirect(url_for("auth.login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session:
        return None
    with db() as con, con.cursor() as cur:
        cur.execute("SELECT id, username, email FROM users WHERE id=%s", (session["user_id"],))
        return cur.fetchone()
