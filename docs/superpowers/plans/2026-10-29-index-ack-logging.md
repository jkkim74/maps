# Index acknowledgement logging repair

The live index subscription acknowledgement contains session encryption key/IV
fields. The old INFO statement wrote the first 300 raw characters, which could
include those fields. Environment-secret redaction cannot remove newly generated
session material reliably.

Design: parse JSON control responses, match `header.tr_id` exactly, and emit only
one locally defined status: success for string `0`, failure for string `1`, unknown
otherwise. Do not log free-form message text, result codes, headers, body or parser
exception text. Malformed JSON receives a fixed payload-free warning. Feed parsing,
subscriptions, fallback thresholds and trading state remain unchanged.

Verification plan: first reproduce raw-payload disclosure using synthetic secrets;
cover success/failure, malformed bodies/result types, unrelated TRs containing the
index name, invalid JSON and existing tick/feed behavior. Run isolated runtime/feed
tests and independent review before deployment. Check actual new subscription log,
index reception, broker sync, quote recording, health and execution invariants.
No schema or configuration migration. Preserve existing historical logs.

Progress: nine new regression cases failed against the original implementation,
confirming raw acknowledgement disclosure and substring-based TR misidentification.

Independent review identified decoder integer/nesting limits as an additional
disconnect risk. Added actual oversized/deep input tests and platform-independent
decoder-exception regressions; reproduced the escaping RecursionError and fixed it.
Review approved after correction (reviewer independently ran 57 passing tests).
Final isolated logging/runtime/feed/freshness/gap/shared-recording suite: **131
passed in 40.58s**. Changed Python files compile; `git diff --check` passes. This is
targeted validation, not a rerun of the entire repository test suite.
