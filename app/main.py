"""FastAPI app factory. Run with: uv run uvicorn --factory app.main:create_app --reload"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from app.routes import health
from app.settings import Settings, get_settings
from core.db.session import make_engine

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and engine; otherwise both come from the environment."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        # Creating an engine doesn't connect, so the API starts even if Postgres is down; /ready reports it.
        app.state.engine = engine or make_engine(settings.database_url)
        log.info("Courier Ops Copilot starting (env=%s)", settings.app_env)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title="Courier Ops Copilot", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    return app
