"""FastAPI app factory. Run with: uv run uvicorn --factory app.main:create_app --reload"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from app.routes import actions, ask, auth, exceptions, health, hubs, metrics, sim
from app.settings import Settings, get_settings
from core.db.session import make_engine
from core.llm import LLM, get_llm

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, engine: Engine | None = None, llm: LLM | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings, engine and LLM; otherwise they come from the environment."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        # Creating an engine doesn't connect, so the API starts even if Postgres is down; /ready reports it.
        app.state.engine = engine or make_engine(settings.database_url)
        app.state.llm = llm or get_llm(settings)
        model = app.state.llm.name if app.state.llm else "none"
        log.info("Courier Ops Copilot starting (env=%s, llm=%s)", settings.app_env, model)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title="Courier Ops Copilot", version="0.1.0", lifespan=lifespan)
    for module in (health, auth, exceptions, actions, hubs, metrics, sim, ask):
        app.include_router(module.router)
    return app
