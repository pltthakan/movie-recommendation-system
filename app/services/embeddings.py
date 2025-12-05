# app/services/embeddings.py
import json
import hashlib
import numpy as np
from functools import lru_cache
from .tmdb import tmdb_get
from ..db import db
from .utils import now_utc

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

@lru_cache(maxsize=1)
def sbert():
    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers kurulu değil. `pip install sentence-transformers numpy`")
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def _hash_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()

@lru_cache(maxsize=8192)
def movie_text_en(movie_id: int) -> str:
    d = tmdb_get(f"/movie/{movie_id}", {"language": "en-US"})
    title = (d.get("title") or d.get("original_title") or "").strip()
    overview = (d.get("overview") or "").strip()
    genres = ", ".join([g.get("name","") for g in (d.get("genres") or []) if g.get("name")])
    parts = [title, overview, genres]
    return " [SEP] ".join([p for p in parts if p])

def embed_texts(texts):
    vecs = sbert().encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)

def ensure_embeddings(movie_ids):
    movie_ids = [int(x) for x in set(movie_ids or []) if x]
    if not movie_ids:
        return {}

    with db() as con:
        existing = {}
        with con.cursor() as cur:
            cur.execute(
                "SELECT movie_id, text_hash, embedding FROM movie_embeddings WHERE movie_id = ANY(%s)",
                (movie_ids,),
            )
            for r in cur.fetchall():
                existing[r["movie_id"]] = r

        need, texts = [], []
        for mid in movie_ids:
            text = movie_text_en(mid)
            h = _hash_text(text)
            row = existing.get(mid)
            if (row is None) or (row.get("text_hash") != h) or (row.get("embedding") is None):
                need.append((mid, h, text))
                texts.append(text)

        if need:
            vecs = embed_texts(texts)
            with con.cursor() as cur:
                for (mid, h, _text), vec in zip(need, vecs):
                    cur.execute(
                        """
                        INSERT INTO movie_embeddings(movie_id, text_hash, embedding, updated_at)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (movie_id)
                        DO UPDATE SET text_hash=EXCLUDED.text_hash,
                                      embedding=EXCLUDED.embedding,
                                      updated_at=EXCLUDED.updated_at
                        """,
                        (mid, h, json.dumps(vec.tolist()), now_utc()),
                    )
            con.commit()

        out = {}
        with con.cursor() as cur:
            cur.execute(
                "SELECT movie_id, embedding FROM movie_embeddings WHERE movie_id = ANY(%s)",
                (movie_ids,),
            )
            for r in cur.fetchall():
                if r["embedding"] is not None:
                    out[r["movie_id"]] = np.asarray(r["embedding"], dtype=np.float32)
        return out
