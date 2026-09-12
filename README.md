# pikol-backend

Pikol court booking API — FastAPI, SQLAlchemy 2.0 async, Postgres.

Design: `../docs/plans/2026-09-09-court-booking-system-design.md`

## Local setup

```bash
cp .env.example .env
docker compose up -d
uv sync
uv run alembic upgrade head
uv run uvicorn src.main:app --reload --no-access-log
```

API docs at http://localhost:8000/docs, health at
http://localhost:8000/health.

Postgres is published on host port **5433**, not 5432, so it does not collide
with another project already bound to 5432 on the same machine. The container
still listens on 5432 internally, so `docker compose exec postgres psql` is
unaffected. `.env.example` already points at 5433.

## Checks

```bash
uv run ruff check . && uv run mypy src && uv run pytest
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run alembic downgrade -1
```

**Against Supabase:** load `.env.supabase.prod` (gitignored) and use the
**session pooler on port 5432**, never the transaction pooler on 6543 —
asyncpg uses prepared statements, which 6543 does not support, and the
failure appears only under production load.
