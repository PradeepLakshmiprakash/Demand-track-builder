"""Vercel entry point: the whole FastAPI app as one Python function (Vercel detects the FastAPI `app` here and sends every path to it)."""

from app.main import create_app

app = create_app()
