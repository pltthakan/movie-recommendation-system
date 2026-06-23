"""Versioned behavioural event schema and request-side producer helpers."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from flask import g, request, session

from ..db import db
from .event_bus import publish
from .utils import now_utc, sha1

logger = logging.getLogger(__name__)

# These are product interactions, not technical request logs. Keeping the
# vocabulary bounded makes downstream analytics and feature engineering stable.
BEHAVIOR_EVENT_TYPES = frozenset({
    "impression", "click", "detail_view", "trailer_start",
    "favorite", "unfavorite", "like", "dislike", "unlike",
})


def _session_id() -> str:
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    return sid


def build_event(
    event_type: str,
    *,
    movie_id: int | None = None,
    source: str | None = None,
    context: dict[str, Any] | None = None,
    status: int | None = None,
    path: str | None = None,
    method: str | None = None,
) -> dict[str, Any]:
    """Create the canonical event envelope. Client input never supplies identity."""
    payload = dict(context or {})
    if movie_id is not None:
        payload["movie_id"] = int(movie_id)
    return {
        "event_id": uuid.uuid4().hex,
        "schema_version": 1,
        "event_type": event_type,
        "occurred_at": now_utc().isoformat(),
        "user_id": session.get("user_id"),
        "session_id": _session_id(),
        "source": source or "web",
        "path": path or request.path,
        "method": method or request.method,
        "status": status,
        "ip_hash": sha1(request.headers.get("X-Forwarded-For", request.remote_addr) or ""),
        "ua_hash": sha1(request.headers.get("User-Agent", "") or ""),
        "referrer": (request.headers.get("Referer", "") or "")[:300],
        "context": payload,
    }


def persist_event(event: dict[str, Any]) -> bool:
    """Idempotently store an event; used by the worker and Redis-down fallback."""
    created_at = event.get("occurred_at") or now_utc()
    with db() as con, con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_events(
                event_id, schema_version, user_id, session_id, event_type, source,
                path, method, status, ip_hash, ua_hash, referrer, payload, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (event_id) WHERE event_id IS NOT NULL DO NOTHING
            RETURNING id
            """,
            (
                event.get("event_id"), event.get("schema_version", 1), event.get("user_id"),
                event.get("session_id"), event["event_type"], event.get("source"),
                event.get("path"), event.get("method"), event.get("status"),
                event.get("ip_hash"), event.get("ua_hash"), event.get("referrer"),
                json.dumps(event.get("context") or {}), created_at,
            ),
        )
        inserted = cur.fetchone() is not None
        con.commit()
        return inserted


def emit_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    event = build_event(event_type, **kwargs)
    if not publish(event):
        # Observability remains available during a Redis outage. State-changing
        # routes have already committed their own authoritative DB transaction.
        try:
            persist_event(event)
        except Exception:
            logger.exception("Could not persist fallback event event_id=%s", event["event_id"])
    return event


def emit_behavior_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    if event_type not in BEHAVIOR_EVENT_TYPES:
        raise ValueError(f"Unsupported behavioural event: {event_type}")
    return emit_event(event_type, **kwargs)


def log_event(event_type: str, payload: dict | None = None, status: int | None = None,
              path: str | None = None, method: str | None = None):
    """Store technical/audit events without blocking the behavioural stream."""
    event = build_event(event_type, context=payload, status=status, path=path, method=method)
    try:
        persist_event(event)
    except Exception:
        logger.exception("Could not persist audit event event_id=%s", event["event_id"])
    return event


def register_event_logging(app):
    @app.before_request
    def _ev_before():
        import time
        g._t0 = time.monotonic()

    @app.after_request
    def _ev_after(resp):
        if request.path.startswith("/static"):
            return resp

        import time
        sensitive = (request.path in ("/login", "/register")) and request.method == "POST"
        ms = int((time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000)
        payload = {"ms": ms}
        if not sensitive:
            payload["qs"] = {k: (v[:80] if isinstance(v, str) else v) for k, v in request.args.items()}
        log_event("http_request", payload=payload, status=resp.status_code)
        return resp
