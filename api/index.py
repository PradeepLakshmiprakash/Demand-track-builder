"""Vercel entry point: the whole FastAPI app as one Python function (see vercel.json)."""

from app.main import create_app

app = create_app()
