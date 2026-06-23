# app/config.py
import os

class Config:
    SECRET_KEY = os.getenv("APP_SECRET", "dev-secret-change-me")
    TMDB_API_KEY = os.getenv("TMDB_API_KEY")
    TMDB_READ_ACCESS_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN")
    TMDB_BASE = "https://api.themoviedb.org/3"
    TZ = os.getenv("TZ", "Europe/Istanbul")
    AUTO_WARMUP = os.getenv("AUTO_WARMUP", "0")
    # Event pipeline. Redis Streams is used as an at-least-once delivery queue.
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    EVENT_STREAM = os.getenv("EVENT_STREAM", "movie:behavior-events")
    EVENT_CONSUMER_GROUP = os.getenv("EVENT_CONSUMER_GROUP", "profile-workers-v2")
    EVENT_CONSUMER_NAME = os.getenv("EVENT_CONSUMER_NAME")
    EVENT_STREAM_ENABLED = os.getenv("EVENT_STREAM_ENABLED", "1") == "1"

def load_config(app):
    app.config.from_object(Config)
    assert (
        app.config["TMDB_API_KEY"] or app.config["TMDB_READ_ACCESS_TOKEN"]
    ), "Lütfen TMDB_API_KEY veya TMDB_READ_ACCESS_TOKEN ortam değişkenini ayarlayın."
