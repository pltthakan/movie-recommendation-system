#!/bin/sh
set -e

echo "[entrypoint] waiting for postgres..."
until pg_isready -h "${PGHOST:-db}" -p "${PGPORT:-5432}" -U "${PGUSER:-postgres}" -d "${PGDATABASE:-postgres}" >/dev/null 2>&1; do
  sleep 1
done
echo "[entrypoint] postgres is up"

# İstersen SBERT + candidate cache warmup (opsiyonel)
if [ "${AUTO_WARMUP}" = "1" ]; then
  echo "[entrypoint] running warmup..."
  python app.py warmup || true
fi

exec "$@"
