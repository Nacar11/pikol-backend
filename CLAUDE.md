# pikol-backend

FastAPI + SQLAlchemy 2.0 (async) + Postgres. The court booking API for Pikol.

System-level brief: `../CLAUDE.md`.
Design: `../docs/plans/2026-09-09-court-booking-system-design.md`.

## Non-negotiables

- **snake_case end-to-end** — DB column, domain object, and JSON payload use
  the same name. No camelCase translation at the boundary.
- **All routes under `/api/v1/`** via `src/utils/api.py:API_PREFIX`.
  `/health` is deliberately outside it — infrastructure, not API.
- **Money is integer centavos.** Never float. Never a `Decimal` column.
- **Times are `timestamptz` in UTC. Day boundaries are Manila-local** —
  never `starts_at::date`, which returns the wrong 24 hours and misplaces
  the 12am-1am slot onto the previous day. Use
  `src/utils/time.py:manila_day_bounds`.
- **Request schemas set `extra="forbid"`.**
- **No `if (env)` in application code.** Environment differences are config
  and adapter selection only.
- **Every read of `booking_status = 'PENDING'` filters `expires_at > now()`.**
  Cleanup is lazy, so a lapsed hold keeps its row. `PENDING` alone never
  means "currently holding". No exceptions — this one has bitten already.
- **Native PostgreSQL enums, never varchar.** Adding a value is a
  hand-written `ALTER TYPE ... ADD VALUE`; autogenerate does not handle it.
  Declare `postgresql.ENUM(..., create_type=False)` and call `.create()`
  explicitly in the migration, or it is emitted twice.

## Layering

Each feature is `src/<feature>/{controllers,domain,dto,persistence}/`,
mirroring asima's NestJS modules. The domain layer is **rich only where
invariants live** — `bookings`, `pricing`, `payments` get aggregates and
value objects with no SQLAlchemy imports. CRUD features (`venues`, `courts`,
`users`) go router → service → model, with no mapper.

`config/` is environment (secrets, read at boot). `parameters/` is business
rules (prices, windows, caps — in Postgres, tunable without a deploy). Do
not conflate them.

## Schema

`../docs/adr/pikol.dbml` is the source of truth. Change it first, then write
the migration from it. Constraints it marks ⚠️ are incomplete in the diagram
and must be copied from its Appendix A — partial indexes, `NULLS NOT
DISTINCT`, and CHECKs cannot be expressed in DBML.

## Commands

```bash
docker compose up -d                       # local Postgres, host port 5433
uv run alembic upgrade head
uv run uvicorn src.main:app --reload --no-access-log
uv run ruff check . && uv run mypy src && uv run pytest
```
