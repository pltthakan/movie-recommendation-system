"""Compare exact pgvector cosine search and HNSW ANN search at several sizes."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from app.db import db

DIMENSIONS = 384


def vector_literal(vector) -> str:
    return "[" + ",".join(f"{float(value):.7g}" for value in vector) + "]"


def percentile(values, pct: float) -> float:
    return round(float(np.percentile(values, pct)), 3)


def create_dataset(size: int, hnsw_m: int, ef_construction: int, rng) -> None:
    with db() as con, con.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS vector_benchmark_embeddings;")
        cur.execute("CREATE TABLE vector_benchmark_embeddings(id BIGSERIAL PRIMARY KEY, embedding vector(384) NOT NULL);")
        batch_size = 500
        for start in range(0, size, batch_size):
            vectors = rng.normal(size=(min(batch_size, size - start), DIMENSIONS)).astype(np.float32)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            cur.executemany(
                "INSERT INTO vector_benchmark_embeddings(embedding) VALUES (%s::vector)",
                [(vector_literal(vector),) for vector in vectors],
            )
        con.commit()
        cur.execute("ANALYZE vector_benchmark_embeddings;")
        cur.execute(
            "CREATE INDEX vector_benchmark_hnsw ON vector_benchmark_embeddings "
            f"USING hnsw (embedding vector_cosine_ops) WITH (m={hnsw_m}, ef_construction={ef_construction});"
        )
        con.commit()


def nearest_ids(query: str, exact: bool, ef_search: int) -> tuple[list[int], float]:
    with db() as con, con.cursor() as cur:
        if exact:
            cur.execute("SET LOCAL enable_indexscan=off; SET LOCAL enable_bitmapscan=off;")
        else:
            cur.execute("SELECT set_config('hnsw.ef_search', %s, true);", (str(ef_search),))
        started = time.perf_counter()
        cur.execute(
            "SELECT id FROM vector_benchmark_embeddings ORDER BY embedding <=> %s::vector LIMIT 10",
            (query,),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return [row["id"] for row in cur.fetchall()], elapsed_ms


def benchmark(size: int, queries: int, hnsw_m: int, ef_construction: int, ef_search: int, rng) -> dict:
    create_dataset(size, hnsw_m, ef_construction, rng)
    exact_ms, hnsw_ms, recalls = [], [], []
    for _ in range(queries):
        query = rng.normal(size=DIMENSIONS).astype(np.float32)
        query /= np.linalg.norm(query)
        literal = vector_literal(query)
        exact_ids, elapsed = nearest_ids(literal, exact=True, ef_search=ef_search)
        exact_ms.append(elapsed)
        hnsw_ids, elapsed = nearest_ids(literal, exact=False, ef_search=ef_search)
        hnsw_ms.append(elapsed)
        recalls.append(len(set(exact_ids) & set(hnsw_ids)) / 10)
    return {
        "vectors": size, "queries": queries, "hnsw": {"m": hnsw_m, "ef_construction": ef_construction, "ef_search": ef_search},
        "exact_ms": {"p50": percentile(exact_ms, 50), "p95": percentile(exact_ms, 95)},
        "hnsw_ms": {"p50": percentile(hnsw_ms, 50), "p95": percentile(hnsw_ms, 95)},
        "recall_at_10": round(float(np.mean(recalls)), 3),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark pgvector exact search versus HNSW.")
    parser.add_argument("--sizes", default="1000,10000,50000")
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--m", type=int, default=32, help="HNSW graph connectivity; larger improves recall but costs build time.")
    parser.add_argument("--ef-construction", type=int, default=128)
    parser.add_argument("--ef-search", type=int, default=400)
    parser.add_argument("--output", default="/output/pgvector_hnsw.json")
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    rng = np.random.default_rng(42)
    results = [
        benchmark(int(size), args.queries, args.m, args.ef_construction, args.ef_search, rng)
        for size in args.sizes.split(",")
    ]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    if not args.keep_data:
        with db() as con, con.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS vector_benchmark_embeddings;")
            con.commit()
    print(json.dumps(results, indent=2))
