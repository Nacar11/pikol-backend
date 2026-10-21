from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from src.auth.controllers.auth import router as auth_router
from src.auth.rate_limit import handle_rate_limited, limiter
from src.bookings.controllers.admin import router as admin_bookings_router
from src.bookings.controllers.availability import router as availability_router
from src.bookings.controllers.player import router as bookings_router
from src.bookings.http_errors import register_booking_errors
from src.config.settings import Settings, get_settings
from src.courts.controllers.admin import router as admin_courts_router
from src.health.controllers.health import router as health_router
from src.pricing.controllers.admin import router as admin_pricing_router
from src.users.controllers.admin import router as admin_users_router
from src.users.controllers.me import router as me_router
from src.utils.errors import register_exception_handlers
from src.utils.log import configure_logging
from src.utils.request_id import RequestIDMiddleware
from src.venues.controllers.admin import router as admin_venues_router
from src.venues.controllers.catalog import router as catalog_router

# Configured once at import, NOT inside create_app. configure_logging
# replaces the root handler list, and the `client` fixture builds an app per
# test — reconfiguring mid-session destroys pytest's caplog handler and drops
# any handler an operator attached.
configure_logging(get_settings().debug)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    # OpenAPI lists every admin route and its request shape, so it is off
    # outside development — the same call asima makes.
    app = FastAPI(
        title="Pikol API",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    # add_middleware prepends, so CORS ends up OUTERMOST — which is what a
    # preflight request needs.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    register_booking_errors(app)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, handle_rate_limited)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(admin_users_router)
    app.include_router(catalog_router)
    app.include_router(admin_venues_router)
    app.include_router(admin_courts_router)
    app.include_router(admin_pricing_router)
    app.include_router(availability_router)
    app.include_router(bookings_router)
    app.include_router(admin_bookings_router)
    return app


app = create_app()
