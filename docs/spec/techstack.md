# Demand Tracker — Tech Stack

Version 1.0 · 23 Sep 2026

## 1. Stack summary

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Team standard; one language across API, jobs and Excel parsing |
| Web framework | FastAPI | Async, typed routes, automatic OpenAPI docs; one router file per screen |
| UI | Jinja2 templates served by FastAPI (HTMX optional for partial updates) | Keeps the whole app in Python; routers also expose JSON so a React UI can be added later |
| Validation | Pydantic v2 | Request/response schemas, settings |
| Database | PostgreSQL 16 | Source of truth for app refs, GTD requisition IDs and their links; JSONB for per-account custom fields and raw sheet rows |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Typed models, versioned schema changes |
| DB driver | psycopg 3 | Current Postgres driver |
| Excel parsing | openpyxl + pandas | Reads the DP coverage sheet |
| Fuzzy matching | rapidfuzz | Name similarity for tier-3 reconciliation |
| Scheduling | APScheduler (in-process) | Daily admin mail, escalation sweep |
| Email | SMTP (v1) → AWS SES | Admin mail, interview alerts, escalation notices |
| File storage | Local disk (v1) → AWS S3 | JDs, CVs, uploaded DP sheets |
| Auth | Deferred. Dev-only "View as" switcher now → SSO via OIDC (Azure AD) later | Access rules come from the User access page, not from login |
| Testing | pytest, httpx TestClient, factory fixtures | |
| Code quality | ruff (lint + format), mypy | |
| Packaging | Docker, docker-compose for local (app + Postgres) | |
| Source control | Git, feature branches, PR review | |
| CI | GitHub Actions (or equivalent) | Lint, type check, tests, migrations check |
| Hosting | AWS App Runner or ECS Fargate + RDS Postgres + S3 + SES | |

## 2. Project structure

Convention: **every screen has its own router file, template folder and service file.** Routers stay thin; logic lives in services so scheduled jobs reuse it.

```
demand-tracker/
├── app/
│   ├── main.py                       # app factory, router mounting, scheduler start
│   ├── core/
│   │   ├── config.py                 # env settings (Pydantic BaseSettings)
│   │   ├── db.py                     # engine, session dependency
│   │   ├── security.py               # login, role checks, account/BU scoping
│   │   └── mail.py                   # SMTP / SES sender
│   ├── models/                       # one SQLAlchemy model per table
│   ├── schemas/                      # Pydantic models per screen
│   ├── routers/                      # one file per screen
│   │   ├── view_switcher.py          # dev-only: "View as" persona, replaced by SSO later
│   │   ├── user_access.py            # admin: users, roles, BU/practice, visibility
│   │   ├── my_demands.py             # demand owner list
│   │   ├── raise_demand.py           # demand owner form
│   │   ├── gtd_queue.py              # admin: pending GTD entry, req ID capture
│   │   ├── excel_import.py           # admin: DP sheet upload
│   │   ├── reconciliation.py         # admin: match / create / missing
│   │   ├── approvals.py              # admin: offer margin approvals
│   │   ├── rate_card.py              # admin: vendor rates
│   │   ├── interviewer_profiles.py   # admin: interviewer skill tags
│   │   ├── escalations.py            # admin + leadership: list, resolve
│   │   ├── leadership_dashboard.py   # leadership overview
│   │   ├── my_interviews.py          # interviewer: list, feedback
│   │   └── account_settings.py       # per-account thresholds and mappings
│   ├── services/
│   │   ├── demand_service.py
│   │   ├── gtd_service.py
│   │   ├── excel_parser.py
│   │   ├── reconcile_service.py
│   │   ├── margin_service.py
│   │   ├── interview_service.py
│   │   ├── escalation_service.py
│   │   ├── loss_service.py
│   │   └── notify_service.py
│   ├── jobs/
│   │   └── scheduler.py              # daily_admin_mail, escalation_sweep
│   └── templates/
│       ├── base.html                 # sidebar, role-based nav
│       └── <screen>/…                # one folder per router
├── migrations/                       # Alembic
├── seed/                             # dummy accounts, users, demands, sample DP sheet
├── tests/
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## 3. Data model (PostgreSQL)

| Table | Key columns | Notes |
|---|---|---|
| `accounts` | id, name, grace_days, l1_sla_days, l2_sla_days, panel_timer_hours, aging_days, rejection_limit, margin_threshold, mail_time, config (jsonb) | All client-specific rules; status mapping and supply channels live in `config` |
| `business_units` | id, account_id, name | CARDS, BANKING, PAYMENTS, DATA |
| `users` | id, email, name, role, level, visibility_scope, active, created_by, updated_at | role: demand_owner, admin, leadership, interviewer. visibility_scope: own, own_bu_read, full, assigned_interviews. Admin and leadership forced to `full` |
| `user_accounts` | user_id, account_id | Account scoping |
| `user_business_units` | user_id, bu_id | One or more BUs per user |
| `user_practices` | user_id, practice | Optional; used for interviewers and practice-level filtering |
| `demands` | id, app_ref (unique, `DM-000142`), account_id, bu_id, owner_id, name, practice, grade, category, type, position_type, replaced_resource, skills (text[]), exp_min, exp_max, client_rate, start_date, region, location, work_mode, hiring_manager, jd_path, custom_fields (jsonb), status, created_at | |
| `notification_batches` | id, account_id, sent_at, demand_ids (int[]) | One row per daily admin mail |
| `gtd_submissions` | id, demand_id, gtd_req_id (unique), submitted_by, submitted_at, previous_submission_id | Resubmits chain to the previous ID |
| `excel_imports` | id, account_id, file_path, uploaded_by, imported_at, row_count | |
| `excel_rows` | id, import_id, gtd_req_id, status, status_group, source, candidate_name, doj, raw (jsonb), submission_id, match_tier | Snapshot per import |
| `stage_events` | id, demand_id, from_stage, to_stage, at, origin | origin: app or import |
| `candidates` | id, demand_id, name, channel, current_stage | One per person per requisition |
| `interviewer_profiles` | user_id, practices (text[]), skills (text[]), max_grade, active | |
| `interviews` | id, demand_id, candidate_id, round, interviewer_id, scheduled_at, feedback_token (unique), ratings (jsonb), outcome, comments, submitted_at | |
| `rate_cards` | id, account_id, grade, practice, region, channel, cost_rate, effective_from, effective_to | |
| `offer_approvals` | id, candidate_id, bill_rate, cost_rate, margin_pct, route (admin / leadership), approver_id, decision, decided_at | |
| `escalations` | id, demand_id, type, level, opened_at, due_at, reason, action, comment, resolved_by, resolved_at | |

Key constraints: `demands.app_ref` unique; `gtd_submissions.gtd_req_id` unique; `interviews.feedback_token` unique; indexes on `excel_rows.gtd_req_id`, `escalations(status, due_at)`, `stage_events(demand_id, at)`.

## 4. Background jobs

| Job | Schedule | Service |
|---|---|---|
| `daily_admin_mail` | Per account `mail_time` | notify_service → collects `submitted`, sends mail, writes batch |
| `reconcile` | After each import (triggered, not scheduled) | reconcile_service → match tiers, missing, dropped |
| `escalation_sweep` | Every 2 hours | escalation_service → open new, promote L1 → L2 past due |

## 5. Security and access

- A `current_user` dependency resolves the acting user. Until login exists it reads the dev-only "View as" selection (disabled in production by config); later it reads the SSO session. Nothing else changes when login arrives.
- Role-based dependency on every router (`require_role("admin")`) and a scope filter on every query, driven by `visibility_scope`, BUs and practices set on the User access page.
- Admin demand owner and leadership always get `full`; everyone else is limited. Demand owners see only their demands (optional read-only BU view); client bill rate hidden from demand owners and interviewers.
- Interview feedback links use single-use random tokens tied to one interview.
- Secrets via environment variables / AWS Secrets Manager; no credentials in the repo.
- Audit: stage events and escalation resolutions record who and when.

## 6. Environments and configuration

| Env | Database | Mail | Storage |
|---|---|---|---|
| Local | Postgres in docker-compose | Console / MailHog | Local disk |
| Test | RDS (small) | SES sandbox | S3 bucket |
| Prod | RDS Postgres, Multi-AZ | SES | S3 bucket |

Environment variables: `DATABASE_URL`, `SECRET_KEY`, `MAIL_BACKEND`, `SMTP_*` / `SES_REGION`, `STORAGE_BACKEND`, `S3_BUCKET`, `OIDC_*` (phase 6).

## 7. Conventions

- Branch per feature, PR review, CI must pass before merge.
- Alembic migration for every schema change; never edit the DB by hand.
- Services have unit tests; routers have API tests; reconciliation tested against the 09-Sep sample sheet.
- Dates stored in UTC; working-day calculations exclude weekends only (holiday calendar later).
- Seed data uses a single account (Discover NA) with its four BUs and sample users for every role. The schema stays multi-account.
