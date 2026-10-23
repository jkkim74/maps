# Fujimoto task-focused operator UI

## Purpose and binding design

Implement the reviewed 2026-10-23 HTML prototype as the actual `/fujimoto` screen.
Start from production `1bb4e1279925c99a165127d1a1cb045599d24e53` in the isolated
`feat/fujimoto-friendly-ui` worktree. Preserve unrelated dirty master work.
Show current state and one next action, compact saved budgets, one actionable
reason list, consent only when ready, and campaign monitoring before settings.
Keep original-mode no-price-stop and new-acquisition sell consent explicit.

## Interfaces and boundaries

Only template/UI tests and usage documentation change. Reuse all existing GET and
mutation contracts, owner permissions, 15-second read-only polling and concurrency
guards. No backend, schema, broker settings, strategy rules or account changes.
Separate new-trial eligibility from existing trial/validated operation. Show unknown
data as unknown, not zero or a pass. Preserve actual cumulative fills/reservations.
Existing measured activation remains in advanced settings. No production activation,
holding adoption or liquidation is performed. Initial implementation excluded deployment;
the user explicitly requested production deployment on 2026-10-23.

## Work and progress

- [x] Inspect actual deployment, API contracts, template, and offline UI harness.
- [x] Create worktree at matching production revision.
- [x] Reorganize actual template and state-derived guidance.
- [x] Extend offline tests for state visibility, safety, nulls, and polling behavior.
- [x] Run focused regression tests and browser verification at desktop/mobile widths.
- [x] Update usage documentation and review final diff.

## Acceptance checks

Use the existing virtualenv with `scripts/run_isolated_tests.py` for
`tests/test_fujimoto_ui.py`, `test_fujimoto_api.py`, `test_fujimoto_paper_trial.py`
and `test_docs_index.py`; broaden to Fujimoto regression modules when these pass.
Browser QA uses Jinja-rendered real template plus offline fixture responses only;
never import/start the live app with production dotenv or scheduler.
Cover unset/saved budget, simultaneous blocks, consent, scheduled/active/stopped/
expired, measured activation, partial/unknown orders, missing data, failed reads,
deferred requests, hidden-page polling, dirty form/disclosure retention and no
automatic mutation. Record screenshots of the actual implementation with fixtures.

## Decisions, results and rollback

2026-10-23: API already supplies required data. Keep backend unchanged. Local master
predates deployed feature; isolated worktree is necessary rather than copying old
feature files over current production. Prototype review controls/fake data never
ship in production template. Deployment requires a separate concrete review step.

Rollback: revert this UI change only; no migration or operational state restoration
is needed. Do not reset or alter existing worktrees or account state.

## Implemented files and verification

- `templates/fujimoto.html`: scoped responsive layout, next action, deduplicated issues,
  progressive disclosure, readiness-only consent, campaign/validated monitoring,
  completed-stage markers, mobile order cards, native stop confirmation and stale data.
- `tests/test_fujimoto_ui.py`: shipped-script regression coverage, including no writes
  on initial load/poll/dialog cancellation and conservative treatment of missing data.
- `maps/api/CLAUDE.md`, `tests/CLAUDE.md`: usage and verification contracts updated.
- `docs/ui-design/assets/fujimoto-friendly-ui-{desktop,mobile,mobile-orders}.jpg`:
  screenshots of the actual Jinja-rendered implementation with explicitly synthetic data.

Focused UI/API/paper-trial/docs checks: 115 passed. Initial full Fujimoto/docs regression:
397 passed. After tightening null-order/money and UTC timestamp handling, UI/docs checks:
71 passed. Final full Fujimoto/docs regression: **399 passed**, 1 existing
Starlette/httpx deprecation warning, 109.29 seconds. `git diff --check` passed.

```powershell
$fjTests = (rg --files tests | Where-Object { $_ -match 'test_fujimoto_' })
& D:/workspace/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py @fjTests tests/test_docs_index.py -q --tb=short
```

Browser QA: actual Jinja template and base stylesheet, offline fixture fetch only, no
app startup, scheduler, external requests or account operations. Checked 10 states
(unset/saved-blocked/ready/scheduled/active/stopped/expired/missing/validated/failure),
explicit consent then reservation, stop-dialog cancellation, disclosure navigation and
390px responsive layout without horizontal overflow. Browser console contained only
an unrelated installed extension error; no application JavaScript errors observed.
Local preview is `http://127.0.0.1:8769/index.html` while the review server runs.
Preview navigation follow-up: the static review server does not host other application
routes. Its local builder now intercepts service navigation with an explanatory dialog
and an explicit live-service link for ops/risk/batch pages, avoiding misleading 404s.
Confirmed `/ops-config` already exists in `main.py`; execution switches remain read-only
in that screen. This fix changes the preview wrapper only, not production navigation.

The change does not touch any file in `maps/fujimoto/replay.py::code_fingerprint`;
strategy code bindings, APIs, permissions, database schema and operational switches
remain unchanged. No dependency additions, production deployment or trading activation.
Synthetic QA does not establish trading readiness.

## Authorized production deployment — 2026-10-23

- [x] User explicitly requested production deployment.
- [x] Recheck production/origin at `1bb4e12`, no modified tracked files, service active,
  health OK, analysis lock available, outside the 16:00–16:45 KST restricted window.
- [x] Save a private read-only deployment baseline for environment/config/control and
  Fujimoto order/fill counts; no broker calls or secrets printed.
- [x] Run the full isolated `tests maps/tests` suite. First collection failed because
  the local virtualenv lacked the existing `exchange-calendars>=4.11,<5` requirement;
  repaired only that local verification environment, then reran: **2,471 passed**,
  2,602 dependency/deprecation warnings, 524.13 seconds. Product requirements unchanged.
- [ ] Commit only reviewed UI/tests/docs/screenshots, push a fast-forward release.
- [ ] Fast-forward production, restart according to the existing runbook, check health,
  authenticated screen and `/ops-config` navigation, compare deployment baseline.
- [ ] Record deployed revision, verification and rollback result/instructions.

Production rollback baseline: `1bb4e1279925c99a165127d1a1cb045599d24e53`.
Use a targeted Git revert and normal service restart if the UI deployment fails;
do not reset the working tree or change any operational settings/holdings.
