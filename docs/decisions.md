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

**D12. What the admin team can do** (confirmed 24 Sep). Full-account visibility, locked like the admin
demand owner's. Menu: All demands, GTD queue, DP sheet import, Reconciliation, Escalations (they handle
escalations too). Kept with the admin demand owner only: User access, Account settings, Offer
approvals, Rate card, Interviewer profiles, Raise demand. The admin team doesn't see client bill rates. On the demands list
they're read-only; recording requisition IDs happens on the GTD queue (Phase 2), which they can use.

**D13. BU rules by role.** Demand owner: exactly one BU (service rule; the form's BU chips act as a
single choice). Interviewer: one or more (used for routing). Admin demand owner, admin team, leadership:
every BU, set automatically.

## Decisions made while building Phase 2

**D14. Edit window.** A demand can be edited by its owner (or the admin demand owner) while it's a
draft or submitted. Once it's in an admin mail (`notified`) it's locked; changes after that go through
the admin.

**D15. Drafts may be incomplete.** `practice` and `grade` are nullable so a draft can be saved early;
a CHECK constraint (`submitted_is_complete`) requires both for anything past draft, and submitting
checks every required field (`DemandForm.missing_for_submit`).

**D16. Client bill rate is write-only for demand owners.** They can enter it on the form but never see
it again; leaving it blank on edit keeps the rate on file. The admin demand owner and leadership see it.
It's included in the GTD entry download for the admin roles.

**D17. The daily admin mail** goes to active admin demand owners and admin team members. A job checks
every minute and sends once the account's local mail time has passed and no mail went out that day,
so a changed mail time takes effect without a restart. It lists new submissions (which become
`notified`) and reminds about earlier ones still without an ID. If sending fails, nothing changes and
the next run retries. "Send admin mail now" on the GTD queue sends one immediately. Locally mails are
written as `.eml` files under `var/mail/`.

**D18. Requisition IDs** are stored uppercase with spaces removed and must be 4 to 12 letters or digits
(the 09-Sep sheet uses 6). Please confirm against real GTD IDs. Submitted demands can be linked
before their mail goes out.

**D19. Not built yet from techstack.md:** per-account custom fields on the demand form (the
`custom_fields` column exists). Planned with the Phase 8 second-account work unless Discover needs one
sooner.

## Phase 3 answers (24 Sep 2026): all recommendations accepted

| # | Question | Answer |
|---|---|---|
| 1 | Read the real 09-Sep sheet? | Headers and status values only; no names or candidate data copied. It has one sheet, a header row and 30 rows. |
| 2 | One row per requisition or per candidate? | One per requisition (30 rows, 30 distinct IDs), so each row sets its demand's stage. |
| 3 | Is every upload the full sheet? | Yes. That's what makes "dropped" reliable. |
| 4 | When does the grace period start? | From the day the requisition ID is linked, counted in working days to the sheet's date. |
| 5 | Finished demands leaving the sheet | No escalation for staffed, cancelled or closed demands; only open ones are "dropped". |
| 6 | Name-prefix match | Linked automatically and listed in the import summary. Fuzzy matches always wait for a person. |
| 7 | Owner of a demand created from a sheet row | The sheet's originator if their name matches an active demand owner or admin demand owner; otherwise the admin picks. |
| + | Older sheet uploaded after a newer one | Refused unless the uploader ticks "import it anyway". |

## Decisions made while building Phase 3

**D20. The DP sheet format is account configuration.** Account settings → DP sheet columns maps
each field to its header text (matched ignoring case and spacing) and lists the values that mean
empty (`0` and `-` in the Discover sheet). The parser finds the header row within the first 10 rows,
refuses a sheet missing a required column (naming it), and warns about missing optional ones.

**D21. Every row is a snapshot.** Rows keep the matching fields as columns and the full original row
as JSON (`raw`), including the candidate details, which Phase 6 turns into candidate records. The
uploaded file is kept too. The same file (by SHA-256) can't be imported twice; use "Re-run
reconciliation" instead.

**D22. Only the latest import is reconciled or acted on.** Older imports are shown read-only.
Re-running is idempotent: unchanged stages and already-open escalations are no-ops, and rows a person
matched stay matched.

**D23. Fuzzy score** (0-100, suggestions from 60): name similarity 35%, originator vs owner 20%,
practice 15, grade 10, region 5, start date within ±7 days up to 15. Up to three suggestions per row;
identical bulk demands are offered as interchangeable slots in ref order. Matching a row to a demand
that already has a different ID records the new ID and chains it to the old one (a mistyped ID).

**D24. Reconciliation opens escalations now** (missing, dropped, incorrect) through
`escalation_service.open_escalation`: L1, due after the account's L1 working days, at most one open per
demand and type. Phase 4 adds the sweep, L1 → L2 promotion and resolving.

**D25. A status with no mapping** leaves the demand "Linked" and shows a warning with a link to the
status mapping settings.

**D26. Sample data.** `seed/sample_sheet.py` builds a made-up sheet in the real one's shape
(`python -m seed.sample_sheet` writes `seed/sample_dp_sheet.xlsx`). It's the Phase 3 exit fixture:
the real 09-Sep sheet is never committed.

## Decisions made while building Phase 4

**D27. The LOB delivery head is per business unit, not a user.** Account settings → Business units
holds each BU's delivery head name and email. L1 escalation mails go to them (demand owner, admin
demand owner and admin team copied); a BU without one falls back to the admin demand owner. L2 mails go
to leadership users (delivery head and demand owner copied). Confirmed 24 Sep.

**D28. Trigger details** (thresholds from Account settings):
- *Not submitted:* in an admin mail, still no requisition ID at the next working day's mail time.
- *Aging:* a linked demand (coverage required through offer in market) with no stage change for
  `aging_days` calendar days.
- *Past start date:* start date has passed, the demand is open and past draft, and the latest DP sheet
  shows no DOJ or a DOJ after the start date.
- *Missing, dropped, incorrect:* opened by reconciliation (Phase 3).
- *Rejection limit, panel SLA:* wait for interview records (Phase 6).

**D29. The sweep** runs every two hours (and from "Run sweep now" for the admin roles): open new
escalations, promote L1 past its due date to L2 (new due date after the L2 working days; L2 past due
stays L2), then mail. Mailing is tracked separately (`notified_level`), one mail per recipient group
per sweep, so escalations opened by reconciliation get mailed and a failed mail is retried.

**D30. Who resolves:** L1 by the admin demand owner or admin team; L2 by leadership or the admin
demand owner (confirmed 24 Sep, with D27 and the "no further action" action).
Every resolution needs a reason from the account's list and an action:
- *Resubmit* (link problems only): the demand goes back to Submitted, into the next admin mail, and
  the new GTD ID chains to the old one. The demand's other open link escalations close with it.
- *Extend due date:* stays open, back at L1 with the new date.
- *Close demand:* the demand is closed, and all its open escalations close with it.
- *No further action:* **added**, only allowed once the condition has cleared (e.g. the ID was linked a
  day late). The screen says when a condition has cleared, but a person still closes it.

**D31. Audit.** `escalation_events` records opened, notified, promoted, extended and resolved, with
who and when. The escalation page shows it; demand owners see their demand's escalations on the demand
page.

**D32. Dropped is judged on the current requisition.** After a resubmit, the old ID leaving the DP
sheet is expected and doesn't flag the demand as dropped.

## Decisions made while building Phase 5

**D33. Rate card is dated history.** Cost per hour by grade, practice (blank = any, a practice-specific
rate wins), region and supply channel. A rate is never edited: adding one for the same key closes the
open-ended previous row the day before; an overlap with a bounded row is refused. The seed carries a
made-up card (64 rows) so offers can be priced; replace it with Discover's real vendor rates.

**D34. Offers come from the DP sheet.** When a demand reaches Offer in process and its row names a
candidate, reconciliation creates the candidate and an offer approval (once per candidate). The supply
channel is read from the sheet's Source cell against the supply channels in settings. The admin demand
owner can raise one by hand when the sheet has no name, and set a missing channel.

**D35. Pricing.** Margin = (client bill rate − cost rate) ÷ client bill rate, using the rate in force on
the offer date (kept on the approval, so later rate changes don't move old offers). At or above the
account's cut-off → the admin demand owner decides; below → leadership decides at their discretion.
An offer missing its bill rate, channel or rate card entry waits unpriced with the reason and is
re-priced when the screen loads or the rate card changes. A decline needs a comment; so does a
below-cut-off approval (the exception's reason). Decisions record approver, margin and time. The
approval doesn't move the demand's stage; the DP sheet still does.

**D36. Revenue loss** (confirmed 24 Sep: keep the account setting): days late = (DOJ or today) − start date, counted to today at
most; revenue lost to date = hourly bill rate × billable hours per day (Account settings, default 8) ×
**working** days late. The spec says "daily bill rate × days late"; working days avoid charging
weekends. A future DOJ adds a separate "more by the DOJs set" projection. Staffed demands that joined
late keep their loss; cancelled, closed and draft demands don't count. A late demand with no bill rate
is counted but its loss is shown as unknown, never guessed.

**D37. Leadership overview.** Open demands, still-need-coverage, past start unfilled (with the range
of days late) and revenue lost to date; open escalations and offers waiting for leadership; pipeline by
stage (single-hue bars, the two problem stages in a warning tone, counts labelled); revenue at risk
list; breakdowns by practice and by BU. Leadership and the admin demand owner (confirmed 24 Sep); the
admin demand owner still lands on All demands.
