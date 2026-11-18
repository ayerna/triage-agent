#!/bin/bash
# scripts/start.sh
# Render startup script: normalize DATABASE_URL, run migrations, seed, start server.
set -e

# ------------------------------------------------------------------
# 1. Normalize DATABASE_URL for asyncpg driver.
#    Render supplies  postgres://...  or  postgresql://...
#    SQLAlchemy asyncpg requires  postgresql+asyncpg://...
# ------------------------------------------------------------------
if [ -n "$DATABASE_URL" ]; then
    DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql+asyncpg://}"
    DATABASE_URL="${DATABASE_URL/postgresql:\/\//postgresql+asyncpg://}"
    export DATABASE_URL
    echo "[start] DATABASE_URL driver normalized to asyncpg"
fi

# ------------------------------------------------------------------
# 2. Run Alembic migrations (idempotent — safe to run on every deploy)
# ------------------------------------------------------------------
echo "[start] Running Alembic migrations..."
python -m alembic upgrade head
echo "[start] Migrations done."

# ------------------------------------------------------------------
# 3. Seed synthetic data (idempotent — skips existing rows)
# ------------------------------------------------------------------
echo "[start] Seeding synthetic incidents..."
python scripts/seed.py
echo "[start] Seed done."

# ------------------------------------------------------------------
# 4. Start Uvicorn on the PORT Render assigns (default 10000)
# ------------------------------------------------------------------
PORT="${PORT:-8000}"
echo "[start] Starting uvicorn on port $PORT ..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
