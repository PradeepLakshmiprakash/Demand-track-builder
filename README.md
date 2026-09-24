# Demand Tracker

Tracks client positions (demands) from the moment a demand owner raises them until the candidate is
onboarded and billing starts: GTD submission, DP sheet reconciliation, escalations, offer approvals and
interviews. Built for Discover NA first; every client-specific rule is account configuration.

Specs: [`docs/spec/flow-artifact.md`](docs/spec/flow-artifact.md) (flows),
[`docs/spec/techstack.md`](docs/spec/techstack.md) (stack, data model),
[`docs/spec/plan.md`](docs/spec/plan.md) (phases). Answers to the open questions and design decisions:
[`docs/decisions.md`](docs/decisions.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Decisions and setup | Repo, CI; open questions answered 24 Sep (`docs/decisions.md`) |
| 1 | Foundation | **Done**: all tables + first migration, View-as switcher, User access, Account settings, demands list with scope filtering, seed |
| 2 | Intake and GTD submission | Next |

## Run locally (this machine)

```bat
scripts\db-start.cmd
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m seed
.venv\Scripts\python -m uvicorn app.main:app --port 8010 --reload
```

Open http://localhost:8010 and use **View as** in the sidebar to switch persona.

`.env` holds `DATABASE_URL` and `TEST_DATABASE_URL` (see `.env.example`). With Docker instead:
`docker compose up`, then `docker compose exec app python -m seed`.

## Checks

```bat
.venv\Scripts\ruff check . && .venv\Scripts\ruff format --check .
.venv\Scripts\mypy app seed tests
.venv\Scripts\python -m pytest
```

Tests drop and rebuild `TEST_DATABASE_URL` with Alembic, then reload the seed before every test.

## Layout

Every screen is a router file, a template folder and a service. Routers stay thin; logic lives in
services so scheduled jobs can reuse it.

```
app/core/        config, db, enums (the vocabulary), account_config (per-client rules),
                 security (Actor, current_user, role guards), nav (menu per role)
app/models/      SQLAlchemy models, one class per table
app/services/    demand_service (scope filter), user_service, account_service
app/routers/     view_switcher, my_demands, user_access, account_settings
app/templates/   base.html + one folder per screen
migrations/      Alembic
seed/            Discover NA dummy data (python -m seed)
tests/
```

## Seeded personas

| Persona | User | Sees |
|---|---|---|
| Demand owner | Priya N. (PAYMENTS) | Own demands |
| Demand owner | Rahul K. (CARDS) | Own + CARDS read-only |
| Demand owner | Neha T., Meera S., Arjun D. | Own demands |
| Admin demand owner | Kavya R. | Full account |
| Admin team | Farah Q., Deepak L. | Full account; GTD queue, DP sheet import, reconciliation |
| Leadership | Sanjay M. | Full account, read-only |
| Interviewer | Vikram P., Anita G. | Demands with interviews assigned to them |

All names are placeholders. Never commit a real DP sheet: it carries candidate personal data.
