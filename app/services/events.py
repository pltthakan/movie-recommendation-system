# app/services/events.py
import json
import uuid
import time
from flask import request, session, g
from ..db import db
from .utils import sha1, now_utc

def _session_id():
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    return sid

def log_event(event_type: str, payload: dict | None = None, status: int | None = None,
              path: str | None = None, method: str | None = None):
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
                uid, sid, event_type,
                path or request.path,
                method or request.method,
                status,
                sha1(ip),
                sha1(ua),
                ref[:300],
                json.dumps(payload or {}),
                now_utc(),
            ))
            con.commit()
    except Exception as e:
        print("[user_events] ERROR:", e)

def register_event_logging(app):
    @app.before_request
    def _ev_before():
        g._t0 = time.monotonic()

    @app.after_request
    def _ev_after(resp):
        if request.path.startswith("/static"):
            return resp

        sensitive = (request.path in ("/login", "/register")) and request.method == "POST"
        ms = int((time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000)
        payload = {"ms": ms}
        if not sensitive:
            payload["qs"] = {k: (v[:80] if isinstance(v, str) else v) for k, v in request.args.items()}

        log_event("http_request", payload=payload, status=resp.status_code)
        return resp
