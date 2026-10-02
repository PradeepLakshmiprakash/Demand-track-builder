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

**D18. Requisition IDs** are stored uppercase with spaces removed and must be 4 to 12 letters or digits.
The 09-Sep sheet only has 6-character IDs, but the wider range stays so a future GTD format change
doesn't need a code change (confirmed 24 Sep). Submitted demands can be linked before their mail goes out.

**D19. No per-account custom fields for now** (confirmed 24 Sep): the demand form's fields are enough.
The `custom_fields` column stays for when a review comment asks for one.

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
made-up card (64 rows) so offers can be priced; replace it with Discover's real vendor rates. Rates go
in one at a time on the screen or in bulk from a .csv/.xlsx upload (all or nothing, every bad row
listed); the CSV download uses the same columns, so it's the template (confirmed 24 Sep: both).

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

## Answers before Phase 6 (24 Sep 2026)

| # | Question | Answer |
|---|---|---|
| 1 | Requisition ID format | Keep 4 to 12 letters or digits, for future formats (D18). |
| 2 | Per-account custom fields | Not needed; the fields are enough unless a review asks for more (D19). |
| 3 | Real vendor rates | Both: entered on screen, and bulk upload (built, D33). |
| 4 | Who schedules L1 | **Staffing**, outside the app, once they have coverage. There is still a gap between coverage and interviews being scheduled. For now the app needs a **placeholder to capture the panelist's recommendation on a candidate**; the open problem is **knowing which requisition ID a candidate being interviewed belongs to**. |
| 5 | Who books L2 | Also **staffing**. Who takes the L2 interview is a placeholder: not clear yet. (Earlier answer still holds: the panelist asks for L2, the demand owner approves.) |
| 6 | Karat | A **separate application** for conducting interviews; it needs to be **integrated**, not re-entered by hand. |
| 7 | Candidate CVs | Sent by **staffing by email** today. |
| 8 | Heads-up alerts: matching interviewer | **One matching technology** (at least one shared skill). Practice and grade don't have to match. |
| 9 | Rejection limit and panel SLA | Yes: count panel rejections recorded in the app; feedback due 48 hours after the scheduled interview time. |

### What this changes for Phase 6

The plan assumed the app schedules interviews and so always knows the requisition. It doesn't:
staffing schedules them, outside the app, and CVs arrive by email. So Phase 6 centres on
**linking a candidate to the right requisition** and **capturing the panelist's recommendation**,
rather than on scheduling:

- **Candidates come from the DP sheet** (Candidate Name and the multi-line candidate details on the
  requisition's row), so the app already knows which requisition each named candidate is on.
- **The panelist records feedback against a candidate**, found by name. The app suggests the
  requisition(s) that candidate is on; if a name appears on more than one, or on none, the panelist
  picks or the admin team maps it. This is the "fallback" in flow-artifact §7, now the main path.
- **Interview records are placeholders:** round (L1/L2), candidate, requisition, interviewer and time
  can be filled in by whoever knows them (admin team, or the panelist when giving feedback). Staffing
  scheduling stays outside the app.
- **L2:** the panelist can ask for one in their feedback; the demand owner approves; the interviewer
  stays open until someone is assigned.
- **CVs:** attached to the candidate by the admin team from staffing's email (upload). Reading them
  from a mailbox automatically is a later step.
- **Karat:** a placeholder for the integration. We need Karat's details (API or export, what it
  sends: candidate, requisition or job, result, report link) before building it.
- **Heads-up alerts** go to interviewers sharing at least one technology with the requisition.
- **Rejection limit** counts panel rejections recorded in the app; **panel SLA** is 48 hours from the
  scheduled time when one is known.

## Decisions made while building Phase 6

**D38. Candidates are mapped, not assumed.** A candidate belongs to at most one requisition
(`candidates.demand_id`, empty until known; the same name can be a separate record on another
requisition). They come from the DP sheet's Candidate Name cell (one per line) on the requisition's
row, from a panelist or the admin team adding them, or from Karat. Unmapped candidates wait on
Candidates → "No requisition yet"; mapping moves their interviews too, and mapping onto a requisition
that already has that person merges the two records.

**D39. Interviews are records that can start incomplete.** Round (L1/L2), requisition, interviewer and
time can each be unknown. Statuses: waiting for demand owner (an asked-for L2), extra round declined,
to be scheduled (approved or recorded, nobody assigned yet), scheduled (interviewer assigned; invite
and feedback link sent), feedback in. Staffing's scheduling stays outside the app; the admin team
records what's known on Candidates. Who takes an L2 stays a placeholder until that's decided.

**D40. The panelist's recommendation** is ratings 1 to 5 on the account's dimensions (Account config
`interview_ratings`, default Technical depth, Problem solving, Communication), select / reject / hold,
comments (required to reject or hold), and optionally "needs another round" with a reason. It can be
given on an assigned interview, from the mail link, or for any candidate found by name on My
interviews (the panelist can confirm the requisition while doing it).

**D41. Extra rounds.** "Needs another round" creates an L2 request and mails the demand owner. The
demand owner (or the admin demand owner) approves or declines it on the demand page; declining needs a
note. Rejected candidates can't be sent on; L2 is the last round.

**D42. Feedback link.** Issued when an interviewer is assigned; in the invite with subject
`[GTD ID | DM ref] L2 – Candidate`, the JD link (sign-in) and the CV link. Works without signing in,
single use (cleared on submit; a reassignment issues a new one). The CV link works while the feedback
link is live.

**D43. CVs** are uploaded on Candidates by the admin roles from staffing's email. Reading them from a
mailbox automatically is a later step.

**D44. Heads-up alerts** go once per requisition, when it's first seen in a DP sheet at an interview
stage, to active interviewers sharing at least one technology (primary or secondary skills vs
interviewer skills, case-insensitive). Interviewer profiles shows each interviewer's matching
requisitions. A mail failure never fails an import.

**D45. Escalations switched on:** rejection limit = panel rejections recorded on the requisition reach
the account's limit; panel SLA = a scheduled interview with a known time has no feedback after the
account's panel hours (clears when feedback is in).

**D46. Karat is an integration point, not built out.** `POST /api/integrations/karat/results`
(X-Api-Key, off unless `KARAT_API_KEY` is set) accepts {external_ref, candidate_name, gtd_req_id?,
round, outcome, report_url?, comments?}, maps by GTD ID when given, otherwise leaves the candidate
unmapped, and is idempotent on `external_ref`. We need Karat's real API/export details to finish it.

**D47. An incorrect demand goes back to its owner.** Missing, dropped and incorrect escalations can be
resolved with *send back to demand owner for correction*: the demand becomes **Returned for
correction**, the owner gets a mail with the reason and what to fix, sees the same on the demand page,
edits it and resubmits. It then goes into the admin mail as a resubmission and its new GTD ID chains to
the old one. *Resubmit* stays for a demand that is right as it is (GTD lost it).

**D48. Old requisition IDs don't drive a demand.** Once a demand has a newer ID, sheet rows for its
old ID are marked *superseded* and ignored. While it is being corrected or resubmitted (returned,
submitted or notified after its current ID was linked), rows for that ID are ignored too, so the
next sheet doesn't reopen the escalation that was just settled or report the demand as dropped.

**D49. Panel feedback moves the stage the day it's recorded.** Two app-driven stages sit between
Coverage required and Profiles with client: **Interviewing** (a candidate is in the panel) and
**Selected by panel**. Each demand has *client interview required* (set when it's raised, default
yes): if yes, a panel select moves it to Selected by panel; if no, the panel's decision is final and
it moves to Offer in process and raises the offer approval. All candidates rejected with nothing
pending → back to Coverage required. The demand owner gets a mail when a candidate is selected.

**D50. Sheet vs panel.** The DP sheet lags the panel by about a week. A sheet that still says
Linked / Coverage required doesn't pull back a demand the panel has moved (the reconciliation page
lists these as "sheet behind the panel"); a sheet stage that is further on always wins. Interview
activity counts as movement for the aging escalation.

**D51. The demand owner follows the offer without seeing the numbers.** The demand page has an
*Offer approval* panel per candidate: waiting for approval (with the admin demand owner, or with
leadership), being priced, approved or declined (by whom, when; a decline shows its reason). The
demands list shows the same in one line. The owner is mailed when an offer is approved or declined.
Rates and margin appear only for the admin demand owner and leadership.

**D52. Date of joining and candidate stage come from the DP sheet.** The demand page shows the DOJ
from the latest sheet and how many days it is after the requested start. Candidates named on the
row take its stage (Profiles with client, Offer in market, Staffed); an offer's own stage (Offer
approved / declined) is kept when the next sheet still says Offer in process.

## Phase 8: second account (24 Sep)

**D53. A role belongs to a person's membership of an account.** Role, visibility scope, level and
active move from `users` to `user_accounts` (migration 0009). One person, one login, several accounts,
a different role in each; the sidebar has an account switcher for people in more than one. Adding an
existing person's email on User access gives them a membership in this account instead of an error.
Deactivating on User access removes access to that account only. Each account shows only its own
business units and practices for a person.

**D54. A platform admin creates and deactivates accounts.** A flag on the person, separate from any
role inside an account (seeded: Kavya). The Accounts screen creates an account from blank settings or a
copy of another account's settings (lists, channels, DP sheet columns and status mapping, thresholds;
never business units, people, rates or demands) and names its first admin demand owner, who sets up
the rest in Account settings. Deactivating keeps all data; people lose access and the jobs skip it.

**D55. The second account is a made-up client, Acme Insurance,** configured unlike Discover in every
setting (time zone, BUs, practices, grade names, channels, DP sheet headers, column order and blanks,
statuses, ratings, thresholds, margin cut-off). Phase 8's exit test creates a third account and sets it
up through the screens only, then takes a demand from raise to an approved offer. Requisition IDs stay
unique across all accounts (GTD is one system); demand refs share one sequence.

**D56. The last two client-specific settings moved to Account settings:** the interview rating
dimensions and the L1/L2 escalation owner labels.

**D57. An interviewer belongs to exactly one account** (user, 24 Sep). Someone who works in another
account can't be given the interviewer role, and an interviewer can't be added to another account in
any role, including as a new account's first admin. Other roles may span accounts. Acme has its own
interviewer (Nadia K.); Vikram is Discover's only.

## Review notes of 1 Oct (handwritten pages) and the escalation redesign

**D58. Roles.** *Administrator* is a new role that runs the app's controls and nothing else: User
access, Account settings, Rate card and the platform Accounts screen. They see no demands. The former
"admin demand owner" is the **GTD team admin** (heads the GTD admin team) and the "admin team" is the
**GTD admin team**. The GTD team admin doesn't edit the controls: they raise a **request to the
Administrator** in the app, which mails the Administrators; the Administrator makes the change and
marks it done or declined, which mails the requester. An account always keeps one active
Administrator and one GTD team admin. Whoever creates an account becomes its Administrator.

**D59. Replacement demands record the leaver's last working day (LWD)**, required to submit. Revenue
loss (and the past-start escalation) counts from the requested start date, or from the day after the
LWD when that is later, because the leaver bills until they go. The demand page shows the uncovered
days between the LWD and the requested start.

**D60. The name sent to GTD is the plain demand name.** The GTD admin team doesn't type the DM
reference on GTD; the demand is tied to GTD by the requisition ID they link back. (A sheet row that
happens to carry a `[DM-…]` prefix is still matched by it.) Owners are told to type a plain role title.

**D61. Mail flow for a new demand:** an email the moment it is submitted (to the GTD admin team, demand
owner copied), a reminder every morning until it is created on GTD (the daily mail, owners copied on
their own demands), and a completion email when the requisition ID is linked (to the owner, team
copied). The all-demands list shows "GTD created: Yes/No".

**D62. Escalations: the responsible person acts; everyone else is informed.** Each trigger has a rule
in Account settings (edited by the Administrator): on or off, who is responsible (GTD admin team,
demand owner or interviewer), a severity and the steps the email spells out. Defaults: not submitted
and missing → GTD admin team; dropped, incorrect, aging, past start, rejection limit → demand owner;
late feedback → the interviewer. Anything the GTD team has to do is theirs to solve, with the demand
owner copied. The GTD team admin is notified on everything and responds only to the GTD team's own.

**D63. Severity and L1/L2.** High, medium or low gives 1, 2 or 3 working days to respond (editable). A
billable demand past its start is always high. L1 = the responsible person has been mailed and is in
time. L2 = the due date passed: leadership and the BU delivery head are informed (each can be switched
off), the same person still has to act and is reminded once a day. Leadership never acts. Asking for
more time keeps the level. Late feedback closes itself when the feedback arrives; for a past start the
owner can revise the start date.

**D64. Demand owners** see the revenue lost on their own demands (the rate stays hidden), have a *My
escalations* screen for their own demands, and can **ask for the offer approval** for a candidate the
panel selected (candidate + supply channel); it is priced and routed as usual and the deciders are
mailed.

**D65. Margin calculator** (GTD team admin and leadership): practice, grade, region and a bill rate
give, per supply channel, today's vendor cost, the margin, who would approve an offer, and the lowest
bill rate that meets the cut-off. Nothing is saved. May open to demand owners later.

**D66. Non-billable cap per business unit**, set by the GTD team admin on the Account overview;
leadership sees open non-billable positions against it, by practice. A new non-billable demand that
takes a BU over its cap is flagged in the "new demand" email and on the overview.

**D67. Practices** Cloud-Java, Cloud-MF and Cloud-APM are added to Discover's list (alongside the
existing five). **Mail** can be sent through Gmail SMTP, and `MAIL_REDIRECT_TO` delivers every mail to
one inbox while people's real addresses aren't in use; each mail says who it was meant for.

Parked: the leadership charts (cost pie, ABC → allocation, CPF → closed), to be discussed.


**D68. Client accounts are added by the Administrator** (user, 1 Oct). There is no separate platform
admin: the Accounts screen is in every Administrator's menu, and whoever creates an account becomes its
Administrator. The `is_platform_admin` flag is gone (migration 0013).

## Second round of 1 Oct

**D69. Main stages and sub-stages.** The five main stages are Acquisition Central's: Coverage
Required, Selection In Progress, Allocation Pending, Allocation Completed, Abandoned. Every demand
status is a sub-stage of one of them:

| Main stage | Sub-stages |
|---|---|
| Coverage Required | Draft · GTD creation pending · Correction required · GTD approval pending · GTD approval overdue · Removed from sheet · Marked incorrect · GTD approved · Sourcing profiles |
| Selection In Progress | Panel interview · Panel selected · Client interview in progress |
| Allocation Pending | Offer approval pending · Offer made, joining awaited |
| Allocation Completed | Joined |
| Abandoned | Cancelled in sheet · Closed by owner or GTD team |

Lists, filters, cards and the leadership pipeline read by main stage, with the sub-stage beside it.

**D70. The sheet is the BCM sheet** in every screen, mail and document (was "DP sheet").

**D71. The demand owner approves offers** at or above the margin cut-off; the GTD team admin is
notified of every new offer and every decision and decides none. Below the cut-off leadership still
decides, with a reason. A new offer mails whoever decides it.

**D72. Demand owners see rates** on their own demands: client bill rate, vendor cost and margin, and
they have the margin calculator. Not on a BU colleague's demand they can only read; never the GTD
admin team.

**D73. Joining date.** After an offer is approved the demand owner records "offer accepted" with the
expected date of joining; the demand moves to Allocation Pending · Offer made, joining awaited, and
the GTD team admin is mailed. The BCM sheet's DOJ replaces the owner's date when it arrives. A sheet
that is behind doesn't undo progress the app recorded. Start and joining dates show on the demand
page and in the lists.

**D74. Reconciliation is automatic.** A sheet row whose requisition isn't linked in the app links
itself when exactly one demand clearly matches (score 85 or more, the sheet's originator is the
demand's owner, no other demand within 10 points, and the demand is still waiting for its first ID);
the owner and GTD admin team get the completion mail. Every other unlinked row opens an escalation
*In BCM sheet, not in the app* to the GTD admin team, with the demand owner (the sheet's originator)
copied. It closes itself when the row is matched or a demand is created from it on the
Reconciliation page; closing it as "not ours" stops it being raised again.

**D75. Accounts are added by the Administrator** (see D68); there is no platform admin.


**D76. Client interview.** The client has no access to the app or the BCM sheet. The demand owner
marks *Client interview started* (Selection In Progress · Client interview in progress), or the sheet does. After the
interview, the demand owner records the result on the demand page: *Client selected* raises the offer
approval (Allocation Pending · Offer approval pending); *Client did not select* needs a note, takes
the candidate out, and the demand goes back to where its other candidates stand (Sourcing profiles
when there are none). The GTD team admin is mailed either way. The BCM sheet can still move the
demand on; a sheet that still says "with the client" after a recorded "no" doesn't pull it back.
