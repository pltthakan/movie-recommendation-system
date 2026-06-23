from flask import Flask, session

from app.services.events import BEHAVIOR_EVENT_TYPES, build_event


def test_behavior_event_has_versioned_server_owned_identity():
    app = Flask(__name__)
    app.secret_key = "test-secret"

    with app.test_request_context("/movie/42", headers={"User-Agent": "pytest"}):
        session["user_id"] = 7
        event = build_event("favorite", movie_id=42, source="movie_detail")

    assert "favorite" in BEHAVIOR_EVENT_TYPES
    assert event["schema_version"] == 1
    assert event["user_id"] == 7
    assert event["context"]["movie_id"] == 42
    assert event["session_id"]
    assert event["event_id"]
