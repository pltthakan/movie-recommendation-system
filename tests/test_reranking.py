import numpy as np

from app.services.recommender import mmr_rerank


def test_mmr_reranking_prefers_a_diverse_second_movie():
    embeddings = np.asarray([
        [1.0, 0.0],
        [0.995, 0.1],
        [0.0, 1.0],
    ], dtype=np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    scores = np.asarray([0.90, 0.89, 0.75], dtype=np.float32)
    metadata = {0: {"genre_ids": [1]}, 1: {"genre_ids": [1]}, 2: {"genre_ids": [2]}}

    selected = mmr_rerank([0, 1, 2], scores, embeddings, metadata, top_n=2, diversity_lambda=0.70)

    assert selected == [0, 2]
