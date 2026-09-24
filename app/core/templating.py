from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.nav import menu_for
from app.core.security import Actor

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _fmt_date(value: date | datetime | None, fmt: str = "%d %b %Y") -> str:
    return value.strftime(fmt) if value else "—"


templates.env.filters["d"] = _fmt_date


def render(
    request: Request,
    template: str,
    actor: Actor | None,
    db: Session | None = None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    """Render a screen inside base.html: sidebar for the actor's role, plus the View-as switcher."""
    from app.routers.view_switcher import switcher_options  # avoid import cycle

    settings = get_settings()
    ctx: dict[str, Any] = {
        "actor": actor,
        "menu": menu_for(actor.role) if actor else [],
        "path": request.url.path,
        "switcher": switcher_options(db) if (db is not None and settings.view_switcher_enabled) else None,
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        **context,
    }
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
