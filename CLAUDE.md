# pikol-backend

FastAPI + SQLAlchemy 2.0 (async) + Postgres. The court booking API for Pikol.

System-level brief: `../CLAUDE.md` — booking vocabulary, the two status axes,
the PENDING trap, venue scoping, the role/permission map, and deployment
rules. Read it first; this file covers only what is specific to this repo.

Design: `../docs/plans/2026-09-09-court-booking-system-design.md`.
Schema: `../docs/adr/pikol.dbml` (source of truth).

## Stack

Python **3.12** (pinned in `.python-version`), FastAPI, SQLAlchemy 2.0 async
over asyncpg, Alembic, Pydantic v2 + pydantic-settings, pytest +
pytest-asyncio + httpx, ruff, mypy `--strict`, uv, Docker Compose for local
Postgres only.

## Non-negotiables

- **snake_case end-to-end** — DB column, domain object, and JSON payload use
  the same name. No camelCase translation at the boundary.
- **All routes under `/api/v1/`** via `src/utils/api.py:API_PREFIX`.
  `/health` and `/health/ready` are deliberately outside it — infrastructure,
  not API.
- **Money is integer centavos.** Never float. Never a `Decimal` column.
- **Times are `timestamptz` in UTC. Day boundaries are Manila-local** — never
  `starts_at::date`. Use `src/utils/time.py:manila_day_bounds`, which exists
  so this cannot be got wrong by hand.
- **Request schemas set `extra="forbid"`** — asima's `forbidNonWhitelisted`.
  This applies to *request* schemas only. `Parameters` deliberately uses
  `extra="ignore"`: its input is our own table, and forbidding extras would
  500 every booking during the window between a migration and its deploy.
- **No `if (env)` in application code.** Environment differences are config
  and adapter selection only.
- **Every read of `booking_status = 'PENDING'` filters `expires_at > now()`.**
  No exceptions. See the PENDING trap in `../CLAUDE.md`.
- **Native PostgreSQL enums, never varchar.** Declare
  `postgresql.ENUM(..., create_type=False)` — the dialect type, *not* generic
  `sqlalchemy.Enum`, which silently swallows `create_type` — and call
  `.create(op.get_bind(), checkfirst=True)` explicitly in the migration, or
  the type is emitted twice and the migration fails with "type already
  exists". Adding a value later is always a hand-written
  `ALTER TYPE ... ADD VALUE`; autogenerate does not handle it.
- **Never log a provider payload.** `webhook_events.payload` and
  `payments.raw_payload` hold payer name, email, and mobile. Log the
  `provider_event_id` and the outcome.

## Layering

Each feature is `src/<feature>/{controllers,domain,dto,persistence}/`,
mirroring asima's NestJS modules. The domain layer is **rich only where
invariants live** — `bookings`, `pricing`, `payments` get aggregates and value
objects with no SQLAlchemy imports. CRUD features (`venues`, `courts`,
`users`) go router → service → model, with no mapper.

`config/` is environment (secrets, read at boot). `parameters/` is business
rules (prices, windows, caps — in Postgres, tunable without a deploy). Do not
conflate them.

## What exists now (Phase 1 complete)

| Module | Provides |
|---|---|
| `src/main.py` | `create_app(settings=None)`, `app` |
| `src/config/settings.py` | `Settings`, `get_settings()` — fails at boot on bad env |
| `src/database/session.py` | `engine`, `SessionLocal`, `get_session` |
| `src/database/base.py` | `Base` — every model extends it |
| `src/parameters/service.py` | `ParameterService`, `parameter_service` (TTL-cached) |
| `src/utils/request_id.py` | `RequestIDMiddleware`, `request_id_var` |
| `src/utils/log.py` | `configure_logging(debug)` — JSON lines carrying request_id |
| `src/utils/errors.py` | `register_exception_handlers(app)`, `ErrorResponse` |
| `src/utils/pagination.py` | `Page[T]`, `Page.of(...)` |
| `src/utils/time.py` | `MANILA`, `manila_day_bounds(day)` |

Phase 2 adds auth routers to `create_app()`, models extending `Base`, and the
FK from `parameters.updated_by_user_id` to `users`.

## Testing

- `pytest-asyncio` is in **auto** mode; async tests need no decorator.
- **`db_session`** wraps each test in a transaction that is always rolled
  back. The test sees its own writes — including after `commit()` — and the
  database never does. Use it for every write test.
- **`client`** overrides the app's `get_session` with that same rolling-back
  session. Without the override the app would open its own session on the real
  engine and commit for real, making the fixture decorative.
- An autouse fixture disposes the engine after each test. pytest-asyncio gives
  every test its own event loop, and a pooled asyncpg connection reused across
  loops raises `Future attached to a different loop` — deterministic, but it
  presents as flakiness.

## Tooling gotchas (each one has already cost a red gate)

- **`ruff check .` covers `tests/` too.** Imports appended to the bottom of an
  existing test file trip `E402`/`I001`.
- **In `migrations/env.py`, the model import goes ABOVE the `src.*`
  from-imports.** isort orders a plain `import x` before `from x import y`
  within a section; below them it fails `I001`.
- **Generics use PEP 695** — `class Page[T](BaseModel)`, not
  `Generic[T]`. At `target-version = py312`, ruff's `UP046` rejects the older
  spelling.
- **`.python-version` pins 3.12.** `requires-python = ">=3.12"` is a floor, so
  without the pin uv installs the newest Python while mypy and ruff are
  configured for 3.12 — the type checker then reasons about a different
  interpreter than the one running the tests.
- **Alembic's URL never goes through `config.set_main_option()`.** That writes
  into ConfigParser, which performs `%` interpolation, so a Supabase password
  containing `%40` raises `invalid interpolation syntax` — passing locally and
  in CI, failing on the first production migration.
- **Do not replace `alembic.ini` wholesale.** `env.py` calls `fileConfig()`
  unconditionally; dropping the generated `[loggers]`/`[handlers]`/
  `[formatters]` sections kills every alembic command with
  `KeyError: 'formatters'`.
- **Run uvicorn with `--no-access-log`.** Its access logger sets
  `propagate = False` and keeps a plain-text formatter, so leaving it on logs
  every request twice and breaks the JSON-lines format.
- **`ruff format .` before every commit.** CI runs `ruff format --check`.

## Local Postgres is on host port 5433

Not 5432 — another project on this machine already binds it. The container
still listens on 5432 internally, so `docker compose exec postgres psql` is
unaffected, and CI is unaffected because it runs its own service container.
`.env.example` already points at 5433.

## Commands

```bash
docker compose up -d                       # local Postgres, host port 5433
uv sync
uv run alembic upgrade head
uv run uvicorn src.main:app --reload --no-access-log
uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest
```

CI runs lint, format check, mypy, `alembic upgrade head` against a real
Postgres service, and the test suite. All five must pass.
