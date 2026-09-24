# Decisions

Phase 0 asks for the open questions in `spec/flow-artifact.md` §12 to be answered before the build.
They weren't answered yet, so the build proceeds on the working assumptions below. Each is isolated
in account settings or a single service, so changing it later is cheap. **Status: to confirm.**

| # | Question | Working assumption | Where it lives | Needed by |
|---|---|---|---|---|
| 1 | Who schedules L2 interviews today? | The admin demand owner creates interview records | `interview_service` (Phase 6) | Phase 6 |
| 2 | Offers below 30% margin: leadership exception or blocked? | Routed to leadership as an exception | `accounts.margin_threshold`, `margin_service` | Phase 5 |
| 3 | Is the admin demand owner the person who submits on GTD? | Yes: the same role records requisition IDs | Role `admin` | Phase 2 |
| 4 | How does the DP sheet arrive? | Manual upload in v1 | `excel_import` | Phase 3 |
| 5 | "Not submitted" escalation trigger and cutoff | In the daily mail, no requisition ID by the next day's mail time → escalation | `accounts.mail_time`, `escalation_service` | Phase 4 |
| 6 | Grace period before a demand is flagged missing | 3 working days | `accounts.grace_days` (Account settings) | Phase 3 |
| 7 | Can a demand owner belong to more than one BU? | Yes, one or more (`user_business_units`) | User access page | Phase 1 ✔ |

## Decisions made while building Phase 1

**D1. Access rule is enforced three times.** The allowed visibility per role
(`app/core/enums.py: ALLOWED_SCOPES`) drives the User access form, `user_service` coerces any other
request, and a CHECK constraint on `users` (`scope_matches_role`) rejects it at the database. A demand
owner can't be given "Full account": the spec only allows widening to own + BU read-only. The
prototype showed a "Full account" option for demand owners; the spec won.

**D2. One scope filter.** Every demand query goes through `demand_service.visible_demands(actor)`.
Visibility is wider than edit rights: BU read-only viewers and leadership see demands they can't
change (`can_edit`).

**D3. Menus and route guards come from one registry** (`app/core/nav.py`). `require_screen(key)`
guards a route with exactly the roles that have that screen in their menu. Screens from later phases
appear in the menu with a phase tag and a placeholder page, so each persona's navigation can be
checked now.

**D4. Postgres owns app refs.** `demands.app_ref` defaults from the `demand_ref_seq` sequence
(`DM-000142`); Python never generates one. The seed continues the sequence after its highest ref.

**D5. Thresholds are columns, lists and mappings are JSONB.** Numeric thresholds are real columns on
`accounts` because the escalation sweep filters on them in SQL. BU-independent lists (practices,
grades, regions, supply channels, status mapping, escalation owners, reasons) live in `accounts.config`,
validated by the Pydantic model `AccountConfig`. Status mapping can only target the stages a DP sheet
may set (`SHEET_STAGES`).

**D6. Primary and secondary skills are separate columns** (`demands.primary_skills`,
`secondary_skills`) instead of one `skills` array, because interviewer routing and bench matching
weigh them differently.

**D7. Escalations carry `status` and `detail`**, and a partial unique index allows at most one open
escalation per demand and type, so the escalation sweep (Phase 4) can run repeatedly without
duplicates. A CHECK constraint makes "resolved" impossible without a reason, an action and a time.

**D8. All timestamps are `timestamptz` (UTC).** "Today" for past-start checks is computed in the
account's time zone (Account settings).

**D9. Local database.** No Docker on the dev machine, so a private Postgres 17 cluster runs at
`..\demand-tracker-db` on port 5434 (`scripts\db-start.cmd`), separate from the Acquisition Central
cluster (5433) and the system PG17 service (5432). CI and docker-compose use Postgres 16 as specified.

**D10. No CSRF protection yet.** Forms are plain POSTs behind the dev-only switcher. CSRF tokens
arrive with login in Phase 7.
