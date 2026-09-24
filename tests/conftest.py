"""Tests run against TEST_DATABASE_URL: the schema is dropped and rebuilt by Alembic once per run,
and the seed data is reloaded before every test."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
if not TEST_URL:
    raise RuntimeError("Set TEST_DATABASE_URL (see .env.example). Tests wipe that database.")
os.environ["DATABASE_URL"] = TEST_URL
os.environ["ENV"] = "test"
os.environ["VIEW_SWITCHER_ENABLED"] = "true"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["MAIL_BACKEND"] = "console"
os.environ["MAIL_DIR"] = "var/test-mail"
os.environ["STORAGE_DIR"] = "var/test-files"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import new_session  # noqa: E402
from app.core.security import VIEW_AS_COOKIE  # noqa: E402
from app.models import BusinessUnit, User  # noqa: E402
from seed.load import load  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def schema() -> None:
    """Migrations from scratch: drop everything, then `alembic upgrade head`."""
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = TEST_URL
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def seeded(schema: None) -> None:
    with new_session() as db:
        load(db)


@pytest.fixture
def db() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
    finally:
        s.close()


def user_id(email_prefix: str) -> int:
    with new_session() as s:
        uid = s.scalar(select(User.id).where(User.email.startswith(email_prefix + ".")))
    assert uid, email_prefix
    return uid


def bu_id(name: str) -> int:
    with new_session() as s:
        bid = s.scalar(select(BusinessUnit.id).where(BusinessUnit.name == name))
    assert bid, name
    return bid


class Client(TestClient):
    def as_user(self, email_prefix: str) -> "Client":
        """Act as a seeded user, e.g. client.as_user('priya')."""
        self.cookies.set(VIEW_AS_COOKIE, str(user_id(email_prefix)))
        return self


@pytest.fixture
def client() -> Iterator[Client]:
    from app.main import create_app

    with Client(create_app(), follow_redirects=False) as c:
        yield c
