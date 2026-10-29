# Annual-source failure audit — 2026-10-29

## Scope and evidence

Investigated the 12 partial securities from the preceding 20-security batch.
Used the existing paced DART client for 29 dividend-audit requests and 30 financial
audit requests, including corporate-code lookups. Raw responses and audit summaries
are retained privately in `/opt/maps/backups/annual-failure-audit-20261029/`.
No parser, eligibility gate, source review or historical success marker was changed.

## Findings

- `0053F0`, `005725`, `005745`: no corporate-code mapping for these security codes.
  The latter two are preferred shares; issuer mapping was not guessed or borrowed.
- Samjin Pharmaceutical (`005500`, 2023/2024), Samyoung Electronics (`005680`, 2023),
  Daelim B&Co (`005750`, 2022–2025), Wonlim (`005820`, 2022): dividend API contains
  numeric cash-DPS rows but `stock_knd` is `-`, with another ambiguous row. The
  existing requirement for exactly one explicit common-share row correctly blocks
  automatic ingestion. Review exact filing documents before introducing a supported
  provenance path; do not infer share class from row order or the nonempty amount.
- Huneed (`005870`) and Korea Line (`005880`): audited annual cash-DPS rows have
  unspecified share class and `-` amounts. These do not establish a numeric zero.
- Hanil Feed (`005860`, 2022): common-share cash DPS is nonnumeric; `invalid_money`
  remains blocked. No zero substitution.
- DB Insurance (`005830`): seven audited periods fail the existing standard
  revenue/operating-profit pair. Six responses contain insurance-specific statement
  accounts; 2022 returns no financial rows. Supporting insurance statements needs
  a separately justified financial mapping and tests, not arbitrary alias relaxation.
- Crown Haitai (`005740`) and Huneed financial requests were checked against current
  registered receipt/period identities. Most valid responses already had snapshots.
  One exact-identity, parser-valid Huneed snapshot was genuinely absent and recovered.

## Recovery and verification

Inserted **one** Huneed 2026 half-year OFS snapshot using the existing
`DartFinancialRepository.save` validator: receipt `20260929000648`, period end
2026-06-30, id 5642. Rechecked absence before insertion; raw response hash is
`a10d3e6e7176d8d619fff0d4a0b901f95c5613822a4bf5e362d0083d5e3039cf`.
Observed at actual recovery time; available date **2026-10-30**. Existing snapshots
were not overwritten. Total financial snapshots: 5641 → 5642.

Execution policy/config/control and order/fill counts match the before snapshot.
No restart, environment change or trade activation was required. Tests:
`scripts/run_isolated_tests.py tests/test_dart_financials.py
tests/test_fujimoto_sources.py -q --tb=short`: **38 passed in 7.53s**.

This closes the bounded failure investigation and the immediately verifiable
snapshot repair. It does not make all 12 partial securities complete: ambiguous
dividends, unsupported statement structures, missing mappings and annual
comparability evidence remain blocked. Actual post-close universe/screening and
whole-market coverage remain separate readiness requirements.
