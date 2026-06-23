"""Consume behaviour events and refresh affected recommendation profiles."""
from __future__ import annotations

import json
import logging
import os
import socket
import time

import redis

from app import create_app
from app.db import db
from app.services.events import persist_event
from app.services.recommender import (
    CAND_TTL_SEC,
    build_user_recommendations,
    get_candidate_cache,
    get_or_build_user_profile,
    invalidate_user_cache,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

PROFILE_EVENTS = {"favorite", "unfavorite", "like", "dislike", "unlike", "trailer_start"}


def ensure_group(client, stream: str, group: str) -> None:
    try:
        # The worker backfills preferences from PostgreSQL at boot. New stream
        # consumers therefore start at the current tail, not years of audit logs.
        client.xgroup_create(stream, group, id="$", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def prepare_user(app, user_id: int) -> None:
    """Build one user's profile and recommendation cache from DB preferences."""
    with app.app_context():
        invalidate_user_cache(user_id)
        sig, profile = get_or_build_user_profile(user_id)
        candidates = get_candidate_cache()
        count = len(build_user_recommendations(user_id, sig, profile, candidates))
        logger.info("Prepared personalized cache user_id=%s recommendations=%s", user_id, count)


def handle_event(app, event: dict) -> None:
    """Persist raw event, then prepare an affected user's recommendation cache."""
    persist_event(event)
    user_id = event.get("user_id")
    if event.get("event_type") in PROFILE_EVENTS and user_id:
        prepare_user(app, int(user_id))


def warm_candidates(app) -> None:
    """Run TMDB/embedding work outside the request-serving web process."""
    with app.app_context():
        candidates = get_candidate_cache()
        logger.info("Candidate cache warmed candidates=%s", len(candidates["ids"]))


def backfill_user_caches(app) -> None:
    """Prepare users who interacted before this worker instance started."""
    with db() as con, con.cursor() as cur:
        cur.execute(
            """SELECT user_id FROM favorites
               UNION SELECT user_id FROM ratings
               UNION SELECT user_id FROM trailer_events"""
        )
        user_ids = [row["user_id"] for row in cur.fetchall()]
    for user_id in user_ids:
        prepare_user(app, int(user_id))


def read_messages(client, stream: str, group: str, consumer: str):
    # Reclaim messages abandoned by a crashed container before accepting new
    # ones. This closes the gap left by consumer-name changes on redeploy.
    _next_id, claimed, _deleted = client.xautoclaim(
        stream, group, consumer, min_idle_time=60_000, start_id="0-0", count=10
    )
    if claimed:
        return [(stream, claimed)]

    return client.xreadgroup(group, consumer, {stream: ">"}, count=10, block=5000)


def run() -> None:
    app = create_app()
    stream = app.config["EVENT_STREAM"]
    group = app.config["EVENT_CONSUMER_GROUP"]
    consumer = app.config["EVENT_CONSUMER_NAME"] or socket.gethostname()
    client = redis.Redis.from_url(app.config["REDIS_URL"], decode_responses=True, health_check_interval=30)
    last_warmup = 0.0

    while True:
        try:
            ensure_group(client, stream, group)
            if time.monotonic() - last_warmup >= CAND_TTL_SEC:
                warm_candidates(app)
                backfill_user_caches(app)
                last_warmup = time.monotonic()
            for _stream, messages in read_messages(client, stream, group, consumer):
                for message_id, fields in messages:
                    try:
                        event = json.loads(fields["event"])
                        handle_event(app, event)
                        client.xack(stream, group, message_id)
                    except Exception:
                        # Leave the message pending. It will be retried by this
                        # consumer after a restart, and persistence is idempotent.
                        logger.exception("Event processing failed: message_id=%s", message_id)
        except redis.exceptions.RedisError:
            logger.exception("Redis connection failed; retrying in 3 seconds")
            time.sleep(3)
        except Exception:
            logger.exception("Unexpected event worker failure; retrying in 3 seconds")
            time.sleep(3)


if __name__ == "__main__":
    run()
