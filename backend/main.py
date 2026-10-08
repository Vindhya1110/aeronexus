"""ASGI entry point. Run from the repo root:  uvicorn backend.main:app --reload"""
from .app import create_app

app = create_app()

