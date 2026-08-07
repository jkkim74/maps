# AI Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded, source-transparent Bedrock AI scoring that can be disabled, rerank rule-selected candidates, or replace recommendation scores without bypassing existing signals and order safety gates.

**Architecture:** Candidate generation first persists rule-only snapshots for every strategy, then a new `AIStockScoringService` groups eligible rows by ticker, enforces a durable daily request budget, reuses cached results, calls Bedrock once per ticker, and updates strategy-specific recommendation scores. Pure scoring validation lives separately from the Bedrock adapter; order selection shares one SQL expression so `rerank` eligibility always uses `rule_score` while ordering uses `recommendation_score`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, pandas, boto3 Bedrock Runtime `InvokeModel`, Pydantic settings, pytest, Jinja2, vanilla JavaScript.

## Global Constraints

- `MAPS_AI_SCORING_MODE` is `off|rerank|replace` and defaults to `off`.
- `rerank` candidate eligibility and minimum-score checks use `rule_score`; only ordering uses `rule_score * 0.80 + ai_score * 0.20` by default.
- `replace` keeps existing market, signal, liquidity, exclusion, freshness, and order safety gates; only the globally shortlisted rows are recommendation-eligible.
- The global daily network-call limit defaults to 5 unique tickers and counts started, successful, and failed calls.
- The default model is `us.anthropic.claude-sonnet-4-6`; `us.anthropic.claude-haiku-4-5-20251001-v1:0` remains a configuration alternative.
- Sonnet 4.6 requests use adaptive thinking with `output_config.effort="low"`; do not send `thinking=disabled`, `temperature`, `top_p`, or `top_k`.
- Bedrock calls use `bedrock-runtime`, not `bedrock-mantle`, because structured outputs are required.
- Do not send the raw 30-bar OHLCV table. Send locally derived indicators only.
- Do not request AI-generated buy, stop, or target prices. Existing rule-based price plans remain authoritative.
- Do not use prompt caching, batch inference, or automatic network retries.
- Same-day identical results are reused by `ref_date+ticker+input_hash+model_id+prompt_version`.
- AI errors never abort candidate generation; they produce `RULE_FALLBACK` without exposing credentials or raw provider responses.
- Preserve the user's unrelated `HANDOFF.md` working-tree change.
- All new or changed Python functions and classes require type hints and docstrings.
- Follow red-green-refactor: every behavior change starts with a failing focused test.

---

## File Structure

### New production files

- `maps/ai/scoring.py` — score enums, immutable result types, structured-payload validation, rubric totals, and mode-specific recommendation formulas.
- `maps/ai/scoring_service.py` — durable daily budget, cache lookup/reservation, ticker deduplication, Bedrock invocation orchestration, snapshot updates, and run summary.
- `maps/ops/candidate_selection.py` — shared SQLAlchemy expressions for minimum-score and replace-mode recommendation eligibility.
- `maps/ai/evaluation.py` — pure model-comparison statistics for schema success, score variance, token usage, and latency.
- `scripts/evaluate_ai_scoring_models.py` — guarded live Bedrock comparison CLI.
- `alembic/versions/0020_ai_scoring.py` — candidate score-source columns and `ai_scoring_invocation` table.

### Modified production files

- `maps/common/settings.py` — new mode, budget, weight, model, and timeout settings plus legacy compatibility.
- `maps/common/exceptions.py` — typed AI configuration, provider, and response-validation errors.
- `maps/common/models.py` — candidate score-source fields and invocation ORM model.
- `maps/ai/technical_scorer.py` — compact features, static rubric, dynamic ticker payload, JSON Schema, and Bedrock Runtime adapter.
- `maps/ops/scheduler.py` — rule-only snapshot generation followed by one global AI scoring pass.
- `maps/ops/order_preview.py` — shared eligibility expressions.
- `maps/api/schemas.py`, `maps/api/candidates.py` — score-source response fields.
- `static/js/app.js`, `templates/candidates.html` — source badges and rule/AI/recommendation columns; rule-based price labels.
- `.env.example`, `docs/OPERATIONS_CONFIG.md` — operator configuration and rollout instructions.

### Test files

- `tests/test_settings.py`
- `tests/test_ai_scoring_models.py`
- `tests/test_ai_scoring_domain.py`
- `tests/test_ai_technical_scorer.py`
- `tests/test_ai_scoring_service.py`
- `tests/test_ai_scoring_scheduler.py`
- `tests/test_candidate_snapshot_scheduler.py`
- `tests/test_order_preview.py`
- `tests/test_candidates_api.py`
- `tests/test_candidates_ui.py`
- `tests/test_ai_scoring_evaluation.py`

---

### Task 1: Runtime configuration and legacy compatibility

**Files:**
- Modify: `maps/common/settings.py:1-160`
- Modify: `maps/common/settings.py:350-390`
- Modify: `.env.example:65-85`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `AIScoringMode = Literal["off", "rerank", "replace"]`.
- Produces: `MapsSettings.maps_ai_scoring_mode`, `maps_ai_daily_call_limit`, `maps_ai_rerank_weight`, `maps_ai_scoring_model_id`, and `maps_ai_request_timeout_seconds`.
- Compatibility: old `maps_ai_technical_scoring_enabled`, `maps_ai_technical_score_weight`, and `maps_ai_candidate_top_n` remain readable for one release.

- [ ] **Step 1: Write failing defaults and override tests**

```python
def test_ai_scoring_defaults_are_safe_and_bounded() -> None:
    settings = MapsSettings()
    assert settings.maps_ai_scoring_mode == "off"
    assert settings.maps_ai_daily_call_limit == 5
    assert settings.maps_ai_rerank_weight == 0.20
    assert settings.maps_ai_scoring_model_id == "us.anthropic.claude-sonnet-4-6"
    assert settings.maps_ai_request_timeout_seconds == 60.0


def test_ai_scoring_settings_accept_explicit_replace_overrides() -> None:
    settings = MapsSettings(
        maps_ai_scoring_mode="replace",
        maps_ai_daily_call_limit=9,
        maps_ai_rerank_weight=0.35,
        maps_ai_scoring_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
    assert settings.maps_ai_scoring_mode == "replace"
    assert settings.maps_ai_daily_call_limit == 9
    assert settings.maps_ai_rerank_weight == 0.35
```

- [ ] **Step 2: Run the focused tests and confirm missing attributes**

Run: `pytest tests/test_settings.py::test_ai_scoring_defaults_are_safe_and_bounded tests/test_settings.py::test_ai_scoring_settings_accept_explicit_replace_overrides -v`

Expected: FAIL with `AttributeError` for `maps_ai_scoring_mode`.

- [ ] **Step 3: Add typed settings with validation bounds**

```python
AIScoringMode = Literal["off", "rerank", "replace"]

class MapsSettings(BaseSettings):
    maps_ai_scoring_mode: AIScoringMode = "off"
    maps_ai_daily_call_limit: int = Field(default=5, ge=0, le=100)
    maps_ai_rerank_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    maps_ai_scoring_model_id: str = "us.anthropic.claude-sonnet-4-6"
    maps_ai_request_timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
```

Keep the three legacy fields in the model. Add the five new environment variables to the `data` config-status section and `.env.example`.

- [ ] **Step 4: Write failing legacy precedence tests**

```python
def test_legacy_ai_enabled_maps_to_rerank_when_new_mode_is_absent() -> None:
    settings = MapsSettings(
        maps_ai_technical_scoring_enabled=True,
        maps_ai_technical_score_weight=0.30,
        maps_ai_candidate_top_n=7,
    )
    assert settings.maps_ai_scoring_mode == "rerank"
    assert settings.maps_ai_rerank_weight == 0.30
    assert settings.maps_ai_daily_call_limit == 7


def test_explicit_new_ai_settings_win_over_legacy_values() -> None:
    settings = MapsSettings(
        maps_ai_scoring_mode="off",
        maps_ai_daily_call_limit=3,
        maps_ai_rerank_weight=0.10,
        maps_ai_technical_scoring_enabled=True,
        maps_ai_technical_score_weight=0.80,
        maps_ai_candidate_top_n=50,
    )
    assert settings.maps_ai_scoring_mode == "off"
    assert settings.maps_ai_daily_call_limit == 3
    assert settings.maps_ai_rerank_weight == 0.10
```

- [ ] **Step 5: Implement a post-validation compatibility mapper**

Use `self.model_fields_set` so an explicitly supplied new value wins even when it equals the default.

```python
@model_validator(mode="after")
def _map_legacy_ai_scoring_settings(self) -> "MapsSettings":
    supplied = self.model_fields_set
    if "maps_ai_scoring_mode" not in supplied and self.maps_ai_technical_scoring_enabled:
        self.maps_ai_scoring_mode = "rerank"
        warnings.warn("MAPS_AI_TECHNICAL_SCORING_ENABLED is deprecated", DeprecationWarning)
    if "maps_ai_rerank_weight" not in supplied and "maps_ai_technical_score_weight" in supplied:
        self.maps_ai_rerank_weight = self.maps_ai_technical_score_weight
    if "maps_ai_daily_call_limit" not in supplied and "maps_ai_candidate_top_n" in supplied:
        self.maps_ai_daily_call_limit = self.maps_ai_candidate_top_n
    return self
```

- [ ] **Step 6: Run settings tests**

Run: `pytest tests/test_settings.py tests/test_ops_config_api.py -v`

Expected: PASS.

- [ ] **Step 7: Commit configuration changes**

```powershell
git add maps/common/settings.py .env.example tests/test_settings.py tests/test_ops_config_api.py
git commit -m "feat: add bounded AI scoring settings"
```

---

### Task 2: Persistence schema and migration

**Files:**
- Create: `alembic/versions/0020_ai_scoring.py`
- Modify: `maps/common/models.py:75-140`
- Test: `tests/test_ai_scoring_models.py`

**Interfaces:**
- Produces: new nullable/backward-compatible score columns on `CandidateSnapshot`.
- Produces: `AIScoringInvocation` with durable request reservation and response-cache payload.
- Cache identity: `(ref_date, ticker, input_hash, model_id, prompt_version)`.

- [ ] **Step 1: Write failing ORM shape tests**

```python
def test_candidate_snapshot_stores_score_provenance(db) -> None:
    row = CandidateSnapshot(
        ref_date=dt.date(2026, 8, 7), strategy_id="pullback_v3",
        ticker="005930", name="삼성전자", market="KOSPI",
        factor_score=80, trend_strength=70, ts_bucket="S4",
        final_score=76, rule_score=75, recommendation_score=76,
        score_source="AI", ai_scoring_mode="rerank", ai_status="SUCCESS",
        ai_confidence=0.82, ai_reason_codes=["UPTREND"],
        ai_model_id="us.anthropic.claude-sonnet-4-6", weekly_pass=True,
    )
    db.add(row)
    db.commit()
    saved = db.query(CandidateSnapshot).one()
    assert saved.rule_score == 75
    assert saved.recommendation_score == saved.final_score == 76
    assert saved.score_source == "AI"


def test_ai_invocation_unique_cache_key(db) -> None:
    kwargs = dict(
        ref_date=dt.date(2026, 8, 7), ticker="005930", input_hash="a" * 64,
        model_id="us.anthropic.claude-sonnet-4-6", prompt_version="ai-score-v1",
        status="STARTED", input_tokens=0, output_tokens=0,
    )
    db.add(AIScoringInvocation(**kwargs))
    db.commit()
    db.add(AIScoringInvocation(**kwargs))
    with pytest.raises(IntegrityError):
        db.commit()
```

- [ ] **Step 2: Run tests and confirm missing model fields/classes**

Run: `pytest tests/test_ai_scoring_models.py -v`

Expected: FAIL importing `AIScoringInvocation` or constructing the new candidate fields.

- [ ] **Step 3: Add ORM fields and invocation model**

```python
class AIScoringInvocation(Base):
    __tablename__ = "ai_scoring_invocation"
    __table_args__ = (
        UniqueConstraint(
            "ref_date", "ticker", "input_hash", "model_id", "prompt_version",
            name="uq_ai_scoring_invocation_cache_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    score_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
```

Add these exact fields to `CandidateSnapshot`; nullable score fields let readers safely fall back to
`final_score` for pre-migration fixtures while every newly generated row sets them explicitly:

```python
rule_score: Mapped[float | None] = mapped_column(Float, nullable=True)
recommendation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
score_source: Mapped[str | None] = mapped_column(String(24), nullable=True, default="RULE")
ai_scoring_mode: Mapped[str | None] = mapped_column(String(16), nullable=True, default="off")
ai_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
ai_reason_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
ai_model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

Keep `ai_technical_score` as the stored AI total.

- [ ] **Step 4: Add idempotent Alembic upgrade and downgrade**

Set `revision = "0020_ai_scoring"` and `down_revision = "0019_bt_run_source"`. In `upgrade()`, inspect existing columns/tables before adding them. Backfill existing rows before enforcing defaults:

```python
op.execute("UPDATE candidate_snapshot SET rule_score = final_score WHERE rule_score IS NULL")
op.execute(
    "UPDATE candidate_snapshot SET recommendation_score = final_score "
    "WHERE recommendation_score IS NULL"
)
op.execute("UPDATE candidate_snapshot SET score_source = 'RULE' WHERE score_source IS NULL")
op.execute("UPDATE candidate_snapshot SET ai_scoring_mode = 'off' WHERE ai_scoring_mode IS NULL")
```

Use `batch_alter_table` for SQLite-compatible downgrade and keep the revision ID below 32 characters.

- [ ] **Step 5: Run model tests and migration checks**

Run: `pytest tests/test_ai_scoring_models.py -v`

Run on a disposable database:

```powershell
$env:MAPS_DB_URL='sqlite:///./ai_scoring_migration_test.db'
alembic upgrade head
alembic upgrade head
Remove-Item -LiteralPath '.\ai_scoring_migration_test.db'
Remove-Item Env:MAPS_DB_URL
```

Expected: tests PASS; both upgrades succeed; the second is a no-op.

- [ ] **Step 6: Commit persistence changes**

```powershell
git add maps/common/models.py alembic/versions/0020_ai_scoring.py tests/test_ai_scoring_models.py
git commit -m "feat: persist AI score provenance and usage"
```

---

### Task 3: Pure scoring domain and validation

**Files:**
- Create: `maps/ai/scoring.py`
- Modify: `maps/common/exceptions.py`
- Test: `tests/test_ai_scoring_domain.py`

**Interfaces:**
- Produces: `AIStockScore.from_payload(payload, expected_strategy_ids)`.
- Produces: `AIStockScore.score_for(strategy_id) -> float`.
- Produces: `recommendation_score(mode, rule_score, ai_score, weight) -> float`.
- Raises: `AIScoringResponseError` for schema/range/strategy violations.

- [ ] **Step 1: Write failing rubric and formula tests**

```python
VALID_PAYLOAD = {
    "trend": 21, "momentum": 15, "volume": 11, "risk": 12, "timing": 10,
    "strategy_fit": [{"strategy_id": "pullback_v3", "score": 8}],
    "confidence": 0.82,
    "reason_codes": ["UPTREND", "HEALTHY_PULLBACK", "VOLUME_WEAK"],
    "contrarian_opinion": "NONE",
    "contrarian_score": None,
}


def test_ai_score_is_server_sum_not_model_total() -> None:
    score = AIStockScore.from_payload(VALID_PAYLOAD, ("pullback_v3",))
    assert score.score_for("pullback_v3") == 77.0


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("off", 70.0), ("rerank", 72.0), ("replace", 80.0)],
)
def test_recommendation_score_by_mode(mode: str, expected: float) -> None:
    assert recommendation_score(mode, rule_score=70, ai_score=80, weight=0.20) == expected


def test_missing_ai_score_falls_back_to_rule() -> None:
    assert recommendation_score("replace", rule_score=70, ai_score=None, weight=0.20) == 70
```

- [ ] **Step 2: Run tests and confirm module import failure**

Run: `pytest tests/test_ai_scoring_domain.py -v`

Expected: FAIL with `ModuleNotFoundError: maps.ai.scoring`.

- [ ] **Step 3: Implement immutable score types and recommendation formula**

```python
@dataclass(frozen=True)
class AIStockScore:
    trend: int
    momentum: int
    volume: int
    risk: int
    timing: int
    strategy_fit: dict[str, int]
    confidence: float
    reason_codes: tuple[str, ...]
    contrarian_opinion: str = "NONE"
    contrarian_score: float | None = None

    @property
    def common_score(self) -> int:
        return self.trend + self.momentum + self.volume + self.risk + self.timing

    def score_for(self, strategy_id: str) -> float:
        return float(self.common_score + self.strategy_fit[strategy_id])


def recommendation_score(
    mode: AIScoringMode, *, rule_score: float, ai_score: float | None, weight: float
) -> float:
    if ai_score is None or mode == "off":
        return round(rule_score, 2)
    if mode == "rerank":
        return round(rule_score * (1.0 - weight) + ai_score * weight, 2)
    return round(ai_score, 2)
```

- [ ] **Step 4: Write failing strict validation tests**

```python
@pytest.mark.parametrize(
    "field,value",
    [("trend", 26), ("momentum", -1), ("volume", 16), ("risk", 16), ("timing", 16)],
)
def test_score_ranges_are_rejected(field: str, value: int) -> None:
    payload = copy.deepcopy(VALID_PAYLOAD)
    payload[field] = value
    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(payload, ("pullback_v3",))


def test_strategy_fit_must_match_requested_strategies_exactly() -> None:
    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(VALID_PAYLOAD, ("pullback_v3", "donchian_v2"))


def test_reason_codes_are_bounded_and_known() -> None:
    payload = {**VALID_PAYLOAD, "reason_codes": ["UNKNOWN_CODE"]}
    with pytest.raises(AIScoringResponseError):
        AIStockScore.from_payload(payload, ("pullback_v3",))
```

- [ ] **Step 5: Add typed exceptions and strict payload validation**

Add `AIScoringError`, `AIScoringUnavailableError`, `AIScoringProviderError`, and `AIScoringResponseError` to `maps/common/exceptions.py`. Validate integer types without accepting booleans, exact strategy IDs, score bounds `25/20/15/15/15/10`, confidence `0..1`, at most three reason codes, and these codes:

```python
AI_REASON_CODES = {
    "UPTREND", "DOWNTREND", "MOMENTUM_POSITIVE", "MOMENTUM_WEAK",
    "VOLUME_CONFIRMED", "VOLUME_WEAK", "LOW_VOLATILITY", "HIGH_VOLATILITY",
    "HEALTHY_PULLBACK", "BREAKOUT_CONFIRMED", "OVEREXTENDED", "NEAR_SUPPORT",
    "RESISTANCE_OVERHEAD", "CONFLICTING_SIGNALS", "INSUFFICIENT_DATA",
}
```

- [ ] **Step 6: Run domain tests**

Run: `pytest tests/test_ai_scoring_domain.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the domain layer**

```powershell
git add maps/ai/scoring.py maps/common/exceptions.py tests/test_ai_scoring_domain.py
git commit -m "feat: define validated AI scoring rubric"
```

---

### Task 4: Compact feature builder and Bedrock Runtime adapter

**Files:**
- Rewrite: `maps/ai/technical_scorer.py`
- Rewrite tests: `tests/test_ai_technical_scorer.py`

**Interfaces:**
- Produces: `AIStockFeatures.from_frame(...) -> AIStockFeatures`.
- Produces: `AIStockFeatures.to_payload() -> dict[str, object]` and `canonical_json() -> str`.
- Produces: `BedrockScoreResponse(score, input_tokens, output_tokens, raw_payload)`.
- Produces: `AITechnicalScorer.score(features) -> BedrockScoreResponse`.
- Produces: `AITechnicalScorer.is_configured -> bool` for a no-cost preflight before budget reservation.
- Consumes: `AIStockScore.from_payload` and typed AI exceptions from Task 3.

- [ ] **Step 1: Replace legacy price-output tests with failing compact-feature tests**

```python
def test_features_use_derived_values_without_raw_ohlcv(ohlcv_df: pd.DataFrame) -> None:
    features = AIStockFeatures.from_frame(
        ticker="005930", name="삼성전자", ref_date="2026-06-18",
        frame=ohlcv_df, strategy_ids=("pullback_v3",),
        trend_strength=72.5, ts_bucket="S4",
    )
    payload = features.to_payload()
    assert payload["ticker"] == "005930"
    assert payload["strategy_ids"] == ["pullback_v3"]
    assert "rsi14" in payload and "atr_pct" in payload
    assert "recent_ohlcv" not in payload
    assert "open" not in payload and "volume" not in payload
    assert len(features.canonical_json()) < 1800


def test_features_require_sixty_bars_for_ma60() -> None:
    with pytest.raises(AIScoringResponseError):
        AIStockFeatures.from_frame(
            ticker="005930", name="삼성전자", ref_date="2026-06-18",
            frame=_make_ohlcv(n=59), strategy_ids=("pullback_v3",),
            trend_strength=72.5, ts_bucket="S4",
        )
```

- [ ] **Step 2: Run compact-feature tests and confirm missing type**

Run: `pytest tests/test_ai_technical_scorer.py::test_features_use_derived_values_without_raw_ohlcv tests/test_ai_technical_scorer.py::test_features_require_sixty_bars_for_ma60 -v`

Expected: FAIL importing `AIStockFeatures`.

- [ ] **Step 3: Implement normalized local indicators**

`AIStockFeatures` contains rounded, scale-independent values: close, RSI14, MACD histogram as percent of close, volume ratio, ATR percent, MA20/MA60 distance percent, 5/20-day return, 52-week position, MA alignment, breakout/pullback/overextended flags, trend strength, TS bucket, and sorted strategy IDs. Serialize with:

```python
json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

- [ ] **Step 4: Write failing request-body and response tests**

```python
def test_bedrock_request_uses_structured_output_and_low_effort(scorer, features) -> None:
    body = scorer._request_body(features)
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["max_tokens"] == 1024
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_static_system_prompt_does_not_contain_ticker_or_name(scorer, features) -> None:
    system = scorer._system_prompt()
    assert features.ticker not in system
    assert features.name not in system
    assert len(system + features.canonical_json()) < 5000


def test_score_parses_usage_and_validated_payload(scorer, features, monkeypatch) -> None:
    monkeypatch.setattr(scorer, "_invoke", lambda _body: ({"content": [{"text": json.dumps(VALID_PAYLOAD)}], "usage": {"input_tokens": 410, "output_tokens": 86}}))
    result = scorer.score(features)
    assert result.score.score_for("pullback_v3") == 77
    assert result.input_tokens == 410
    assert result.output_tokens == 86
```

- [ ] **Step 5: Implement static rubric, stable JSON Schema, and runtime call**

Use an array for `strategy_fit` so the schema stays identical across strategy combinations:

```json
{"strategy_fit":[{"strategy_id":"pullback_v3","score":8}]}
```

Create the boto3 client with no SDK retry:

```python
config = Config(
    connect_timeout=min(10.0, self._timeout_seconds),
    read_timeout=self._timeout_seconds,
    retries={"max_attempts": 0, "mode": "standard"},
)
client = boto3.client("bedrock-runtime", region_name=self._region, config=config, **credentials)
```

Invoke `invoke_model` with the configured model and a JSON body containing static system instructions, one compact user payload, adaptive thinking, low effort, and JSON Schema output. Find the first response content block whose `type` is `text` instead of assuming it is the first block, because adaptive thinking may precede it. Convert provider, refusal, empty-content, and JSON errors into typed exceptions without logging raw payloads. `is_configured` is true only when both configured access-key fields are present.

- [ ] **Step 6: Add explicit missing-credentials and no-retry tests**

```python
def test_missing_credentials_raise_unavailable(features) -> None:
    scorer = AITechnicalScorer(aws_access_key_id="", aws_secret_access_key="")
    with pytest.raises(AIScoringUnavailableError):
        scorer.score(features)


def test_provider_error_is_not_retried(scorer, features, monkeypatch) -> None:
    invoke = Mock(side_effect=TimeoutError("timeout"))
    monkeypatch.setattr(scorer, "_invoke", invoke)
    with pytest.raises(AIScoringProviderError):
        scorer.score(features)
    assert invoke.call_count == 1
```

- [ ] **Step 7: Run adapter tests**

Run: `pytest tests/test_ai_technical_scorer.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the Bedrock adapter**

```powershell
git add maps/ai/technical_scorer.py tests/test_ai_technical_scorer.py
git commit -m "feat: add compact Bedrock AI scorer"
```

---

### Task 5: Durable cache, global budget, and ticker deduplication

**Files:**
- Create: `maps/ai/scoring_service.py`
- Create: `tests/test_ai_scoring_service.py`

**Interfaces:**
- Produces: `AIScoringRunSummary(targets, calls, cache_hits, successes, failures, skipped_limit, input_tokens, output_tokens)`.
- Produces: `AIStockScoringService.apply(db: Session, ref_date: date, frames: Mapping[str, pd.DataFrame], active_strategy_ids: set[str]) -> AIScoringRunSummary`.
- Consumes: persisted rule-only `CandidateSnapshot` rows and `AITechnicalScorer` from Task 4.

- [ ] **Step 1: Write failing target-selection and deduplication tests**

```python
def test_service_scores_one_ticker_once_across_strategies(db, frames, scorer) -> None:
    seed_candidate(db, ticker="005930", strategy_id="pullback_v3", rule_score=80, entry_signal=True)
    seed_candidate(db, ticker="005930", strategy_id="donchian_v2", rule_score=75, entry_signal=True)
    service = AIStockScoringService(settings=rerank_settings(limit=5), scorer=scorer)
    summary = service.apply(
        db, dt.date(2026, 8, 7), frames, {"pullback_v3", "donchian_v2"}
    )
    assert summary.calls == 1
    assert scorer.calls[0].strategy_ids == ("donchian_v2", "pullback_v3")
    assert {row.score_source for row in db.query(CandidateSnapshot)} == {"AI"}


def test_service_only_targets_rule_eligible_signals(db, frames, scorer) -> None:
    seed_candidate(db, ticker="SIGNAL", rule_score=80, entry_signal=True, weekly_pass=True)
    seed_candidate(db, ticker="NO_SIGNAL", rule_score=99, entry_signal=False, weekly_pass=True)
    seed_candidate(db, ticker="BLOCKED", rule_score=98, entry_signal=True, weekly_pass=False)
    service = AIStockScoringService(settings=rerank_settings(limit=5), scorer=scorer)
    service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    assert [call.ticker for call in scorer.calls] == ["SIGNAL"]


def test_unconfigured_scorer_consumes_no_budget_or_network_call(db, frames) -> None:
    seed_candidate(db, ticker="005930", rule_score=80, entry_signal=True)
    scorer = FakeScorer(configured=False)
    summary = AIStockScoringService(settings=rerank_settings(limit=5), scorer=scorer).apply(
        db, dt.date(2026, 8, 7), frames, {"pullback_v3"}
    )
    row = db.query(CandidateSnapshot).one()
    assert summary.calls == 0
    assert db.query(AIScoringInvocation).count() == 0
    assert row.score_source == "RULE"
    assert row.ai_status == "SKIPPED_UNCONFIGURED"
```

- [ ] **Step 2: Run tests and confirm missing service**

Run: `pytest tests/test_ai_scoring_service.py::test_service_scores_one_ticker_once_across_strategies tests/test_ai_scoring_service.py::test_service_only_targets_rule_eligible_signals -v`

Expected: FAIL importing `AIStockScoringService`.

- [ ] **Step 3: Implement target grouping and mode application**

Query same-day rows satisfying `entry_signal IS TRUE`, `weekly_pass IS TRUE`, no `excluded_reason`, active strategy membership, and `coalesce(rule_score, final_score) >= maps_candidate_min_score`. Group by ticker, rank each ticker by maximum rule score, and select up to the remaining global budget. Initialize every row with `recommendation_score=rule_score`, `final_score=rule_score`, `score_source="RULE"`, and the snapshot mode. Before creating any invocation reservation, check `scorer.is_configured`; when false, mark eligible rows `SKIPPED_UNCONFIGURED`, return zero calls, and create no `AIScoringInvocation` rows.

On success, update each strategy row with:

```python
ai_score = result.score.score_for(row.strategy_id)
row.ai_technical_score = ai_score
row.recommendation_score = recommendation_score(
    mode, rule_score=row.rule_score, ai_score=ai_score, weight=settings.maps_ai_rerank_weight
)
row.final_score = row.recommendation_score
row.score_source = "AI"
row.ai_status = "SUCCESS"
```

- [ ] **Step 4: Write failing durable budget and cache tests**

```python
def test_daily_limit_counts_failed_calls_and_marks_remaining_rows(db, frames, failing_scorer) -> None:
    for index in range(7):
        seed_candidate(db, ticker=f"T{index}", rule_score=100-index, entry_signal=True)
    service = AIStockScoringService(settings=replace_settings(limit=5), scorer=failing_scorer)
    summary = service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    assert summary.calls == 5
    assert summary.failures == 5
    assert summary.skipped_limit == 2
    assert db.query(AIScoringInvocation).filter(AIScoringInvocation.status == "FAILED").count() == 5


def test_success_cache_is_reused_without_new_call(db, frames, scorer) -> None:
    seed_candidate(db, ticker="005930", rule_score=80, entry_signal=True)
    service = AIStockScoringService(settings=rerank_settings(limit=5), scorer=scorer)
    first = service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    second = service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    assert first.calls == 1
    assert second.calls == 0
    assert second.cache_hits == 1
    assert len(scorer.calls) == 1


def test_failed_cache_is_not_retried_same_day(db, frames, failing_scorer) -> None:
    seed_candidate(db, ticker="005930", rule_score=80, entry_signal=True)
    service = AIStockScoringService(settings=rerank_settings(limit=5), scorer=failing_scorer)
    service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    service.apply(db, dt.date(2026, 8, 7), frames, {"pullback_v3"})
    assert len(failing_scorer.calls) == 1
```

- [ ] **Step 5: Implement cache hashing and pre-call reservation**

Hash `features.canonical_json()` with SHA-256. Before the network call, insert an invocation row with `status="STARTED"` and commit it; STARTED, SUCCESS, and FAILED all count against the date's budget. On success store `score_payload` and usage. On a typed error update the reservation to FAILED with only the exception class name as `error_code`, mark candidate rows `RULE_FALLBACK`, and preserve rule scores.

- [ ] **Step 6: Write failing replace/rerank limit semantics tests**

```python
def test_rerank_limit_rows_remain_recommendation_eligible(db, frames, scorer) -> None:
    seed_seven_ranked_candidates(db)
    AIStockScoringService(settings=rerank_settings(limit=5), scorer=scorer).apply(
        db, REF_DATE, frames, {"pullback_v3"}
    )
    skipped = db.query(CandidateSnapshot).filter(CandidateSnapshot.ai_status == "SKIPPED_LIMIT").all()
    assert len(skipped) == 2
    assert all(row.ai_scoring_mode == "rerank" and row.score_source == "RULE" for row in skipped)


def test_replace_limit_rows_are_marked_for_order_exclusion(db, frames, scorer) -> None:
    seed_seven_ranked_candidates(db)
    AIStockScoringService(settings=replace_settings(limit=5), scorer=scorer).apply(
        db, REF_DATE, frames, {"pullback_v3"}
    )
    skipped = db.query(CandidateSnapshot).filter(CandidateSnapshot.ai_status == "SKIPPED_LIMIT").all()
    assert len(skipped) == 2
    assert all(row.ai_scoring_mode == "replace" for row in skipped)
```

- [ ] **Step 7: Map the optional combined contrarian result without a second call**

When `maps_ai_contrarian_check_enabled` and `maps_ai_analysis_mode == "all"`, ask the same response for `contrarian_opinion` and `contrarian_score`, copy them into existing snapshot columns, and recalculate `holding_type` with `HoldingTypeClassifier`. Do not call `AIContrarianAnalyzer` from this service. In `technical_only`, store no contrarian fields.

- [ ] **Step 8: Run service tests**

Run: `pytest tests/test_ai_scoring_service.py -v`

Expected: PASS.

- [ ] **Step 9: Commit the service**

```powershell
git add maps/ai/scoring_service.py tests/test_ai_scoring_service.py
git commit -m "feat: enforce durable AI scoring budget"
```

---

### Task 6: Candidate-generation integration

**Files:**
- Modify: `maps/ops/scheduler.py:305-470`
- Modify: `maps/ops/scheduler.py:1413-1800`
- Create: `tests/test_ai_scoring_scheduler.py`
- Modify: `tests/test_candidate_snapshot_scheduler.py`
- Modify: `tests/test_candidate_funnel.py`

**Interfaces:**
- `_save_candidate_snapshot(...)` continues returning stored row count for existing callers.
- It now persists rule-only rows with provenance fields and makes zero Bedrock calls.
- `generate_candidates()` calls `AIStockScoringService.apply(...)` once after all strategies are saved.

- [ ] **Step 1: Write a failing rule-only snapshot test**

```python
def test_save_candidate_snapshot_never_calls_bedrock(monkeypatch, db, pipeline, universe, contexts) -> None:
    monkeypatch.setattr(
        "maps.ai.technical_scorer.AITechnicalScorer.score",
        Mock(side_effect=AssertionError("Bedrock must run after all strategies")),
    )
    pipeline._save_candidate_snapshot(
        db, REF_DATE, "pullback_v3", universe, contexts=contexts
    )
    row = db.query(CandidateSnapshot).filter_by(strategy_id="pullback_v3").first()
    assert row.rule_score == row.recommendation_score == row.final_score
    assert row.score_source == "RULE"
    assert row.ai_technical_score is None
```

- [ ] **Step 2: Run the test and confirm current Bedrock path is reached**

Run: `pytest tests/test_ai_scoring_scheduler.py::test_save_candidate_snapshot_never_calls_bedrock -v`

Expected: FAIL because `_save_candidate_snapshot` constructs/calls the current AI scorer when legacy AI is enabled.

- [ ] **Step 3: Remove per-strategy AI pre-scoring and calls**

Delete `ai_target_tickers`, the duplicate pre-score loop, `AITechnicalScorer.from_settings()`, and the scheduler's `AIContrarianAnalyzer` invocation. Calculate the existing scoring formula with `ai_weight=0.0`, store it as `rule_score`, `recommendation_score`, and `final_score`, and keep rule-based buy/stop/target values in the existing price columns.

- [ ] **Step 4: Write a failing one-global-pass integration test**

```python
def test_generate_candidates_applies_one_global_ai_pass(monkeypatch, pipeline) -> None:
    apply = Mock(return_value=AIScoringRunSummary(
        targets=2, calls=2, cache_hits=0, successes=2, failures=0,
        skipped_limit=0, input_tokens=800, output_tokens=160,
    ))
    monkeypatch.setattr("maps.ops.scheduler.AIStockScoringService.apply", apply)
    run = pipeline.generate_candidates(REF_DATE)
    assert apply.call_count == 1
    assert run.details["ai_calls"] == 2
    assert run.details["ai_input_tokens"] == 800
    assert run.details["ai_output_tokens"] == 160
```

- [ ] **Step 5: Call the scoring service after all strategies**

Build `frames = {ticker: context.frame for ticker, context in ticker_contexts.items()}` and pass the active strategy IDs. Add summary fields to job details and one INFO log:

```text
AI scoring: mode=rerank model=... targets=2 calls=2 cache_hits=0 success=2 failed=0 skipped_limit=0 input_tokens=800 output_tokens=160
```

If mode is off, do not instantiate a Bedrock client; return a zero-valued summary.

- [ ] **Step 6: Preserve existing funnel behavior and update assertions**

Adjust candidate tests so stored row counts, signal gating, one OHLCV load per ticker, and replace-day-strategy behavior remain unchanged. Add an assertion that legacy `AIContrarianAnalyzer._call_bedrock` is never invoked during candidate generation.

- [ ] **Step 7: Run scheduler and funnel tests**

Run: `pytest tests/test_ai_scoring_scheduler.py tests/test_candidate_snapshot_scheduler.py tests/test_candidate_funnel.py -v`

Expected: PASS.

- [ ] **Step 8: Commit scheduler integration**

```powershell
git add maps/ops/scheduler.py tests/test_ai_scoring_scheduler.py tests/test_candidate_snapshot_scheduler.py tests/test_candidate_funnel.py
git commit -m "feat: apply AI scoring after candidate funnel"
```

---

### Task 7: Shared order eligibility for rerank and replace

**Files:**
- Create: `maps/ops/candidate_selection.py`
- Modify: `maps/ops/scheduler.py:2483-2530`
- Modify: `maps/ops/order_preview.py:61-85`
- Modify: `tests/test_candidate_snapshot_scheduler.py`
- Modify: `tests/test_order_preview.py`

**Interfaces:**
- Produces: `candidate_min_score_expression() -> ColumnElement[float]`.
- Produces: `candidate_recommendation_eligible_expression() -> ColumnElement[bool]`.
- Both live order selection and preview consume the same expressions.

- [ ] **Step 1: Write failing rerank and replace order tests**

```python
def test_rerank_uses_rule_score_for_minimum_but_recommendation_for_order(db, pipeline) -> None:
    seed_ai_candidate(db, ticker="A", mode="rerank", rule_score=11, recommendation_score=2)
    seed_ai_candidate(db, ticker="B", mode="rerank", rule_score=12, recommendation_score=9)
    rows = pipeline._order_candidates(db, dt.date.today())
    assert [row.ticker for row in rows] == ["B", "A"]


def test_rerank_low_rule_score_is_not_rescued_by_ai(db, pipeline) -> None:
    seed_ai_candidate(db, ticker="A", mode="rerank", rule_score=9, recommendation_score=99)
    assert pipeline._order_candidates(db, dt.date.today()) == []


def test_replace_skipped_limit_is_excluded_from_orders(db, pipeline) -> None:
    seed_ai_candidate(
        db, ticker="A", mode="replace", rule_score=90,
        recommendation_score=90, ai_status="SKIPPED_LIMIT",
    )
    assert pipeline._order_candidates(db, dt.date.today()) == []
```

- [ ] **Step 2: Run tests and confirm current `final_score` query is wrong**

Run: `pytest tests/test_candidate_snapshot_scheduler.py -k "rerank or replace_skipped" -v`

Expected: at least one FAIL because the current query filters only `final_score`.

- [ ] **Step 3: Implement shared SQL expressions**

```python
def candidate_min_score_expression():
    rule = func.coalesce(CandidateSnapshot.rule_score, CandidateSnapshot.final_score)
    return case(
        (CandidateSnapshot.ai_scoring_mode == "rerank", rule),
        else_=CandidateSnapshot.final_score,
    )


def candidate_recommendation_eligible_expression():
    return or_(
        CandidateSnapshot.ai_scoring_mode != "replace",
        CandidateSnapshot.ai_status.is_(None),
        CandidateSnapshot.ai_status != "SKIPPED_LIMIT",
    )
```

Use these expressions in `_order_candidates` and `_get_order_candidates`. Keep ordering by `final_score DESC, trend_strength DESC` because `final_score` is the recommendation score.

- [ ] **Step 4: Add matching preview tests**

Seed the same three cases through `build_order_preview` and assert the preview and actual order pipeline return the same tickers in the same order.

- [ ] **Step 5: Run order tests**

Run: `pytest tests/test_candidate_snapshot_scheduler.py tests/test_order_preview.py -v`

Expected: PASS.

- [ ] **Step 6: Commit shared order selection**

```powershell
git add maps/ops/candidate_selection.py maps/ops/scheduler.py maps/ops/order_preview.py tests/test_candidate_snapshot_scheduler.py tests/test_order_preview.py
git commit -m "fix: preserve rule gates under AI reranking"
```

---

### Task 8: Candidate API, source badges, and operator visibility

**Files:**
- Modify: `maps/api/schemas.py:135-175`
- Modify: `maps/api/candidates.py:16-105`
- Modify: `static/js/app.js:385-480`
- Modify: `templates/candidates.html`
- Modify: `maps/common/settings.py:360-385`
- Modify: `docs/OPERATIONS_CONFIG.md`
- Test: `tests/test_candidates_api.py`
- Create: `tests/test_candidates_ui.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- API adds `rule_score`, `ai_score`, `recommendation_score`, `score_source`, `ai_scoring_mode`, `ai_status`, `ai_confidence`, `ai_reason_codes`, and `ai_model_id` without removing legacy fields.
- Candidate UI treats existing price fields as rule-based plans, not AI-generated prices.

- [ ] **Step 1: Write failing API provenance test**

```python
def test_candidates_exposes_score_provenance(ctx) -> None:
    client, factory = ctx
    seed_candidate_with_scores(
        factory, rule_score=70, ai_score=80, recommendation_score=72,
        source="AI", mode="rerank", confidence=0.82,
        reason_codes=["UPTREND", "HEALTHY_PULLBACK"],
    )
    item = client.get("/api/v1/candidates").json()["candidates"][0]
    assert item["rule_score"] == 70
    assert item["ai_score"] == 80
    assert item["recommendation_score"] == item["final_score"] == 72
    assert item["score_source"] == "AI"
    assert item["ai_scoring_mode"] == "rerank"
```

- [ ] **Step 2: Run API test and confirm missing response fields**

Run: `pytest tests/test_candidates_api.py::test_candidates_exposes_score_provenance -v`

Expected: FAIL because the fields are absent.

- [ ] **Step 3: Extend schemas and mapping with legacy fallback**

Map `rule_score` and `recommendation_score` with `row.final_score` fallback for old rows. Expose `ai_score` from `row.ai_technical_score`. Keep the existing `ai_technical_score` response field during compatibility.

- [ ] **Step 4: Write failing static UI assertions**

```python
def test_candidate_ui_contains_score_source_badges() -> None:
    script = Path("static/js/app.js").read_text(encoding="utf-8")
    assert "RULE_FALLBACK" in script
    assert "규칙점수" in script
    assert "추천점수" in script
    assert "AI점수" in script
    assert "AI 적정 매수가" not in script
```

- [ ] **Step 5: Update the candidate table and legend**

Render a source badge beside recommendation score:

- `RULE` → neutral `규칙`
- `AI` → info `AI 적용`
- `RULE_FALLBACK` → warning `규칙 대체`

Show separate rule, AI, and recommendation columns. Show confidence as a percentage and translate reason codes with a fixed client map. Rename price columns to `계획 매수가`, `계획 손절가`, and `계획 목표가`; remove claims that Claude generated those prices.

- [ ] **Step 6: Add settings visibility and operations documentation**

Expose the five new environment variables through `get_config_status`. Document mode semantics, global limit, Sonnet default, Haiku override, no retry/cache behavior, token-log fields, and the rollout order `off -> rerank -> replace` in `docs/OPERATIONS_CONFIG.md`.

- [ ] **Step 7: Run API, UI, and configuration tests**

Run: `pytest tests/test_candidates_api.py tests/test_candidates_ui.py tests/test_settings.py tests/test_ops_config_api.py -v`

Expected: PASS.

- [ ] **Step 8: Commit user-visible scoring provenance**

```powershell
git add maps/api/schemas.py maps/api/candidates.py static/js/app.js templates/candidates.html maps/common/settings.py docs/OPERATIONS_CONFIG.md tests/test_candidates_api.py tests/test_candidates_ui.py tests/test_settings.py
git commit -m "feat: show AI score source on candidates"
```

---

### Task 9: Haiku-versus-Sonnet evaluation harness

**Files:**
- Create: `maps/ai/evaluation.py`
- Create: `scripts/evaluate_ai_scoring_models.py`
- Create: `tests/test_ai_scoring_evaluation.py`

**Interfaces:**
- Produces: `EvaluationSample`, `ModelObservation`, `ModelEvaluationSummary`.
- Produces: `summarize_observations(observations) -> list[ModelEvaluationSummary]`.
- CLI defaults to dry-run and requires `--execute` before making billable calls.

- [ ] **Step 1: Write failing pure metric tests**

```python
def test_evaluation_summary_calculates_success_variance_tokens_and_latency() -> None:
    observations = [
        ModelObservation("sonnet", "005930", 78, True, 400, 80, 1.2),
        ModelObservation("sonnet", "005930", 80, True, 402, 82, 1.1),
        ModelObservation("haiku", "005930", None, False, 390, 20, 0.4),
    ]
    summaries = {item.model_id: item for item in summarize_observations(observations)}
    assert summaries["sonnet"].schema_success_rate == 1.0
    assert summaries["sonnet"].score_stddev == pytest.approx(1.0)
    assert summaries["sonnet"].input_tokens == 802
    assert summaries["haiku"].schema_success_rate == 0.0
```

- [ ] **Step 2: Run test and confirm missing evaluation module**

Run: `pytest tests/test_ai_scoring_evaluation.py -v`

Expected: FAIL importing `maps.ai.evaluation`.

- [ ] **Step 3: Implement immutable observations and summaries**

Use `statistics.pstdev` per model/ticker and aggregate schema success, mean score variance, input/output tokens, and mean latency. Keep this module provider-free and deterministic.

- [ ] **Step 4: Add guarded CLI tests**

```python
def test_cli_dry_run_makes_no_model_calls(monkeypatch, capsys) -> None:
    call = Mock(side_effect=AssertionError("billable call"))
    monkeypatch.setattr("scripts.evaluate_ai_scoring_models.run_live_evaluation", call)
    assert main(["--ref-date", "2026-08-07", "--sample-size", "5"]) == 0
    assert call.call_count == 0
    assert "20 planned calls" in capsys.readouterr().out
```

- [ ] **Step 5: Implement the live-comparison CLI**

Arguments:

```text
--ref-date YYYY-MM-DD
--sample-size 5
--repeats 2
--models us.anthropic.claude-sonnet-4-6 us.anthropic.claude-haiku-4-5-20251001-v1:0
--execute
--output logs/ai-scoring-evaluation-YYYY-MM-DD.json
```

Select entry-signal snapshots for the reference date, load each ticker's OHLCV frame once, construct the same `AIStockFeatures`, and call each model for each repeat. Print the planned billable call count before execution. Refuse execution when AWS credentials are absent. Write only features, scores, metrics, token counts, latency, and error classes; never write credentials or raw provider payloads.

- [ ] **Step 6: Run evaluation tests**

Run: `pytest tests/test_ai_scoring_evaluation.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the evaluation harness**

```powershell
git add maps/ai/evaluation.py scripts/evaluate_ai_scoring_models.py tests/test_ai_scoring_evaluation.py
git commit -m "feat: compare AI scoring model consistency"
```

---

### Task 10: Migration, regression, and rollout verification

**Files:**
- Modify only if failures reveal an in-scope defect in files changed by Tasks 1-9.
- Verify: all files above plus `docs/superpowers/specs/2026-08-07-ai-scoring-design.md`.

**Interfaces:**
- Produces: a migration-clean, fully tested feature that remains disabled by default.

- [ ] **Step 1: Run focused AI scoring tests**

Run:

```powershell
pytest tests/test_ai_scoring_models.py tests/test_ai_scoring_domain.py tests/test_ai_technical_scorer.py tests/test_ai_scoring_service.py tests/test_ai_scoring_scheduler.py tests/test_ai_scoring_evaluation.py -v
```

Expected: all PASS.

- [ ] **Step 2: Run candidate and order regression tests**

Run:

```powershell
pytest tests/test_candidate_funnel.py tests/test_candidate_snapshot_scheduler.py tests/test_candidates_api.py tests/test_candidates_ui.py tests/test_order_preview.py tests/test_settings.py tests/test_ops_config_api.py -v
```

Expected: all PASS.

- [ ] **Step 3: Verify a fresh SQLite migration twice**

```powershell
$env:MAPS_DB_URL='sqlite:///./ai_scoring_verify.db'
alembic upgrade head
alembic upgrade head
alembic current
Remove-Item -LiteralPath '.\ai_scoring_verify.db'
Remove-Item Env:MAPS_DB_URL
```

Expected: `0020_ai_scoring (head)` and no second-run error.

- [ ] **Step 4: Run the full suite**

Run: `pytest --tb=short`

Expected: all tests PASS.

- [ ] **Step 5: Inspect the final diff and repository state**

Run:

```powershell
git status --short
git diff --check
git log --oneline -10
```

Expected: no whitespace errors; only the pre-existing user-owned `HANDOFF.md` change may remain unstaged.

- [ ] **Step 6: Perform a no-cost dry run of the evaluation CLI**

Run:

```powershell
python scripts/evaluate_ai_scoring_models.py --ref-date 2026-08-07 --sample-size 5 --repeats 2
```

Expected: prints the planned call count and makes no Bedrock calls because `--execute` is absent.

- [ ] **Step 7: Commit any final in-scope corrections**

If Steps 1-6 required code corrections, stage only those specific AI Scoring files and commit:

```powershell
git commit -m "fix: complete AI scoring rollout safeguards"
```

If no corrections were needed, do not create an empty commit.

---

## Post-Implementation Operational Rollout

These are deployment/operator actions, not automatic implementation steps.

1. Deploy with `MAPS_AI_SCORING_MODE=off` and run the normal candidate job once.
2. Confirm migration head, unchanged rule scores, and zero AI calls.
3. Set `MAPS_AI_SCORING_MODE=rerank` and `MAPS_AI_DAILY_CALL_LIMIT=5`.
4. Confirm each candidate job log reports no more than five calls and records input/output tokens.
5. Run the model evaluation CLI only with explicit operator approval for billable calls.
6. Observe recommendation ordering and fallback rate before enabling `replace`.
7. Enable `replace` only by an explicit environment change; never promote automatically from model benchmarks.
