import base64
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, new_session
from app.core.nav import BY_KEY, home_for
from app.core.security import VIEW_AS_COOKIE, Actor, current_user
from app.core.templating import render
from app.routers import (
    account_settings,
    accounts,
    approvals,
    candidates,
    cron,
    escalations,
    excel_import,
    feedback_link,
    gtd_queue,
    integrations,
    interviewer_profiles,
    leadership_dashboard,
    my_demands,
    my_interviews,
    raise_demand,
    rate_card,
    reconciliation,
    user_access,
    view_switcher,
)

# raise_demand before my_demands so /demands/new isn't read as a demand ref.
ROUTERS = [
    raise_demand.router,
    my_demands.router,
    gtd_queue.router,
    excel_import.router,
    reconciliation.router,
    escalations.router,
    approvals.router,
    rate_card.router,
    leadership_dashboard.router,
    candidates.router,
    my_interviews.router,
    interviewer_profiles.router,
    feedback_link.router,
    integrations.router,
    user_access.router,
    account_settings.router,
    accounts.router,
    cron.router,
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    scheduler = None
    if get_settings().scheduler_enabled:
        from app.jobs.scheduler import start

        scheduler = start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


OPEN_PATHS = ("/health", "/api/cron/")


def _demo_gate(app: FastAPI, password: str) -> None:
    """Hosted demo: one shared password (HTTP Basic, any user name) in front of every page."""

    @app.middleware("http")
    async def gate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.startswith(OPEN_PATHS):
            return await call_next(request)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        given = ""
        if scheme.lower() == "basic":
            try:
                given = base64.b64decode(value).decode("utf-8").partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                given = ""
        if not secrets.compare_digest(given.encode(), password.encode()):
            return Response(
                "Demand Tracker demo: enter the demo password (any user name).",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Demand Tracker demo", charset="UTF-8"'},
            )
        return await call_next(request)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Demand Tracker", version="0.2.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    if settings.demo_password:
        _demo_gate(app, settings.demo_password)

    if settings.view_switcher_enabled:
        app.include_router(view_switcher.router)
    for r in ROUTERS:
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    def home(request: Request, actor: Actor = Depends(current_user)) -> RedirectResponse:
        msg = request.query_params.get("msg")
        return RedirectResponse(home_for(actor.role) + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    @app.get("/soon/{key}", response_class=HTMLResponse, include_in_schema=False)
    def coming_soon(
        key: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
    ) -> HTMLResponse:
        item = BY_KEY.get(key)
        if item is None or actor.role not in item.labels:
            raise HTTPException(404)
        return render(request, "soon.html", actor, db, item=item, label=item.label_for(actor.role))

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/") or exc.status_code not in (401, 403, 404):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        # Pages: keep the View-as switcher reachable so a blocked persona can switch away.
        db = new_session()
        try:
            resp = render(
                request,
                "error.html",
                None,
                db,
                status_code=exc.status_code,
                status=exc.status_code,
                detail=exc.detail,
            )
        finally:
            db.close()
        if exc.status_code == 401:
            resp.delete_cookie(VIEW_AS_COOKIE)
        return resp

    return app


app = create_app()
