# app/config.py
import os

class Config:
    SECRET_KEY = os.getenv("APP_SECRET", "dev-secret-change-me")
    TMDB_API_KEY = os.getenv("TMDB_API_KEY")
    TMDB_BASE = "https://api.themoviedb.org/3"
    TZ = os.getenv("TZ", "Europe/Istanbul")
    AUTO_WARMUP = os.getenv("AUTO_WARMUP", "0")

def load_config(app):
    app.config.from_object(Config)
    assert app.config["TMDB_API_KEY"], "Lütfen TMDB_API_KEY ortam değişkenini ayarlayın."
