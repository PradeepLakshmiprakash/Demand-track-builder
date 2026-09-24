from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


def _at(value: datetime | None, tz: str | None = None, fmt: str = "%d %b %Y, %H:%M %Z") -> str:
    """A timestamp in the account's time zone, e.g. '24 Sep 2026, 09:00 CDT'."""
    if value is None:
        return "—"
    return value.astimezone(ZoneInfo(tz)).strftime(fmt) if tz else value.strftime(fmt)


def _money(value: object, cents: bool = False) -> str:
    """$12,345 (or $95.50 with cents); em dash when unknown."""
    if value is None:
        return "—"
    return f"${value:,.2f}" if cents else f"${value:,.0f}"


templates.env.filters["d"] = _fmt_date
templates.env.filters["money"] = _money
templates.env.filters["at"] = _at


def _account_tz(db: Session | None, actor: Actor | None) -> str | None:
    if db is None or actor is None:
        return None
    from app.models import Account

    account = db.get(Account, actor.account_id)
    return account.settings.timezone if account else None


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
    menu = menu_for(actor.role) if actor else []
    path = request.url.path
    # The deepest menu path that contains this page is the active one (/demands/new → Raise demand).
    hits = [n for n in menu if path == n.path or path.startswith(n.path + "/") or path == f"/soon/{n.key}"]
    ctx: dict[str, Any] = {
        "actor": actor,
        "menu": menu,
        "active_nav": max(hits, key=lambda n: len(n.path)).key if hits else None,
        "path": request.url.path,
        "switcher": switcher_options(db) if (db is not None and settings.view_switcher_enabled) else None,
        "tz": _account_tz(db, actor),
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        **context,
    }
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)
