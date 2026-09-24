"""python -m seed : wipe the database contents and load the Discover NA dummy data."""

import sys

from app.core.config import get_settings
from app.core.db import new_session
from seed.load import load


def main() -> None:
    if get_settings().env == "production":
        sys.exit("Refusing to seed a production database.")
    with new_session() as db:
        load(db)
    print("Seeded Discover NA: 4 BUs, 9 users, 18 demands, 6 open escalations, 2 interviews.")


if __name__ == "__main__":
    main()
