from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config.settings import Settings, get_settings
from src.health.controllers.health import router as health_router
from src.utils.errors import register_exception_handlers
from src.utils.log import configure_logging
from src.utils.request_id import RequestIDMiddleware

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
    app.include_router(health_router)
    return app


app = create_app()
