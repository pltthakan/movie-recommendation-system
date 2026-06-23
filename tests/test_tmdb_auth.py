from unittest.mock import Mock, patch

from flask import Flask

from app.services.tmdb import tmdb_get


def _response():
    response = Mock()
    response.json.return_value = {"results": []}
    return response


def test_tmdb_v4_token_uses_bearer_authorization():
    app = Flask(__name__)
    app.config.update(TMDB_BASE="https://api.themoviedb.org/3", TMDB_API_KEY=None,
                      TMDB_READ_ACCESS_TOKEN="eyJ.test.token")
    with app.app_context(), patch("app.services.tmdb.requests.get", return_value=_response()) as get:
        tmdb_get("/movie/now_playing", {"page": 1})

    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer eyJ.test.token"
    assert "api_key" not in get.call_args.kwargs["params"]


def test_tmdb_v3_key_uses_query_parameter():
    app = Flask(__name__)
    app.config.update(TMDB_BASE="https://api.themoviedb.org/3", TMDB_API_KEY="v3-key",
                      TMDB_READ_ACCESS_TOKEN=None)
    with app.app_context(), patch("app.services.tmdb.requests.get", return_value=_response()) as get:
        tmdb_get("/movie/now_playing", {"page": 1})

    assert get.call_args.kwargs["params"]["api_key"] == "v3-key"
    assert get.call_args.kwargs["headers"] is None
