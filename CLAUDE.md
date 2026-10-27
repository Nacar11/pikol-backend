# pikol-backend

FastAPI + SQLAlchemy 2.0 (async) + Postgres. The court booking API for Pikol.

System-level brief: `../CLAUDE.md` — booking vocabulary, the two status axes,
the PENDING trap, venue scoping, user groups and permissions, and deployment
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

## What exists now (user groups complete; Maya adapter pending)

| Module | Provides |
|---|---|
| `src/main.py` | `create_app(settings=None)`, `app` |
| `src/config/settings.py` | `Settings`, `get_settings()` — fails at boot on bad env |
| `src/database/session.py` | `engine`, `SessionLocal`, `get_session` |
| `src/database/base.py` | `Base` — every model extends it |
| `src/parameters/service.py` | `ParameterService`, `parameter_service` (TTL-cached) |
| `src/parameters/admin.py` | `parameter_admin_service.update` — row-locked, validates the WHOLE `Parameters` model with the candidate value (a cross-field break is `422 INVALID_PARAMETER`, detail `"{key}: msg"`; unknown key `404`), stamps `updated_at`/`updated_by_user_id`, commits, then invalidates the cache. Routes: `GET /admin/parameters`, `PATCH /admin/parameters/{key}` `{value: str}`, both `PARAMETER:Manage` |
| `src/utils/request_id.py` | `RequestIDMiddleware`, `request_id_var` |
| `src/utils/log.py` | `configure_logging(debug)` — JSON lines carrying request_id |
| `src/utils/errors.py` | `AppError(status, code, detail)`, `register_exception_handlers(app)`, `ErrorResponse` |
| `src/utils/pagination.py` | `Page[T]`, `Page.of(...)` |
| `src/utils/time.py` | `MANILA`, `manila_day_bounds(day)` |
| `src/auth/dependencies.py` | `get_current_user`, `require_super_admin`, `forbidden()` |
| `src/auth/access.py` | `get_venue_access` (`X-Venue-ID` → `VenueAccess(scope, permissions, is_master)`, per venue, never summed), `require_permission(code)`, `get_venue_scope`, `require_customer` (Customer-group membership) |
| `src/auth/scope.py` | `VenueScope(all_venues, venue_id).where(column)` |
| `src/auth/security.py` | argon2 passwords, HS256 access/refresh JWTs, hashed single-use email tokens |
| `src/auth/service.py` | `auth_service` — register, verify, login, refresh rotation, logout, reset, accept invite; `insert_user` |
| `src/auth/rate_limit.py` | `limiter` (slowapi), `LOGIN_LIMIT`, `EMAIL_LIMIT` |
| `src/email/port.py` | `EmailMessage`, `EmailSender`, `get_email_sender`, `send_quietly` |
| `src/users/domain/roles.py` | `PERMISSIONS` — every code |
| `src/users/domain/groups.py` | `GroupKind`, `OWNER_ONLY`, the Venue master / Front desk seeds |
| `src/users/persistence/repository.py` | `user_repository` — `list`/`get_scoped` take `scope=` with no default; "at a venue" means holding one of its groups |
| `src/users/persistence/group_repository.py` | `group_repository` — memberships, codes per venue, `seed_venue_groups` (called in the venue's own transaction), scoped group reads |
| `src/users/group_service.py` | `group_service` — every escalation rule for groups, permissions and members (ADR 0003). Routes: `/admin/user-groups` (+ `/permissions`, `/members`), `/admin/permissions`, all `USER:Manage` |
| `src/users/controllers/me.py` | `/users/me` (+ `is_customer`), `/users/me/permissions` (per `X-Venue-ID`, union without), `/users/me/venues` |
| `src/users/cli.py` | `create-super-admin` — the only way a SUPER_ADMIN is made |
| `src/venues/` | `venue_repository`, `venue_service`; admin routes (`VENUE:Manage`) and the catalog (`GET /venues`, `/venues/{id}/courts`) |
| `src/courts/` | `CourtModel`, `court_repository`, `court_service` (`get` → scoped 404); admin routes (`COURT:Manage`) |
| `src/pricing/domain/pricing.py` | `PriceRule`, `PriceResolver(rules, default).price_at(starts_at)`, `convenience_fee_centavos` — pure |
| `src/pricing/service.py` | `pricing_service.resolver_for(session, court_id, scope=)` — one query, then in-memory |

| `src/bookings/domain/` | enums, `BookingError(code, detail, kind)`, `BookingSnapshot`, every transition guard, `display_status`, `new_reference_code` — pure |
| `src/bookings/persistence/repository.py` | `booking_repository` — `insert`, **`set_status` (the only status writer)**, `expire_lapsed`, `count_live_holds`, `occupancy`, scoped and per-player reads |
| `src/bookings/service.py` | `booking_service` — holds, availability, player cancel, walk-ins, staff cancel, mark-paid, outcome, closures, `daily_occupancy` (`GET /admin/bookings/occupancy`) |
| `src/summaries/` | `GET /admin/venues/{id}/courts/summary` (`COURT:Manage`; rate and booked hours null without `PRICE:Manage` / `BOOKING:ReadAny`) and `GET /admin/venues/summary` (`VENUE:Manage`) — one request per Courts/Venues page instead of one per card. Booked hours reuse `occupancy`, minus closures. Its router is included before the venues router so `/venues/summary` is not read as an id |
| `src/devtools/e2e_seed.py` | `python -m src.devtools.e2e_seed` — the Playwright suite's owner, venues, courts and front desk; exits 2 unless the database name ends in `_e2e` |
| `src/bookings/http_errors.py` | maps `BookingError.kind` → 422 (`invalid`) / 409 (`conflict`) |

| `src/payments/domain/port.py` | `PaymentProvider` (`create_checkout`, `parse_webhook` — untrusted, `fetch_payment` — authoritative), `ProviderError`, `PaymentNotFound` |
| `src/payments/adapters/maya_wire.py` | Maya's JSON shapes, `to_pesos`/`from_pesos`, status map — the ONLY place pesos exist |
| `src/payments/adapters/mock.py` | `MockProvider` — Maya-shaped JSON in memory, read back through `maya_wire` |
| `src/payments/provider.py` | `get_payment_provider` (by `PAYMENT_PROVIDER`; only `mock` today) |
| `src/payments/service.py` | `payment_service.start_checkout`, **`sync_payment`** (fetch → record event once → apply), late-payment re-acquisition, `needs_resolution` flagging, receipt |
| `src/payments/controllers/` | `POST /webhooks/maya`; the mock checkout page (only if `MOCK_CHECKOUT_ENABLED`) |

**Next:** `MayaProvider` — an httpx adapter over `maya_wire` (public key for
Create Checkout, secret key for Retrieve Payment, sandbox base URL). Its first
job is to confirm `maya_wire`'s field names against real sandbox responses.

## Payment rules in practice (ADR 0002)

- **Never act on a webhook body.** `sync_payment` takes a payment id and
  asks the provider; the webhook and `refresh-payment` both go through it.
- **The event insert is the idempotency check** (`"{payment_id}:{status}"`).
  Do not add an "already processed?" SELECT.
- **Money with no court is flagged, never dropped.** Any paid-but-unhousable
  outcome sets `needs_resolution` and leaves a `[system]` note.
- **Provider calls happen after the hold commits**, never inside it.
- **`MOCK_CHECKOUT_ENABLED` must be false on any public deploy.**

## Booking rules in practice

- **Only `BookingRepository` writes `booking_status`** — `set_status` moves a
  group and all its slots together and clears `expires_at` when leaving
  `PENDING` (`ck_pending_has_expiry`). Assigning `group.booking_status`
  anywhere else corrupts availability.
- **Every `PENDING` read adds `expires_at > now`** — `count_live_holds`,
  `occupancy`, and `BookingSnapshot.is_live_hold`. Both repository filters
  are mutation-checked by tests; keep it that way when adding a read.
- **The index decides collisions.** Never pre-check a slot with a SELECT;
  `insert` turns `uq_court_slot_active` into 409 `SLOT_UNAVAILABLE`.
- **Writes act on rows already fetched through a scoped read**, which is why
  `insert`/`set_status`/`expire_lapsed` take no `scope`. Reads take one.
- Guards take `now` as a parameter; services pass `datetime.now(UTC)`.
- Known limit: two simultaneous hold requests from one player can exceed
  `max_active_holds_per_player` by one. Accepted — it is a soft cap.

## Venue scope in practice

- `scope.where(column)` filters on the row's OWN `venue_id` (a court's, or a
  rule's court's) — never on an id taken from the URL.
- The catalog passes `VenueScope.everything()` explicitly. Never omit a
  scope; if a read genuinely spans venues, say so with `everything()`.
- `*_service.get(..., scope=)` raises 404 `NOT_FOUND` for out-of-scope rows.
  Use it before acting on a child resource (court before its price rules).

## Auth rules (Phase 2)

- **Expected failures raise `AppError(status, code, detail)`**, never
  `HTTPException` — the envelope carries `code`, which the frontend branches
  on (`EMAIL_NOT_VERIFIED` renders a resend action).
- **Login reveals account state only after a correct password.** Unknown
  email and wrong password are the same 401 `INVALID_CREDENTIALS`, and an
  unknown email still runs argon2 against a dummy hash so timing matches.
- **Every request loads the user.** That is what makes deactivation
  immediate; do not "optimise" it into trusting token claims.
- **Refresh replay revokes the whole family** (asima ADR 0002). Password
  change, password reset, logout, and deactivation all revoke every refresh
  token.
- **Privilege fields come from the token, the active venue or the endpoint,
  never the body.** A non-owner's new group takes the active venue and kind
  STAFF; a body naming either is a 422. Invites name a `group_id`, whose
  venue is the group's.
- **Permissions are per venue** (`get_venue_access`): never authorise on the
  union across a person's venues, which only `/users/me/permissions` without a
  header returns, for coarse client checks.
- **Every escalation rule has a test that fails when it is removed** (ADR
  0003). Keep it that way when adding one.
- **Email is sent after commit, via `send_quietly`**, which logs and swallows
  failures — every email has a resend path.
- **`EMAIL_PROVIDER=console` logs live tokens.** Development only; Phase 9
  adds a real sender before go-live.

## Testing

- `pytest-asyncio` is in **auto** mode; async tests need no decorator.
- **`outbox`** is a `MemoryEmailSender` wired into `client`; read emailed
  links back from `outbox.outbox[-1].body`.
- **`tests/auth/helpers.py`** — `make_user`, `make_venue`, `login`, `bearer`,
  and `RoleCode`: a test-only shorthand for which group `make_user` joins
  (`PLAYER` → Customer, `VENUE_MANAGER` → the venue's master group,
  `VENUE_STAFF` → its Front desk, `SUPER_ADMIN` → none). `make_venue` seeds
  the venue's groups.
- **`provider`** is a fresh `MockProvider` wired into `client`;
  `tests/payments/helpers.py` — `pay(...)` settles and delivers the webhook,
  `checkout_id`, `group` (reloads a booking with its slots).
- `tests/bookings/conftest.py` — a two-venue `world`, `hold(...)`,
  `set_parameter(...)`; `tests/bookings/helpers.py` — `next_hour`,
  `insert_booking`. The parameter cache is invalidated before every test.
- The rate limiter is reset before every test; its buckets are in-process
  and every test client shares one IP.
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

- **A migration never imports a constant that may change.** 0002 and 0006
  carry frozen copies of what they seeded; when 0007 deleted the role
  constants, an import would have broken every fresh `alembic upgrade`.

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
uv run python -m src.users.cli create-super-admin --email … --first-name … --last-name …
uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest && uv run vulture
```

`uv run vulture` finds dead code; names only the framework uses (routes,
validators, Pydantic and ORM fields) live in `vulture_whitelist.py`. Test-only
helpers belong in `tests/`, not `src/`.

CI runs lint, format check, mypy, `alembic upgrade head` against a real
Postgres service, and the test suite. All five must pass.
