"""Load a larger TMDB catalog so pgvector retrieval can operate at scale."""
from __future__ import annotations

import argparse
import json
import logging

from app import create_app
from app.db import db
from app.services.embeddings import ensure_embeddings
from app.services.tmdb import tmdb_get
from app.services.utils import now_utc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def movie_data(movie: dict) -> dict:
    return {
        "id": movie.get("id"), "title": movie.get("title"),
        "overview": movie.get("overview"), "genre_ids": movie.get("genre_ids") or [],
        "popularity": movie.get("popularity") or 0.0,
        "poster_path": movie.get("poster_path"), "vote_average": movie.get("vote_average"),
        "release_date": movie.get("release_date"),
    }


def sync(pages: int) -> None:
    app = create_app()
    with app.app_context():
        for page in range(1, pages + 1):
            results = tmdb_get("/discover/movie", {
                "page": page, "language": "en-US", "sort_by": "popularity.desc",
                "include_adult": "false", "include_video": "false",
            }).get("results") or []
            records = [movie_data(movie) for movie in results if movie.get("id") and movie.get("overview")]
            with db() as con, con.cursor() as cur:
                for data in records:
                    cur.execute(
                        """INSERT INTO candidate_movies(movie_id, data, updated_at)
                           VALUES (%s,%s,%s)
                           ON CONFLICT (movie_id) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at""",
                        (data["id"], json.dumps(data), now_utc()),
                    )
                con.commit()
            texts = {
                data["id"]: " [SEP] ".join(part for part in (
                    data["title"] or "", data["overview"] or "",
                    " ".join(str(genre) for genre in data["genre_ids"]),
                ) if part)
                for data in records
            }
            ensure_embeddings(list(texts), text_overrides=texts)
            logger.info("Catalog sync page=%s/%s movies=%s", page, pages, len(records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate the pgvector movie catalog from TMDB.")
    parser.add_argument("--pages", type=int, default=50, help="TMDB pages to ingest; 500 pages is roughly 10k films.")
    args = parser.parse_args()
    sync(args.pages)
