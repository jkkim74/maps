# Bound frozen candidate loading in the PER operational report

This is a separate operational remediation after PER completion v3 (`ff74b90`),
not a change to screening, collection or execution authorization. Parent-owned
PER deployment plan and handoff edits remain separate.

## Purpose and observed evidence

On 2026-10-21, the deployment operator reported server response delays while
running a 39-ticker valuation report against 8,286 candidate records. The server
cause and any OOM relationship are unconfirmed. Read-only code investigation
confirmed that the report iterated every available candidate before filtering
ticker/date in Python. Installed SQLAlchemy 2.0.49 `orm/loading.py` calls
`cursor._raw_all_rows()` and constructs all ORM rows without `yield_per`.
Each ordinary JSON payload includes the potentially large compressed snapshot.
The old report also retained all latest candidate ORM objects in a dictionary.

## Binding design and files

- `maps/fujimoto/valuation.py`: constrain candidate kind, selected tickers,
  exact payload reference date and both timestamps in SQL, then group by ticker
  and select `max(id)`. This preserves the latest eligible appended revision;
  it does not redefine latest as observation order. Fetch full payloads only for
  these IDs, ordered by ticker, using `yield_per(1)` without a candidate dictionary.
- Explicitly empty selection issues no candidate query. Whole-market selection
  uses the same bounded payload iterator. Report output and public signature stay
  stable; comparison still rebuilds v2 and v3 from exactly the same frozen raw.
- `tests/test_fujimoto_valuation_report.py`: real isolated SQLAlchemy ORM load
  events prove that unrelated tickers, other dates, old revisions and future or
  pending observations do not materialize; the real screening builder observes
  only one newly loaded candidate per comparison. Test subset and whole market.
- `maps/fujimoto/CLAUDE.md`: document the memory boundary and incident uncertainty.
- Detailed commands and outcomes go in the private task report
  `.superpowers/sdd/2026-10-21-per-completion-v3/operational-report-fix.md`.

## Progress and executable acceptance

- [x] Investigate ORM fetch behavior without production access or new server load.
- [x] Red: isolated report tests fail 3/3 on the bulk-load code (12 available
  candidate entities materialized before the first comparison; empty selection
  also issues a candidate query).
- [x] Implement SQL selection and streaming; the same 3 regressions pass.
- [x] Focused valuation/current-use/replay/source/evidence/paper-trial and
  documentation checks: **202 passed in 77.16s**, exit 0. Compile and scoped
  diff checks passed. The first focused run exposed test-observer import leakage;
  initializing the lazy dependency before observing the builder fixed the test
  setup, with no further production changes.
- [ ] Parent independent review and full regression before any additional deploy.

Command (repository root, isolated mock DB/network-disabled runner):

```powershell
& D:/workspace/maps/maps/maps/.venv/Scripts/python.exe scripts/run_isolated_tests.py tests/test_fujimoto_valuation_report.py tests/test_fujimoto_valuation.py tests/test_fujimoto_current_valuation.py tests/test_fujimoto_replay.py tests/test_fujimoto_sources.py tests/test_fujimoto_evidence.py tests/test_fujimoto_paper_trial.py tests/test_docs_index.py -q --tb=short
```

## Decisions, limits and rollback

No schema, proof API, timeout, strategy, trading or collector behavior changes.
Metadata/EPS history queries are outside this confirmed candidate bulk-load fix;
their separate scalability review can be proposed after operational recovery.
Grouping/filtering JSON can still cost database work; this change bounds payload
transfer and application retention, without introducing an index/migration.
Report result rows and the selected ticker/ID sets remain proportional to the
requested universe, but full candidate snapshots are processed individually.
No production memory benchmark or server-cause claim is made.

Rollback is a revert of this remediation commit after operator review. Because
`valuation.py` participates in the code fingerprint, code-bound approvals must
follow existing invalidation rules. This change grants no execution permission.
