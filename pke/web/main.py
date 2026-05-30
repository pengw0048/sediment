"""FastAPI app factory for Sediment."""

from __future__ import annotations

from pathlib import Path

from pke.app import App
from pke.config.settings import Settings


def create_app(*, settings: Settings | None = None):
    """Create the FastAPI web application."""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from pke.adapters.browser_ext_endpoint import router as evidence_router
    from pke.web.routes import api_intervention, api_review, api_settings, api_skills, pages

    app_state = App.create(settings=settings)
    web = FastAPI(title="Sediment PKE")
    static_dir = Path(__file__).parent / "static"
    web.mount("/static", StaticFiles(directory=static_dir), name="static")

    def store_getter() -> App:
        return app_state

    web.include_router(pages.router(store_getter))
    web.include_router(evidence_router(store_getter))
    web.include_router(api_review.router(store_getter))
    web.include_router(api_skills.router(store_getter))
    web.include_router(api_settings.router(store_getter))
    web.include_router(api_intervention.router(store_getter))
    return web
