# app/db.py
import os
import psycopg
from psycopg.rows import dict_row

def _pg_conninfo():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")
    dbn  = os.getenv("PGDATABASE", "postgres")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{dbn}"

def db():
    return psycopg.connect(_pg_conninfo(), row_factory=dict_row)

def _column_exists(con, table, column):
    with con.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            LIMIT 1
        """, (table, column))
        return cur.fetchone() is not None

def _index_exists(con, index_name):
    with con.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (index_name,))
        return bool(cur.fetchone()["exists"])

def init_db():
    with db() as con:
        with con.cursor() as cur:
            # `web` and `event-worker` start concurrently in Compose. PostgreSQL
            # does not make concurrent CREATE TABLE IF NOT EXISTS fully safe, so
            # serialize schema initialization across processes.
            cur.execute("SELECT pg_advisory_xact_lock(4815162342);")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id            BIGSERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS comments(
                id              BIGSERIAL PRIMARY KEY,
                movie_id        INTEGER NOT NULL,
                user_id         BIGINT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content         TEXT    NOT NULL,
                is_spoiler      BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL,
                sentiment_label TEXT,
                sentiment_score DOUBLE PRECISION
            );
            """)
            if not _column_exists(con, "comments", "sentiment_label"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_label TEXT;")
            if not _column_exists(con, "comments", "sentiment_score"):
                cur.execute("ALTER TABLE comments ADD COLUMN sentiment_score DOUBLE PRECISION;")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS favorites(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS ratings(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                value      SMALLINT NOT NULL CHECK (value IN (-1, 1)),
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(user_id, movie_id)
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS trailer_events(
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id   INTEGER NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'watch_trailer',
                created_at TIMESTAMPTZ NOT NULL
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trailer_events_user ON trailer_events(user_id, created_at DESC);")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS movie_embeddings(
                movie_id   INTEGER PRIMARY KEY,
                text_hash  TEXT,
                embedding  JSONB,
                embedding_vector vector(384),
                updated_at TIMESTAMPTZ
            );
            """)
            if not _column_exists(con, "movie_embeddings", "text_hash"):
                cur.execute("ALTER TABLE movie_embeddings ADD COLUMN text_hash TEXT;")
            if not _column_exists(con, "movie_embeddings", "updated_at"):
                cur.execute("ALTER TABLE movie_embeddings ADD COLUMN updated_at TIMESTAMPTZ;")
            if not _column_exists(con, "movie_embeddings", "embedding_vector"):
                cur.execute("ALTER TABLE movie_embeddings ADD COLUMN embedding_vector vector(384);")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_movies(
                movie_id    INTEGER PRIMARY KEY,
                data        JSONB NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles(
                user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                signals_hash TEXT NOT NULL,
                embedding    JSONB NOT NULL,
                embedding_vector vector(384),
                updated_at   TIMESTAMPTZ NOT NULL
            );
            """)
            if not _column_exists(con, "user_profiles", "embedding_vector"):
                cur.execute("ALTER TABLE user_profiles ADD COLUMN embedding_vector vector(384);")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_recommendations(
                user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                movie_id     INTEGER NOT NULL,
                score        DOUBLE PRECISION NOT NULL,
                data         JSONB NOT NULL,
                signals_hash TEXT NOT NULL,
                updated_at   TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(user_id, movie_id)
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_recs_user ON user_recommendations(user_id, updated_at DESC);")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, id DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id, id DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_movie ON ratings(movie_id, value);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_movie ON comments(movie_id, id DESC);")

            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_events(
                id         BIGSERIAL PRIMARY KEY,
                event_id   TEXT,
                schema_version SMALLINT NOT NULL DEFAULT 1,
                user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
                session_id TEXT,
                event_type TEXT NOT NULL,
                source     TEXT,
                path       TEXT,
                method     TEXT,
                status     INTEGER,
                ip_hash    TEXT,
                ua_hash    TEXT,
                referrer   TEXT,
                payload    JSONB,
                created_at TIMESTAMPTZ NOT NULL
            );
            """)
            if not _column_exists(con, "user_events", "event_id"):
                cur.execute("ALTER TABLE user_events ADD COLUMN event_id TEXT;")
            if not _column_exists(con, "user_events", "schema_version"):
                cur.execute("ALTER TABLE user_events ADD COLUMN schema_version SMALLINT NOT NULL DEFAULT 1;")
            if not _column_exists(con, "user_events", "source"):
                cur.execute("ALTER TABLE user_events ADD COLUMN source TEXT;")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_events_event_id ON user_events(event_id) WHERE event_id IS NOT NULL;")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_events_user ON user_events(user_id, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_events_type ON user_events(event_type, created_at DESC);")

            # Migrate existing JSONB vectors incrementally, then enable ANN
            # cosine search. JSONB remains temporarily for backwards safety.
            cur.execute("""
                UPDATE movie_embeddings
                SET embedding_vector=embedding::text::vector
                WHERE embedding_vector IS NULL AND embedding IS NOT NULL
            """)
            cur.execute("""
                UPDATE user_profiles
                SET embedding_vector=embedding::text::vector
                WHERE embedding_vector IS NULL AND embedding IS NOT NULL
            """)
            if not _index_exists(con, "idx_movie_embeddings_hnsw_cosine_v2"):
                cur.execute("DROP INDEX IF EXISTS idx_movie_embeddings_hnsw_cosine;")
                cur.execute("""
                    CREATE INDEX idx_movie_embeddings_hnsw_cosine_v2
                    ON movie_embeddings USING hnsw (embedding_vector vector_cosine_ops)
                    WITH (m=32, ef_construction=128)
                """)

        con.commit()
