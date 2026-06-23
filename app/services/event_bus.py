"""Redis Streams producer for versioned behavioural events.

The stream provides at-least-once delivery. Consumers use ``event_id`` as an
idempotency key when persisting the event in PostgreSQL.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from flask import current_app

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _client(redis_url: str):
    import redis
    return redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)


def publish(event: dict) -> bool:
    """Publish an event without making the user-facing request depend on Redis."""
    if not current_app.config["EVENT_STREAM_ENABLED"]:
        return False

    try:
        client = _client(current_app.config["REDIS_URL"])
        client.xadd(
            current_app.config["EVENT_STREAM"],
            {"event": json.dumps(event, separators=(",", ":"))},
            maxlen=100_000,
            approximate=True,
        )
        return True
    except Exception:
        logger.warning("Event stream unavailable; persisting event synchronously", exc_info=True)
        return False
