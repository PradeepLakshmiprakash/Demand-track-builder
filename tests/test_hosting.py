"""Hosted demo (Vercel): the shared demo password, the cron endpoint, and hosted Postgres URLs."""

import base64
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings


def _basic(password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"anyone:{password}".encode()).decode()}


@pytest.fixture
def settings() -> Iterator[Settings]:
    s = get_settings()
    before = (s.demo_password, s.cron_secret)
    yield s
    s.demo_password, s.cron_secret = before


def test_demo_password_guards_every_page(settings: Settings) -> None:
    from app.main import create_app

    settings.demo_password = "open-sesame-test"
    c = TestClient(create_app(), follow_redirects=False)
    r = c.get("/demands")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert c.get("/demands", headers=_basic("wrong")).status_code == 401
    assert c.get("/view-as/1", headers=_basic("open-sesame-test")).status_code == 303
    assert c.get("/health").status_code == 200  # the platform's health check stays open


def test_cron_needs_the_secret_and_runs_the_jobs(settings: Settings) -> None:
    from app.main import create_app

    settings.cron_secret = "cron-test-secret"
    c = TestClient(create_app())
    assert c.get("/api/cron/daily").status_code == 401
    assert c.get("/api/cron/daily", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.get("/api/cron/daily", headers={"Authorization": "Bearer cron-test-secret"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_cron_is_off_without_a_secret(settings: Settings) -> None:
    from app.main import create_app

    settings.cron_secret = None
    assert (
        TestClient(create_app()).get("/api/cron/daily", headers={"Authorization": "Bearer "}).status_code
        == 401
    )


@pytest.mark.parametrize("url", ["postgres://u:p@host/db?sslmode=require", "postgresql://u:p@host/db"])
def test_hosted_postgres_urls_get_the_driver(url: str) -> None:
    assert Settings(database_url=url).database_url.startswith("postgresql+psycopg://u:p@host/db")


def test_demo_requires_a_password() -> None:
    with pytest.raises(ValueError, match="DEMO_PASSWORD"):
        Settings(env="demo", demo_password=None)


def test_all_mail_can_go_to_one_inbox() -> None:
    from app.core import mail

    s = get_settings()
    before = s.mail_redirect_to
    s.mail_redirect_to = "inbox@example.com"
    try:
        msg = mail._build(mail.Mail(to=["a@example.com"], cc=["b@example.com"], subject="Hi", text="Body"))
    finally:
        s.mail_redirect_to = before
    assert msg["To"] == "inbox@example.com" and msg["Cc"] is None
    assert "[Meant for To: a@example.com | Cc: b@example.com]" in msg.get_content()
