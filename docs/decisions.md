# Decisions


## Answers to the open questions (`spec/flow-artifact.md` §12), 24 Sep 2026

| # | Question | Answer | Where it lives | Built |
|---|---|---|---|---|
| 1 | Is the admin demand owner the person who submits on GTD? | The admin demand owner **and their admin team**. The team is separate from ordinary demand owners; its job is all the manual admin work. | Role `admin_team` (migration 0002) | Role: Phase 1. GTD queue: Phase 2 |
| 2 | "Not submitted" escalation trigger and cutoff | As assumed: in the daily mail, no requisition ID by the next day's mail time, so 1 working day | `accounts.mail_time`, `escalation_service` | Phase 4 |
| 3 | How does the DP sheet arrive? | Manual upload, by the admin demand owner or their admin team | `excel_import` guarded for `admin`, `admin_team` | Phase 3 |
| 4 | Grace period before a demand is flagged missing | Set per account on the Account settings page (default 3 working days) | `accounts.grace_days` | Phase 1 ✔ |
| 5 | Offers below 30% margin | Leadership decides: approve or reject at their discretion. At or above 30%: admin demand owner. | `accounts.margin_threshold`, `margin_service` | Phase 5 |
| 6 | Who schedules the L2 interview? | The **panelist asks** for an L2 round and the **demand owner approves** it | `interview_service`, see D11 | Phase 6 |
| 7 | Can a demand owner belong to more than one BU? | **No.** A demand owner has exactly one BU. Only the admin demand owner spans BUs (with the admin team and leadership, who see the full account). | `user_service`, User access form | Phase 1 ✔ |

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

## Decisions from the answers

**D11. L2 rounds are requested, then approved (Phase 6).** An interviewer (panelist) raises an L2
request from their feedback; the demand's owner approves or declines it; only an approved request
becomes an interview record with its invite and feedback link. Pending requests count toward the panel
SLA timer so a request waiting on the owner can still escalate.

**D12. What the admin team can do (to confirm).** Full-account visibility, locked like the admin
demand owner's. Menu: All demands, GTD queue, DP sheet import, Reconciliation. Kept with the admin
demand owner only: User access, Account settings, Escalations, Offer approvals, Rate card,
Interviewer profiles, Raise demand. The admin team doesn't see client bill rates. On the demands list
they're read-only; recording requisition IDs happens on the GTD queue (Phase 2), which they can use.

**D13. BU rules by role.** Demand owner: exactly one BU (service rule; the form's BU chips act as a
single choice). Interviewer: one or more (used for routing). Admin demand owner, admin team, leadership:
every BU, set automatically.
