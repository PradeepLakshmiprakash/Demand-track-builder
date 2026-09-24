# Demand Tracker — Development Plan

Version 1.0 · 23 Sep 2026
Companion documents: `flow-artifact.md` (flows), `techstack.md` (stack and data model).
Estimates are indicative for one developer and should be revisited after Phase 1.

## Guiding principles

1. **Account config over code.** Every Discover-specific rule (BUs, supply channels, status mapping, thresholds, margin cut-off, escalation owners) lives in account settings. Onboarding a second client must not need a code change.
2. **Every screen is its own file** — router, template folder, service.
3. **Postgres owns the IDs.** App ref and GTD requisition ID are linked at submission; the DP sheet only confirms.
4. **Missed means escalated.** Every gap the app can detect opens an escalation automatically.
5. **Dummy data first.** Build against seeded data for a single account (Discover NA) and the 09-Sep sheet; switch to live data once reconciliation is proven.
6. **Access is data, not login.** Who sees what is set on the User access page; login only identifies the user and comes last.

## Phases

### Phase 0 — Decisions and setup (≈ 0.5 week)

- Answer the open questions in `flow-artifact.md` §12 (interview scheduling owner, below-30% handling, DP sheet delivery, grace period, not-submitted trigger).
- Create the Git repo, branch rules, CI skeleton.
- **Exit:** decisions recorded; repo builds in CI.

### Phase 1 — Foundation (≈ 2 weeks)

- FastAPI app factory, config, DB session, docker-compose with Postgres.
- All SQLAlchemy models and the first Alembic migration.
- **No login yet.** A dev-only "View as" dropdown (Demand owner, Admin demand owner, Leadership, Interviewer) sets the acting user through a `current_user` dependency, so login can be swapped in later without touching screens.
- `user_access.py`: admin page to add, edit and deactivate users with account, BU(s), practice(s), level, role and visibility scope. Admin demand owner and leadership locked to full account.
- Role guard on routers and scope filter on queries; base template with role-based sidebar.
- Account settings screen (thresholds, BU list, status mapping).
- Seed script: **one account only (Discover NA)**, its four BUs, sample users for every role, dummy demands.
- **Exit:** switching views shows each persona's menu and only the data its access allows; a demand owner cannot see another BU's demands; migrations run cleanly from scratch.

### Phase 2 — Demand intake and GTD submission (≈ 2 weeks)

- `raise_demand.py`: form, validation, draft/save/submit, app ref generation.
- `my_demands.py`: list with filters and status chips.
- `gtd_queue.py`: pending list, requisition ID capture with uniqueness check, name prefix on export.
- `daily_admin_mail` job and `notification_batches`.
- **Exit:** a demand goes from draft to `sent_to_gtd` with a linked requisition ID; the admin receives the daily mail.

### Phase 3 — DP sheet import and reconciliation (≈ 2.5 weeks)

- `excel_parser.py` for the DP sheet columns, including the free-text candidate details field.
- `excel_import.py`: upload, snapshot rows per import.
- `reconcile_service.py`: exact → prefix → fuzzy tiers; missing (grace period), dropped (vs previous import), incorrect demand, unmatched rows.
- `reconciliation.py` screen: batch summary (sent / in sheet / missing), confirm suggestions, create demand from row.
- Stage events from sheet status changes.
- **Exit:** importing the 09-Sep sheet links every seeded demand correctly; a deliberately missing demand is flagged.

### Phase 4 — Escalation engine (≈ 2 weeks)

- `escalation_service.py`: triggers for not submitted, missing, dropped, incorrect demand, aging, past start date.
- `escalation_sweep` job; L1 → L2 promotion on due date; notifications.
- `escalations.py` screen: list, filters, resolve panel (reason, action, comment); resubmit creates a linked GTD submission in the next daily mail.
- **Exit:** every trigger in `flow-artifact.md` §9.1 (except rejection limit and panel SLA) opens, promotes and resolves correctly in tests.

### Phase 5 — Rate card, margin approvals, leadership view (≈ 2 weeks)

- `rate_card.py` with dated rates.
- `margin_service.py` and `approvals.py`: ≥ 30% to admin, < 30% to leadership.
- `loss_service.py`: days late, revenue lost.
- `leadership_dashboard.py`: KPIs, pipeline by status group, revenue-at-risk list, by practice and BU.
- **Exit:** leadership numbers match a manual calculation from the 09-Sep sheet.

### Phase 6 — Interviews (≈ 2.5 weeks)

- `interviewer_profiles.py`: practices, skill tags, max grade.
- Routing: heads-up alert to matching interviewers when a demand is linked.
- Candidate records from sheet candidate details; `interviews` records created by the scheduler.
- Invite mail with both IDs, JD, CV and tokenized feedback link.
- `my_interviews.py`: list, feedback form, candidate-name search fallback.
- Rejection-limit and panel-SLA escalation triggers switched on.
- **Exit:** an interviewer can submit feedback from the mail link without knowing the requisition ID; three rejections open an escalation.

### Phase 7 — Production readiness (≈ 2 weeks)

- Login: SSO via OIDC. The identity provider confirms who the user is; role, BU and visibility still come from the User access page. Remove the "View as" switcher (config flag off in production).
- AWS: App Runner or ECS, RDS Postgres, S3 for files, SES for mail, Secrets Manager.
- Logging, error alerts, daily DB backups.
- User acceptance testing with one demand owner, the admin, one leader and two interviewers.
- **Exit:** running in production for Discover NA with live DP sheets.

### Phase 8 — Second account (≈ 1 week)

- Onboard a second client through account settings only (BUs, channels, status mapping, thresholds).
- Fix anything that needed code to configure.
- **Exit:** the second account runs with no client-specific code.

## Timeline at a glance

| Phase | Scope | Indicative duration | Cumulative |
|---|---|---|---|
| 0 | Decisions and setup | 0.5 wk | 0.5 wk |
| 1 | Foundation | 2 wk | 2.5 wk |
| 2 | Intake and GTD submission | 2 wk | 4.5 wk |
| 3 | Import and reconciliation | 2.5 wk | 7 wk |
| 4 | Escalation engine | 2 wk | 9 wk |
| 5 | Rate card, approvals, leadership | 2 wk | 11 wk |
| 6 | Interviews | 2.5 wk | 13.5 wk |
| 7 | Production readiness | 2 wk | 15.5 wk |
| 8 | Second account | 1 wk | 16.5 wk |

A usable first release for Discover (demand owners and admin, with reconciliation and escalations) is possible after Phase 4, around week 9, by pulling a slimmed Phase 7 (deployment only, session login) forward.

## Risks

| Risk | Mitigation |
|---|---|
| DP sheet format changes | Column mapping in account config; parser validates headers and reports what's missing |
| Admin forgets to record requisition IDs | Name-prefix matching, not-submitted escalation |
| Candidate names inconsistent across sheet columns | Candidate IDs in the app; fuzzy match with admin confirmation |
| Interviews scheduled outside the app | Name-search fallback on the interviewer screen |
| Leadership numbers disputed | Loss formula and inputs shown on the dashboard; rates from dated rate card |
| Scope creep toward a full ATS | Keep the DP sheet as the source for supply-side status; the app tracks, it doesn't source |

## Definition of done (per phase)

- Screens and services built with tests; CI green.
- Alembic migration included and applied from scratch.
- Seed data updated so the phase can be demoed end to end.
- Relevant sections of `flow-artifact.md` updated if behavior changed.
