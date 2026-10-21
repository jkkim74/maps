# PER completion v3 execution plan

Approved scope: retain KRX positive PER, distinguish documented negative common EPS and official nonordinary/SPAC securities from genuinely unknown valuations. No numeric KIS fallback, estimated PER, trading activation, or edits to shared fundamentals.

## Binding design

- Preserve frozen v1/v2 replay behavior and provider-reviewed `verified_not_applicable` semantics. Add screening v3 with a separate strategy-exclusion state backed by DART negative annual common EPS; never represent DART evidence as an explanation issued by KRX.
- Reuse immutable FujimotoEvidence JSON sources and reviews; no schema migration. Freeze every new proof into candidate snapshots and validate it during shared replay/readiness rebuild.
- Official exact-date KRX basic metadata (KIND_STKCERT_TP_NM and SECT_TP_NM; no ticker-name heuristics) determines ordinary/non-SPAC comparison membership. Preserve all ordinary peers regardless of trading eligibility, growth or liquidity. Unknown classification remains blocking. Current metadata must never be relabelled as historical.
- Retain KRX positive PER and existing positive median including self. Nonordinary/SPACs leave comparison and cannot become candidates. Negative-EPS peers can leave completeness denominator only after validated evidence; their own entry remains blocked. Other missing/nonpositive/zero or conflicting evidence remains unknown. No coverage percentage shortcut.
- Negative-EPS proof uses applicable annual common basic EPS, CFS preferred, OFS only on explicit CFS non-applicability. Preserve sign and match corporation/ticker, fiscal period, receipt and statement context. Direct standard basic EPS is sufficient if common-share context is unambiguous and no contradictory rows/components. Component sum requires both continuing/discontinued numeric entries from identical context; absent never means zero. No operating/total net-loss inference. Unknown/custom tags or mixed share classes require original-document review or remain unresolved.
- Use 2025-12-31 annual statements for the initial 2026-10-21 twelve-month-end cohort, under KRX's annual update window. Other fiscal calendars remain unknown unless explicit evidence establishes the applicable period. Latest known correction masks stale evidence. An unavailable/error response is not permission to switch statement basis.
- Actual first observation/review time and next-session financial availability remain causal. Ingestion may record today but must not manufacture today's readiness. Reviews bind exact source hashes and financial receipts. Subsequent corrections/identity changes invalidate applicable current evidence; old snapshots replay their original context.
- Source selection must never let diagnostic KIS/Naver sidecars replace a valid authoritative KRX input. Store optional diagnostics separately; this implementation need not add another broker collector.
- Daily operation refreshes bounded missing-EPS/official metadata evidence and generates per-state/reason counts, along with v2/v3 same-data comparison. Respect provider request budgets. Do not reuse the eligible-only annual collector for excluded peers (loss-making and financial peers are required).

## Task 1 — Implement, test, document

Implement the versioned validation and source collection/review flow, offline comparison/operational CLI, and integrate the normal market-screen path. Prefer a focused valuation module plus small edits to evidence/sources/replay over broad refactors. Reuse settings, DartClient, metadata adapter and immutable repository. Expose source ingestion and dry-run/report separately from applying validated reviews; command names can follow existing CLI conventions. Public HTTP API changes are unnecessary.

Acceptance tests: legacy v1/v2 unchanged; positive KRX retained; signed negative common EPS validated; unproven/zero EPS blocks; conflicting KR Motors-like direct/component EPS blocks; consolidated owner loss without EPS does not pass; stale Samsung Pharma-like 2024 evidence cannot justify 2025 exclusion; positive-EPS Bowon-like missing PER stays unknown; exact-date official preferred/SPAC exclusion and unknown/missing metadata; peer set not reduced to eligible candidates; own excluded security never admitted; receipt/basis/ticker/year/hash tampering, future evidence, later pending correction, component context disagreement, CFS transient failure, and superseding observations fail closed; frozen replay and readiness use identical rules.

Use failing regression tests first, then implement. Run focused isolated tests, compile/diff checks, and independent review. Update maps/fujimoto/CLAUDE.md and the execution progress below. Do not commit secrets/raw provider/account payloads.

## Task 2 — Operational validation and rollout

Back up production before writes. Reuse observed raw KRX baseline and current official metadata; acquire exact DART evidence for all comparison peers, starting securities sector (11 preferred stocks plus Sangsangin). Expand boundedly to promising-sector unknown peers, reporting retained unknowns. Compare v2/v3 on identical frozen inputs in memory. Do not claim that comparison alone is a complete screen pass.

After independent review and full isolated regression, integrate/push and deploy using the established service workflow. Capture new proofs at actual time; rerun screen only with currently available evidence and report upcoming evidence availability. Preserve environment hashes and all order/approval switches. Check service/health/errors and trial remains unstarted. Do not liquidate existing positions or activate any order path.

Rollback: revert feature commits, restart established service; evidence is append-only and older rules/snapshots retain behavior. Never delete audit data. Runbook reports commit, source counts, comparison results, actual screen outcome, limitations and remaining trial blockers.

## Progress / decisions / test ledger

- 2026-10-21: user approved final plan. Reusing clean existing isolated worktree C:/Users/jack/.codex/worktrees/9f3a/maps at 3680ba5; main workspace's four user document changes remain untouched.
- Task 1 implementation/test progress: implemented version 3 shared screening,
  exact-date official security classification, signed common-EPS source/review
  proofs, causal receipt masking, bounded independent EPS ingestion, normal daily
  refresh and operational CLI. Original v1/v2 frozen replay remains versioned.
- 2026-10-21 decisions: CFS `013` alone cannot establish non-applicability;
  collected OFS sources remain unresolved until an original-document basis review
  workflow exists. Explicit common EPS is separate from clearly labelled preferred
  EPS. Numeric PER retains original `pykrx`/`krx` provider identity; diagnostic
  sources and newer Naver shared rows cannot override authoritative KRX inputs.
- Unchanged EPS checks reuse their first immutable observation and review, even
  when official identity changes representation between company.json and corpCode
  mapping. Actual corporation/receipt/basis/financial response changes mask reviews.
- Verification: failing regressions were observed before feature implementation.
  Final identity-representation fix followed by fresh focused run: 73 passed
  (`test_fujimoto_valuation`, `test_fujimoto_sources`, `test_fujimoto_evidence`,
  12.38s). Broader replay/paper-trial/final-review/docs-index run: 200 passed
  (61.65s). Compileall and git diff --check exit 0. Independent parent review and
  full isolated regression are pending before integration/rollout.
- Task 2: pending.
- Task 1 review round 1: addressed fail-closed unknown market/group handling and
  independent first-observed identity journaling before subsequent provider
  failures. Current v3 proofs require latest-known identity context; frozen
  snapshots preserve their original context. Public source ingestion signatures
  and v1/v2 replay rules remain unchanged. Verification ledger is in the task report.
  Final post-fix checks: 145 valuation/source/evidence/replay/paper-trial tests plus
  69 final-review/docs-index tests passed; compileall and diff checks exit 0.
- Task 1 review round 2: archival source ingestion now seeds identity only when
  no independent observation exists. Re-imported unchanged or changed old-corp
  financial archives cannot supersede a later independent identity. Extended
  collector failure/replay regressions and a causally available changed-archive
  review reproduce and verify the invalidated-review resurrection fix.
  Final verification: 146 valuation/source/evidence/replay/paper-trial tests and
  69 final-review/docs-index tests passed; compileall and diff checks exit 0.
