# Fujimoto Guided Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Execute the approved design without another design approval round.

**Goal:** Make the next user action obvious throughout the existing paper-test lifecycle.

**Architecture:** Reorganize the existing Jinja template and its inline JavaScript around the existing status contract. Use server-supplied readiness and lifecycle states; no new API, backend trading behavior, dependency, or schema. Extend the existing offline Node VM harness for polling and races.

**Tech Stack:** Jinja2, plain HTML/CSS/JavaScript, pytest, Node built-ins.

## Global Constraints

- Binding approved design: `docs/superpowers/specs/2026-10-22-fujimoto-guided-start-design.md`.
- Start requires server `paper_test.eligible === true` and explicit checked consent; no automatic mutation or approval.
- Keep config PUT, trial POST, measured activation POST, stop POST contracts unchanged. Never change production trading/account/configuration switches or start a trial for UI verification.
- Preserve saved settings and unsaved form edits during reads. Saving budget/settings continues to stop new entries and successful writes clear trial consent.
- Poll every 15 seconds only while visible, with no overlapping reads or stale pre-mutation response rendering. Read failure must fail closed and clearly replace ready guidance.
- All dynamic external strings remain escaped. Null/missing data is explicit; no invented candidates, account readiness or dates.
- Reuse current styling and dependencies. No broad refactor, unrelated files, or backend/API changes.
- Preserve the existing morning-handoff modifications and original user files; stage/commit only this task's named files.

### Task 1: Guide the paper-test lifecycle in the existing screen

**Files:**
- Modify `templates/fujimoto.html` (layout, status/next-action rendering, translated reasons, polling).
- Modify `tests/test_fujimoto_ui.py` (actual shipped-script behavioral regression tests and timer/deferred-response harness).
- Modify `maps/api/CLAUDE.md` (actual screen usage and polling behavior).
- Update this plan's progress and create task report in its ignored workspace.

**Interfaces:** Existing `/api/v1/fujimoto/status`, `/candidates?limit=15`, `/validation`, `/evidence?limit=15`, and current mutation endpoints. Server readiness remains authoritative. Keep existing control IDs so current tests and controls can remain compatible; add semantic IDs for progress/next-action/refresh summary as needed.

- [ ] Write failing behavioral checks first, run them and record failures. Cover budget-unset, saved budget with zero candidates, candidate present but operational/quote blocks, ready unchecked/checked, scheduled/active/stopped/expired, null status and read error. Test timer-triggered GET-only refresh preserving drafts; hidden-page polling skip; delayed GET overlapping explicit mutation cannot restore stale readiness; no overlapping polls; explicit mutation contracts remain unchanged. Preserve existing meaningful tests.

  Use the existing runner, for example:
  ```powershell
  & 'D:/workspace/maps/maps/maps/.venv/Scripts/python.exe' scripts/run_isolated_tests.py tests/test_fujimoto_ui.py -q --tb=short
  ```

- [ ] Implement the approved sequence. Top next-action card and four linked steps: `1 예산 확인`, `2 자동 준비 점검`, `3 동의·시작 승인`, `4 진행 확인`. Put budget before approval, clearly display already-saved amounts, and place a stop-on-save note next to the save button. Keep the original no-price-stop warning and new-acquisition-only sell consent visible in the approval flow.
- [ ] Render guidance from actual data: candidate wait is not a promise of future passes; operational blocking reasons need operator attention, with read-only navigation to existing operational pages if helpful, not a new toggle. Distinguish ordinary data wait from user action and unknown reasons. Active/scheduled states direct users to monitoring/start dates, not a new approval. Stopped/expired states retain their real reasons and do not imply reactivation permission.
- [ ] Add sequential visibility-aware refresh using native timers; avoid duplicate reads and mutation races. Keep the latest successful status timestamp visible and show refreshing/error state. Initial and background requests are GET only. Preserve edited forms; a failed read disables approval until an actual successful reload. A successful write performs one fresh reload after any invalidated read.
- [ ] Collapse measured validation activation under a clearly labelled `<details>` advanced section. Translate common candidate blocking codes and use `미통과`/`확인 필요` rather than presenting failed selection as ordinary waiting. Keep ticker codes if no names are available; do not invent names or expand the API.
- [ ] Run the focused UI, API, paper-trial and docs checks:
  ```powershell
  & 'D:/workspace/maps/maps/maps/.venv/Scripts/python.exe' scripts/run_isolated_tests.py tests/test_fujimoto_ui.py tests/test_fujimoto_api.py tests/test_fujimoto_paper_trial.py tests/test_docs_index.py -q --tb=short
  git diff --check
  ```
- [ ] Update package usage documentation and test ledger. Commit only template/test/API-doc changes and this task's plan progress with a concise `feat:` message. Report exact failed/passing commands, changes, commit and any concerns.

## Parent verification and delivery

- Obtain independent spec-compliance and code-quality review of the entire feature diff. Resolve substantive findings with the implementer and recheck the changed scope.
- Render the real shipped template and API fixtures in an isolated local preview; inspect waiting, ready, scheduled/active and failure states in the browser. Save desktop and narrow-width screenshots labelled as synthetic data. Never use real approval controls for verification.
- Run the relevant isolated checks after fixes, then one full isolated regression if deploying. Review final diff and original-user-file hashes.
- Integrate only reviewed feature commits into the primary checkout, preserving existing changes. Record actual deployment state separately; do not claim production updated until verified. Follow repository production window/clean deployed-tree rules and retain all trading switches.

## Progress, decisions and test ledger

- 2026-10-22: User approved the previously proposed guided screen. Existing isolated worktree `C:/Users/jack/.codex/worktrees/9f3a/maps` is reused; unrelated morning handoff modifications remain unstaged. Baseline product is `e3ec484` (deployed code `f847da1` differs only in handoff docs).
- Implementation, behavioral verification, visual QA and independent review pending.

## Rollback

Revert only the guided-screen feature commits. No schema/data migration or trading setting restoration is required because this change does not modify them. Production rollback uses the established deployment workflow with the same trading-state invariants.
