"""ASGI entry point used by Uvicorn."""

from .app import create_app


app = create_app()
