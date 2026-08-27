# 자동매수 유동성 하한 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 얇은 종목을 하루 거래대금 대비 과도한 금액으로 자동매수하는 것을 막는다.

**Architecture:** "20거래일 평균 거래대금"을 계산하는 곳을 레포지터리 함수 하나로 모으고,
유니버스 필터와 주문 경로가 그 하나를 쓴다. 주문 시점 한도는 순수 함수 하나로 두고 실제
주문 경로와 주문 미리보기가 공유한다.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI/Pydantic, Jinja/vanilla JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-order-liquidity-cap-design.md`

## Global Constraints

- DB 마이그레이션, 과거 데이터 백필, 새 테이블을 만들지 않는다.
- 매도 주문에는 유동성 한도를 적용하지 않는다.
- 전략매매(`analysis_pick`) 경로는 이번 범위가 아니다.
- 축소·차단은 반드시 사유가 화면과 다이제스트에 드러나야 한다. 조용히 넘어가면 안 된다.
- `MAPS_ORDER_MAX_TURNOVER_PCT` 기본값은 `0.02`, `MAPS_ORDER_MIN_AMOUNT_KRW` 기본값은 `500000`.
- 20거래일 창은 `ref_date`를 **포함**한다. `ref_date` 이후 데이터는 쓰지 않는다.
- 20거래일치 봉이 없는 종목은 부분 평균을 쓰지 않고 **결과에서 제외**한다.
- 현재 작업공간의 기존 삭제·미추적 파일을 수정하거나 커밋하지 않는다.

## File Structure

| 파일 | 책임 |
|---|---|
| `maps/data/ohlcv_repo.py` | `avg_turnover_20d()` — "20거래일 평균 거래대금"의 유일한 SQL 정의 |
| `maps/ops/liquidity_cap.py` (신규) | 주문 수량 한도 순수 함수와 결과 타입 |
| `maps/common/settings.py` | 새 설정 2개 |
| `maps/ops/scheduler.py` | 유니버스 turnover 주입(G1), 매수 주문 수량에 한도 적용(G2) |
| `maps/ops/order_preview.py` | 미리보기에 같은 한도 적용 |
| `maps/api/schemas.py` | 미리보기 행에 축소 정보 필드, 다이제스트 집계 필드 |
| `maps/ops/daily_digest.py` | 축소·차단 집계 |
| `templates/orders.html`, `static/js/app.js` | 축소 사유 표시 |
| `.claude/commands/blog.md` | 매매일지 서술 규칙 |
| `maps/data/CLAUDE.md`, `maps/ops/CLAUDE.md` | 패키지 문서 정합 |

---

### Task 1: 20거래일 평균 거래대금 레포지터리 함수

**Files:**
- Modify: `maps/data/ohlcv_repo.py` (`HistoricalOHLCVRepository`에 메서드 추가)
- Test: `tests/test_ohlcv_repo.py`

**Interfaces:**
- Consumes: 없음
- Produces: `HistoricalOHLCVRepository.avg_turnover_20d(tickers: list[str], as_of: dt.date) -> dict[str, float]`
  — `as_of`를 포함한 직전 20거래일의 `avg(close * volume)`. 봉이 20개 미만인 티커는
  **딕셔너리에 넣지 않는다.**

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
import datetime as dt

from maps.common.models import HistoricalOHLCV
from maps.data.ohlcv_repo import HistoricalOHLCVRepository


def _bar(db, ticker: str, date: dt.date, close: float, volume: int) -> None:
    db.add(
        HistoricalOHLCV(
            ticker=ticker, date=date, open=close, high=close,
            low=close, close=close, volume=volume, source="test",
        )
    )


def test_avg_turnover_20d_uses_twenty_bars_not_one(db_session) -> None:
    """하루만 급등한 종목이 20일 평균으로는 하한에 못 미치는 것을 잡는다."""
    base = dt.date(2026, 7, 1)
    days = [base + dt.timedelta(days=i) for i in range(20)]
    # 19일은 1,000만원, 마지막 하루만 3억 4천만원
    for d in days[:19]:
        _bar(db_session, "195990", d, close=1000.0, volume=10_000)
    _bar(db_session, "195990", days[19], close=1400.0, volume=242_857)
    db_session.commit()

    repo = HistoricalOHLCVRepository(db_session)
    result = repo.avg_turnover_20d(["195990"], days[19])

    assert "195990" in result
    # 19*1,000만 + 1*3.4억 을 20으로 나누면 약 2,650만 — 코스닥 하한 3억에 한참 못 미친다
    assert result["195990"] < 300_000_000
    assert 25_000_000 < result["195990"] < 28_000_000


def test_avg_turnover_20d_excludes_ticker_with_short_history(db_session) -> None:
    """봉이 20개가 안 되면 부분 평균을 주지 않고 아예 제외한다."""
    base = dt.date(2026, 7, 1)
    for i in range(19):
        _bar(db_session, "000001", base + dt.timedelta(days=i), close=1000.0, volume=10_000)
    db_session.commit()

    repo = HistoricalOHLCVRepository(db_session)
    assert repo.avg_turnover_20d(["000001"], base + dt.timedelta(days=18)) == {}


def test_avg_turnover_20d_ignores_bars_after_as_of(db_session) -> None:
    """as_of 이후 봉은 쓰지 않는다 — as-of-date 생성기 제약."""
    base = dt.date(2026, 7, 1)
    for i in range(20):
        _bar(db_session, "000002", base + dt.timedelta(days=i), close=1000.0, volume=10_000)
    # as_of 다음 날 거래대금이 폭증해도 반영되면 안 된다
    _bar(db_session, "000002", base + dt.timedelta(days=20), close=1000.0, volume=10_000_000)
    db_session.commit()

    repo = HistoricalOHLCVRepository(db_session)
    result = repo.avg_turnover_20d(["000002"], base + dt.timedelta(days=19))
    assert result["000002"] == 10_000_000.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_ohlcv_repo.py -k avg_turnover_20d -v`
Expected: FAIL — `AttributeError: 'HistoricalOHLCVRepository' object has no attribute 'avg_turnover_20d'`

> `tests/test_ohlcv_repo.py`가 없으면 새로 만든다. `db_session` 픽스처는
> `tests/conftest.py`에 이미 있다 — 다른 테스트 파일에서 쓰는 이름을 그대로 따른다.

- [ ] **Step 3: 최소 구현**

`maps/data/ohlcv_repo.py`의 `top_tickers_by_trading_value` 바로 아래에 넣는다.

```python
    def avg_turnover_20d(
        self, tickers: list[str], as_of: dt.date
    ) -> dict[str, float]:
        """``as_of``를 포함한 직전 20거래일의 평균 거래대금(종가×거래량).

        유동성 판정의 **유일한 정의**다. 유니버스 필터와 주문 경로가 같은 값을
        보도록 여기 한 곳에서만 계산한다. 봉이 20개 미만인 종목은 부분 평균을
        주지 않고 결과에서 제외한다 — 거래정지·수집 누락 구간의 부분 평균을
        정상 유동성으로 인정하지 않기 위해서다.
        """
        if not tickers:
            return {}
        ranked = (
            self._db.query(
                HistoricalOHLCV.ticker.label("ticker"),
                (HistoricalOHLCV.close * HistoricalOHLCV.volume).label("turnover"),
                func.row_number()
                .over(
                    partition_by=HistoricalOHLCV.ticker,
                    order_by=HistoricalOHLCV.date.desc(),
                )
                .label("rn"),
            )
            .filter(
                HistoricalOHLCV.ticker.in_(tickers),
                HistoricalOHLCV.date <= as_of,
            )
            .subquery()
        )
        rows = (
            self._db.query(
                ranked.c.ticker,
                func.avg(ranked.c.turnover),
                func.count(ranked.c.turnover),
            )
            .filter(ranked.c.rn <= 20)
            .group_by(ranked.c.ticker)
            .all()
        )
        return {
            ticker: float(avg)
            for ticker, avg, count in rows
            if count >= 20 and avg is not None
        }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/test_ohlcv_repo.py -k avg_turnover_20d -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add maps/data/ohlcv_repo.py tests/test_ohlcv_repo.py
git commit -m "feat: add 20-day average turnover repository query"
```

---

### Task 2: 유니버스 유동성 값을 20일 평균으로 교체 (G1)

**Files:**
- Modify: `maps/ops/scheduler.py:1406-1452` (`_to_securities`)
- Test: `tests/test_candidate_snapshot_scheduler.py`

**Interfaces:**
- Consumes: `HistoricalOHLCVRepository.avg_turnover_20d(tickers, as_of) -> dict[str, float]`
- Produces: `Security.turnover_cache[ref_date]`가 20거래일 평균 거래대금이 된다.
  `avg_turnover_20d_as_of()` 소비자(`universe_filter`, `scheduler`의 유동성 정렬)는
  코드 변경 없이 올바른 값을 받는다.

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
def test_universe_turnover_uses_twenty_day_average(db_session, pipeline) -> None:
    """하루 급등 거래대금으로 유니버스를 통과하던 회귀를 막는다.

    2026-08-20 195990: 8/20 하루치 3.36억(코스닥 하한 3억 통과)이지만
    20일 평균은 3,760만으로 하한의 1/8이었다.
    """
    ref_date = dt.date(2026, 8, 20)
    for i in range(19):
        _bar(db_session, "195990", ref_date - dt.timedelta(days=19 - i),
             close=1000.0, volume=10_000)          # 하루 1,000만원
    _bar(db_session, "195990", ref_date, close=1400.0, volume=240_000)  # 하루 3.36억
    db_session.commit()

    securities = pipeline._to_securities(db_session, meta, collection, ref_date)
    target = next(s for s in securities if s.ticker == "195990")

    assert target.avg_turnover_20d_as_of(ref_date) < 300_000_000


def test_universe_turnover_is_zero_when_history_missing(db_session, pipeline) -> None:
    """20거래일 이력이 없으면 0 이라 유동성 필터가 걸러낸다(fail-closed)."""
    ref_date = dt.date(2026, 8, 20)
    securities = pipeline._to_securities(db_session, meta, collection, ref_date)
    target = next(s for s in securities if s.ticker == "195990")

    assert target.avg_turnover_20d_as_of(ref_date) == 0.0
```

> `meta`, `collection`, `pipeline`, `_bar`는 이 파일의 기존 헬퍼·픽스처를 그대로 쓴다.
> 없으면 파일 안의 다른 테스트가 `OperationalPipeline`과 `CollectionResult`를 어떻게
> 만드는지 보고 같은 방식으로 만든다.

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_candidate_snapshot_scheduler.py -k turnover -v`
Expected: FAIL — 현재는 당일 하루치(3.36억)를 그대로 넣으므로 첫 테스트가 실패한다.

- [ ] **Step 3: 최소 구현**

`maps/ops/scheduler.py`의 `_to_securities`에서 티커 목록을 모아 한 번에 조회하고,
루프 안의 당일 계산을 그 값으로 바꾼다.

```python
        # 유동성 판정은 하루치가 아니라 20거래일 평균이어야 한다.
        # 돌파·던키언 전략은 거래량 급증일에 신호를 내므로, 당일 하루치로 판정하면
        # 하필 그 전략들이 고르는 순간에만 게이트가 느슨해진다(2026-08-20 195990).
        turnover_by_ticker = HistoricalOHLCVRepository(db).avg_turnover_20d(
            [item.ticker for item in meta], ref_date
        )
```

루프 안의 아래 줄을

```python
            turnover = (ohlcv.close * ohlcv.volume) if ohlcv else 0.0
```

이렇게 바꾼다.

```python
            # 20거래일 이력이 없으면 0 — 유동성 필터가 low_turnover 로 걸러낸다.
            turnover = turnover_by_ticker.get(item.ticker, 0.0)
```

`HistoricalOHLCVRepository` import 가 없으면 파일 상단 import 블록에 추가한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/test_candidate_snapshot_scheduler.py -v`
Expected: PASS (기존 테스트 포함 전부)

> 기존 테스트가 당일 거래대금을 전제로 픽스처를 짜 뒀다면 여기서 깨진다. 그때는
> **테스트를 20거래일치 봉을 넣도록 고친다** — 구현을 되돌리지 않는다.

- [ ] **Step 5: 커밋**

```bash
git add maps/ops/scheduler.py tests/test_candidate_snapshot_scheduler.py
git commit -m "fix: use 20-day average turnover for universe liquidity"
```

---

### Task 3: 주문 수량 한도 순수 함수 (G2)

**Files:**
- Create: `maps/ops/liquidity_cap.py`
- Modify: `maps/common/settings.py:66-67` 부근 (새 설정 2개)
- Test: `tests/test_liquidity_cap.py`

**Interfaces:**
- Consumes: `MapsSettings.maps_order_max_turnover_pct: float`,
  `MapsSettings.maps_order_min_amount_krw: int`
- Produces:
  ```python
  LIQUIDITY_CAPPED = "LIQUIDITY_CAPPED"
  BELOW_MIN_ORDER_AMOUNT = "BELOW_MIN_ORDER_AMOUNT"
  TURNOVER_UNAVAILABLE = "TURNOVER_UNAVAILABLE"

  @dataclass(frozen=True)
  class LiquidityCapResult:
      qty: int
      original_qty: int
      reason: str | None
      turnover_20d: float | None
      limit_amount: float

  def apply_liquidity_cap(
      *, qty: int, price: float, turnover_20d: float | None, settings: MapsSettings
  ) -> LiquidityCapResult
  ```

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
import pytest

from maps.common.settings import MapsSettings
from maps.ops.liquidity_cap import (
    BELOW_MIN_ORDER_AMOUNT,
    LIQUIDITY_CAPPED,
    TURNOVER_UNAVAILABLE,
    apply_liquidity_cap,
)


@pytest.fixture
def settings() -> MapsSettings:
    return MapsSettings(
        maps_order_max_turnover_pct=0.02,
        maps_order_min_amount_krw=500_000,
    )


def test_order_within_limit_passes_untouched(settings) -> None:
    """한도 이내면 손대지 않는다 — 정상 주문 18/19건이 여기 해당한다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=1_000_000_000, settings=settings
    )
    assert result.qty == 100
    assert result.reason is None


def test_order_over_limit_is_reduced(settings) -> None:
    """2026-08-20 195990 실제 수치: 333만원 주문, 20일 평균 3,760만원."""
    result = apply_liquidity_cap(
        qty=2323, price=1434, turnover_20d=37_606_136, settings=settings
    )
    assert result.reason == LIQUIDITY_CAPPED
    assert result.original_qty == 2323
    # 한도 = 37,606,136 * 0.02 = 752,122원 → 1434원으로 524주
    assert result.qty == 524
    assert result.qty * 1434 <= result.limit_amount


def test_reduced_below_minimum_is_blocked(settings) -> None:
    """축소 결과가 최소 주문금액 미만이면 주문하지 않는다."""
    result = apply_liquidity_cap(
        qty=1000, price=1000, turnover_20d=10_000_000, settings=settings
    )
    # 한도 = 200,000원 < 최소 500,000원
    assert result.qty == 0
    assert result.reason == BELOW_MIN_ORDER_AMOUNT


def test_missing_turnover_blocks_the_order(settings) -> None:
    """거래대금을 모르면 사지 않는다(fail-closed). limit_amount 는 0 이다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=None, settings=settings
    )
    assert result.qty == 0
    assert result.reason == TURNOVER_UNAVAILABLE
    assert result.limit_amount == 0.0


def test_zero_turnover_blocks_the_order(settings) -> None:
    """0 도 '모른다'와 같게 다룬다 — 20일 이력이 없으면 0 이 들어온다."""
    result = apply_liquidity_cap(
        qty=100, price=10_000, turnover_20d=0.0, settings=settings
    )
    assert result.qty == 0
    assert result.reason == TURNOVER_UNAVAILABLE


def test_pct_zero_disables_the_gate() -> None:
    """설정으로 끌 수 있다 — 끄면 원래 수량 그대로."""
    off = MapsSettings(maps_order_max_turnover_pct=0.0, maps_order_min_amount_krw=500_000)
    result = apply_liquidity_cap(
        qty=2323, price=1434, turnover_20d=37_606_136, settings=off
    )
    assert result.qty == 2323
    assert result.reason is None


def test_non_positive_qty_is_returned_as_is(settings) -> None:
    """상류에서 이미 0 이면 사유를 새로 붙이지 않는다."""
    result = apply_liquidity_cap(
        qty=0, price=10_000, turnover_20d=1_000_000_000, settings=settings
    )
    assert result.qty == 0
    assert result.reason is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_liquidity_cap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'maps.ops.liquidity_cap'`

- [ ] **Step 3: 설정 두 개를 추가한다**

`maps/common/settings.py`의 `maps_order_max_gap_pct` 바로 아래에 넣는다.

```python
    # 주문금액 / 20거래일 평균 거래대금 상한. 0 이면 게이트 비활성.
    maps_order_max_turnover_pct: float = Field(default=0.02, ge=0.0)
    # 유동성 축소 후 이 금액 미만이면 주문하지 않는다.
    maps_order_min_amount_krw: int = Field(default=500_000, ge=0)
```

- [ ] **Step 4: 순수 함수를 구현한다**

`maps/ops/liquidity_cap.py`를 새로 만든다.

```python
"""주문 금액을 종목 유동성에 맞춰 제한하는 순수 함수.

주문 경로와 주문 미리보기가 **같은 함수**를 쓴다. 경로마다 따로 구현하면
화면이 보여준 수량과 실제 주문이 갈린다 — 손절가가 사이징과 화면에서 갈려
포지션이 2배로 잡혔던 2026-07-29 사고와 같은 구조다(CLAUDE.md 제약 7번).
"""

from __future__ import annotations

from dataclasses import dataclass

from maps.common.settings import MapsSettings

LIQUIDITY_CAPPED = "LIQUIDITY_CAPPED"
BELOW_MIN_ORDER_AMOUNT = "BELOW_MIN_ORDER_AMOUNT"
TURNOVER_UNAVAILABLE = "TURNOVER_UNAVAILABLE"


@dataclass(frozen=True)
class LiquidityCapResult:
    """유동성 한도 적용 결과."""

    qty: int
    original_qty: int
    reason: str | None
    turnover_20d: float | None
    limit_amount: float


def apply_liquidity_cap(
    *,
    qty: int,
    price: float,
    turnover_20d: float | None,
    settings: MapsSettings,
) -> LiquidityCapResult:
    """주문 수량을 20거래일 평균 거래대금 대비 상한 이하로 줄인다.

    한도를 넘으면 주문을 버리지 않고 수량을 줄인다. 줄인 결과가 최소 주문금액에
    못 미치면 주문하지 않는다. 거래대금을 알 수 없으면 사지 않는다(fail-closed).
    """
    pct = settings.maps_order_max_turnover_pct
    if pct <= 0 or qty <= 0:
        return LiquidityCapResult(
            qty=qty, original_qty=qty, reason=None,
            turnover_20d=turnover_20d, limit_amount=0.0,
        )

    if not turnover_20d or turnover_20d <= 0:
        return LiquidityCapResult(
            qty=0, original_qty=qty, reason=TURNOVER_UNAVAILABLE,
            turnover_20d=turnover_20d, limit_amount=0.0,
        )

    limit_amount = turnover_20d * pct
    if price <= 0 or qty * price <= limit_amount:
        return LiquidityCapResult(
            qty=qty, original_qty=qty, reason=None,
            turnover_20d=turnover_20d, limit_amount=limit_amount,
        )

    capped_qty = int(limit_amount // price)
    if capped_qty * price < settings.maps_order_min_amount_krw:
        return LiquidityCapResult(
            qty=0, original_qty=qty, reason=BELOW_MIN_ORDER_AMOUNT,
            turnover_20d=turnover_20d, limit_amount=limit_amount,
        )
    return LiquidityCapResult(
        qty=capped_qty, original_qty=qty, reason=LIQUIDITY_CAPPED,
        turnover_20d=turnover_20d, limit_amount=limit_amount,
    )
```

- [ ] **Step 5: 통과를 확인한다**

Run: `pytest tests/test_liquidity_cap.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: 커밋**

```bash
git add maps/ops/liquidity_cap.py maps/common/settings.py tests/test_liquidity_cap.py
git commit -m "feat: add order liquidity cap function"
```

---

### Task 4: 매수 주문 경로에 한도 적용

**Files:**
- Modify: `maps/ops/scheduler.py:2079-2091` (매수 주문 수량 확정 지점)
- Test: `tests/test_order_cycle.py`

**Interfaces:**
- Consumes: `apply_liquidity_cap(...) -> LiquidityCapResult`,
  `HistoricalOHLCVRepository.avg_turnover_20d(...)`
- Produces: `order_log.decision_context["liquidity"]` — 축소된 주문의 감사 근거
  (`{"original_qty": int, "turnover_20d": float, "limit_amount": float, "reason": str}`)

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
def test_buy_order_quantity_is_capped_by_liquidity(db_session, pipeline) -> None:
    """얇은 종목은 20일 평균 거래대금의 2%까지만 산다."""
    # 20거래일 평균 거래대금 3,760만원짜리 종목을 후보로 세운다
    _seed_thin_liquidity_candidate(db_session, ticker="195990", price=1434)

    pipeline.run_order_cycle()

    order = db_session.query(OrderLog).filter(OrderLog.ticker == "195990").one()
    assert order.qty * order.order_price <= 37_606_136 * 0.02
    assert order.decision_context["liquidity"]["reason"] == "LIQUIDITY_CAPPED"
    assert order.decision_context["liquidity"]["original_qty"] > order.qty


def test_buy_order_skipped_when_turnover_unknown(db_session, pipeline) -> None:
    """거래대금 이력이 없으면 사지 않는다."""
    _seed_candidate_without_history(db_session, ticker="000003")

    pipeline.run_order_cycle()

    assert db_session.query(OrderLog).filter(OrderLog.ticker == "000003").count() == 0


def test_sell_order_is_not_capped(db_session, pipeline) -> None:
    """청산은 막지 않는다 — 얇은 종목에 갇히면 안 된다."""
    _seed_thin_liquidity_holding(db_session, ticker="195990", qty=2323)

    pipeline.run_order_cycle()

    sell = db_session.query(OrderLog).filter(
        OrderLog.ticker == "195990", OrderLog.side == "sell"
    ).one()
    assert sell.qty == 2323
```

> `_seed_*` 헬퍼와 `pipeline` 픽스처는 이 파일의 기존 것을 재사용한다. 없으면 파일 안의
> 다른 주문 사이클 테스트가 후보·보유를 어떻게 세우는지 보고 같은 방식으로 만든다.
> 테스트 파일명이 다르면 주문 사이클을 다루는 기존 파일에 넣는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_order_cycle.py -k liquidity -v`
Expected: FAIL — 현재는 한도가 없어 원래 수량 그대로 주문된다.

- [ ] **Step 3: 최소 구현**

매수 후보 루프에 들어가기 **전에** 한 번만 조회한다.

```python
        # 유동성 한도는 후보 전체를 한 번에 조회한다 — 종목마다 쿼리하면 08:55
        # 주문 창에서 왕복이 쌓인다.
        # 기준일은 후보 스냅샷 기준일이다. 후보는 전부 같은 ref_date 를 갖는다
        # (`_get_order_candidates` 가 latest_date 하나로 거른다). 주문은 다음날
        # 08:55 에 나가므로 그 시점의 최신 확정 봉이 곧 스냅샷 기준일 봉이다.
        turnover_by_ticker = (
            HistoricalOHLCVRepository(db).avg_turnover_20d(
                [c.ticker for c in candidates], candidates[0].ref_date
            )
            if candidates
            else {}
        )
```

`qty` 확정 직후, `if qty <= 0` 검사 **앞에** 한도를 적용한다.

```python
            cap = apply_liquidity_cap(
                qty=qty,
                price=limit_price,
                turnover_20d=turnover_by_ticker.get(candidate.ticker),
                settings=self._settings,
            )
            if cap.reason is not None:
                logger.info(
                    "유동성 한도 적용 %s: %s주 → %s주 (%s, 20일평균 %.0f원)",
                    candidate.ticker, cap.original_qty, cap.qty,
                    cap.reason, cap.turnover_20d or 0.0,
                )
            qty = cap.qty
            if qty <= 0:
                skipped += 1
                continue
```

`decision_context`에 근거를 남긴다. 기존 `decision_context={...}` 딕셔너리에 키를 추가한다.

```python
                    "liquidity": {
                        "original_qty": cap.original_qty,
                        "turnover_20d": cap.turnover_20d,
                        "limit_amount": cap.limit_amount,
                        "reason": cap.reason,
                    },
```

매도 경로에는 아무것도 넣지 않는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `pytest tests/test_order_cycle.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add maps/ops/scheduler.py tests/test_order_cycle.py
git commit -m "feat: cap buy order quantity by liquidity"
```

---

### Task 5: 주문 미리보기에 같은 한도 적용

**Files:**
- Modify: `maps/ops/order_preview.py:163-400` (`build_order_preview`)
- Modify: `maps/api/schemas.py:240-255` (`PreviewOrderItem`)
- Test: `tests/test_order_preview.py`

**Interfaces:**
- Consumes: `apply_liquidity_cap(...)`, `HistoricalOHLCVRepository.avg_turnover_20d(...)`
- Produces: `PreviewOrderItem`에 `original_qty: int`, `liquidity_reason: str | None`,
  `turnover_20d: float | None`, `liquidity_limit_amount: float` 추가

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
def test_preview_shows_liquidity_capped_quantity(db_session, settings) -> None:
    """미리보기 수량이 실제 주문 수량과 같아야 한다."""
    _seed_thin_liquidity_candidate(db_session, ticker="195990", price=1434)

    preview = build_order_preview(db_session, settings)
    item = next(i for i in preview.items if i.ticker == "195990")

    assert item.liquidity_reason == "LIQUIDITY_CAPPED"
    assert item.original_qty > item.estimated_qty
    assert item.estimated_amount <= item.liquidity_limit_amount


def test_preview_marks_blocked_when_turnover_unknown(db_session, settings) -> None:
    """차단도 조용히 넘어가지 않고 사유가 보여야 한다."""
    _seed_candidate_without_history(db_session, ticker="000003")

    preview = build_order_preview(db_session, settings)
    item = next(i for i in preview.items if i.ticker == "000003")

    assert item.skipped is True
    assert item.liquidity_reason == "TURNOVER_UNAVAILABLE"
    assert item.estimated_qty == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_order_preview.py -k liquidity -v`
Expected: FAIL — `AttributeError: 'PreviewOrderItem' object has no attribute 'liquidity_reason'`

- [ ] **Step 3: 스키마에 필드를 추가한다**

`maps/api/schemas.py`의 `PreviewOrderItem`에 넣는다. 기존 필드는 건드리지 않는다.

```python
    original_qty: int = 0            # 유동성 축소 전 수량
    liquidity_reason: str | None = None
    turnover_20d: float | None = None
    liquidity_limit_amount: float = 0.0
```

- [ ] **Step 4: 미리보기에 한도를 적용한다**

`build_order_preview`에서 후보 루프 전에 조회한다.

파일 상단에 import 를 추가한다.

```python
from maps.data.ohlcv_repo import HistoricalOHLCVRepository
from maps.ops.liquidity_cap import (
    BELOW_MIN_ORDER_AMOUNT,
    TURNOVER_UNAVAILABLE,
    apply_liquidity_cap,
)
```

후보 루프 전에 한 번만 조회한다. 기준일은 Task 4 와 **같은 값**이어야 한다 — 다르면
미리보기와 실주문이 갈린다.

```python
    turnover_by_ticker = (
        HistoricalOHLCVRepository(db).avg_turnover_20d(
            [c.ticker for c in candidates], candidates[0].ref_date
        )
        if candidates
        else {}
    )
```

`qty = _estimated_qty(...)` 다음, `if qty <= 0` 검사 앞에 넣는다.

```python
        cap = apply_liquidity_cap(
            qty=qty,
            price=limit_price,
            turnover_20d=turnover_by_ticker.get(candidate.ticker),
            settings=settings,
        )
        qty = cap.qty
```

행을 만들 때 새 필드를 채우고, `qty <= 0`으로 건너뛰는 행에도 사유를 넣는다.

```python
                original_qty=cap.original_qty,
                liquidity_reason=cap.reason,
                turnover_20d=cap.turnover_20d,
                liquidity_limit_amount=cap.limit_amount,
```

`skip_reason`이 비어 있고 `cap.reason`이 차단 사유면 `skip_reason`에도 넣어
기존 화면이 이유 없이 비지 않게 한다.

```python
                skip_reason=skip_reason or (
                    "유동성 부족" if cap.reason in (
                        BELOW_MIN_ORDER_AMOUNT, TURNOVER_UNAVAILABLE
                    ) else None
                ),
```

- [ ] **Step 5: 통과를 확인한다**

Run: `pytest tests/test_order_preview.py -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add maps/ops/order_preview.py maps/api/schemas.py tests/test_order_preview.py
git commit -m "feat: show liquidity cap in order preview"
```

---

### Task 6: 다이제스트·매매일지·화면 노출

**Files:**
- Modify: `maps/api/schemas.py` (`DailyDigest`)
- Modify: `maps/ops/daily_digest.py`
- Modify: `templates/orders.html`, `static/js/app.js`
- Modify: `.claude/commands/blog.md`
- Test: `tests/test_daily_digest.py`, `tests/test_orders_ui.py`, `tests/test_beginner_blog_prompt.py`

**Interfaces:**
- Consumes: `order_log.decision_context["liquidity"]`, `PreviewOrderItem.liquidity_reason`
- Produces: `DailyDigest.liquidity_capped_total: int`,
  `DailyDigest.liquidity_blocked_total: int`,
  `DailyDigest.liquidity_notes: list[str]`

- [ ] **Step 1: 실패 테스트를 쓴다**

```python
def test_digest_reports_liquidity_capped_orders(db_session, settings) -> None:
    """축소된 주문이 다이제스트에 집계돼야 한다."""
    _seed_order_with_liquidity_context(
        db_session, ticker="195990", original_qty=2323, qty=524,
        reason="LIQUIDITY_CAPPED",
    )

    digest = build_daily_digest(db_session, dt.date(2026, 8, 21), settings)

    assert digest.liquidity_capped_total == 1
    assert any("195990" in note for note in digest.liquidity_notes)


def test_digest_reports_liquidity_blocked_orders(db_session, settings) -> None:
    """차단은 주문이 없으므로 미리보기 사유에서 집계한다."""
    _seed_preview_blocked_by_liquidity(db_session, ticker="000003")

    digest = build_daily_digest(db_session, dt.date(2026, 8, 21), settings)

    assert digest.liquidity_blocked_total == 1
```

```python
def test_orders_screen_shows_liquidity_cap() -> None:
    """축소된 예정 주문이 원래 수량과 사유를 보여야 한다."""
    template = Path("templates/orders.html").read_text(encoding="utf-8")
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "liquidity_reason" in script
    assert "유동성 축소" in script
    assert "원래 수량" in script
    assert "유동성" in template
```

```python
def test_blog_prompt_requires_liquidity_cap_disclosure() -> None:
    """축소된 주문을 원래 계획대로 산 것처럼 쓰지 못하게 한다."""
    prompt = Path(".claude/commands/blog.md").read_text(encoding="utf-8")

    assert "liquidity_capped_total" in prompt
    assert "유동성 축소" in prompt
    assert "원래 계획대로" in prompt
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest tests/test_daily_digest.py tests/test_orders_ui.py tests/test_beginner_blog_prompt.py -k liquidity -v`
Expected: FAIL (전부)

> `tests/test_orders_ui.py`가 없으면 만든다. `tests/test_candidates_ui.py`가 템플릿과
> JS를 문자열로 읽어 검사하는 방식을 그대로 따른다.

- [ ] **Step 3: 다이제스트 스키마와 빌더**

`maps/api/schemas.py`의 `DailyDigest`에 추가한다.

```python
    liquidity_capped_total: int = 0      # 유동성 한도로 수량이 줄어 나간 주문 수
    liquidity_blocked_total: int = 0     # 유동성 때문에 아예 나가지 않은 후보 수
    liquidity_notes: list[str] = []      # "195990 2323주 → 524주 (LIQUIDITY_CAPPED)"
```

`maps/ops/daily_digest.py`에 집계 함수를 넣고 `build_daily_digest`에서 부른다.

```python
def _build_liquidity(orders: list[OrderLog]) -> tuple[int, int, list[str]]:
    """그날 주문의 유동성 축소·차단을 센다.

    축소는 실제로 나간 주문이라 order_log 에 남는다. 차단은 주문 자체가
    없으므로 여기서는 세지 않고 미리보기 사유에서 센다.
    """
    capped = 0
    notes: list[str] = []
    for order in orders:
        context = order.decision_context or {}
        liquidity = context.get("liquidity") or {}
        if liquidity.get("reason") != "LIQUIDITY_CAPPED":
            continue
        capped += 1
        notes.append(
            "%s %s주 → %s주 (20일 평균 거래대금 %s원의 한도 %s원)"
            % (
                order.ticker,
                liquidity.get("original_qty"),
                order.qty,
                format(int(liquidity.get("turnover_20d") or 0), ","),
                format(int(liquidity.get("limit_amount") or 0), ","),
            )
        )
    return capped, 0, notes
```

차단 건수는 같은 날 주문 미리보기 행 중 `liquidity_reason` 이
`TURNOVER_UNAVAILABLE` 또는 `BELOW_MIN_ORDER_AMOUNT` 인 것을 센다.

```python
    blocked = sum(
        1
        for item in preview.items
        if item.liquidity_reason in (TURNOVER_UNAVAILABLE, BELOW_MIN_ORDER_AMOUNT)
    )
```

- [ ] **Step 4: 화면**

`templates/orders.html` 의 예정 주문 표 헤더에 열을 하나 추가한다.

```html
              <th title="20거래일 평균 거래대금 대비 주문 한도">유동성</th>
```

`static/js/app.js` 의 예정 주문 행 생성부에 셀을 추가한다.

```javascript
        // 유동성 한도로 수량이 줄거나 막힌 경우를 반드시 드러낸다.
        // 조용히 줄어들면 사용자는 왜 이만큼만 샀는지 알 방법이 없다.
        let liquidityHtml = '<span class="text-muted">—</span>';
        if (o.liquidity_reason === 'LIQUIDITY_CAPPED') {
          liquidityHtml = `${badge('유동성 축소', 'warn')}`
            + `<br><span class="text-muted">원래 수량 ${o.original_qty}주</span>`;
        } else if (o.liquidity_reason === 'TURNOVER_UNAVAILABLE') {
          liquidityHtml = badge('거래대금 미확인·차단', 'fail');
        } else if (o.liquidity_reason === 'BELOW_MIN_ORDER_AMOUNT') {
          liquidityHtml = badge('축소 후 최소금액 미달·차단', 'fail');
        }
```

행 템플릿에 `<td>${liquidityHtml}</td>` 를 추가한다.

- [ ] **Step 5: 매매일지 규칙**

`.claude/commands/blog.md`에 넣는다.

```
liquidity_capped_total 이 0 보다 크면 유동성 축소가 있었다는 사실을 반드시 쓴다.
축소된 주문은 `유동성 축소` 라고 부르고 원래 계획대로 매수한 것처럼 쓰지 않는다.
liquidity_notes 의 원래 수량과 실제 수량을 함께 보존한다. liquidity_blocked_total 이
0 보다 크면 유동성 때문에 매수하지 못한 후보가 있었다고 쓴다.
```

- [ ] **Step 6: 통과를 확인한다**

Run: `pytest tests/test_daily_digest.py tests/test_orders_ui.py tests/test_beginner_blog_prompt.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add maps/api/schemas.py maps/ops/daily_digest.py templates/orders.html \
        static/js/app.js .claude/commands/blog.md tests/
git commit -m "feat: surface liquidity cap in digest, screen and diary"
```

---

### Task 7: 탈락률 실측과 문서

**Files:**
- Modify: `maps/data/CLAUDE.md`, `maps/ops/CLAUDE.md`
- Modify: `HANDOFF.md`

- [ ] **Step 1: G1 이 후보를 얼마나 줄이는지 잰다**

운영 DB에 대고 **읽기만** 한다. 최근 5거래일에 대해 기존 판정(당일 하루치)과 새 판정
(20일 평균)으로 각각 `low_turnover` 탈락 종목 수를 세고 차이를 기록한다.

```bash
ssh -i "<key>" ubuntu@3.37.117.246 \
  "cd /opt/maps && PYTHONPATH=/opt/maps .venv/bin/python /home/ubuntu/turnover_impact.py"
```

기록할 것: 날짜별 유니버스 크기, 기존/신규 탈락 수, 탈락률.
**탈락률이 `REJECTION_ALERT_THRESHOLD`(0.40)를 넘으면 여기서 멈추고 사용자에게 보고한다** —
기준을 다시 의논해야 하며 임의로 완화하지 않는다.

- [ ] **Step 2: 패키지 문서를 실제 동작과 맞춘다**

`maps/data/CLAUDE.md`에 `avg_turnover_20d`가 유동성 판정의 유일한 정의라는 것과, 봉이
20개 미만이면 제외한다는 것을 적는다.

`maps/ops/CLAUDE.md`에 `liquidity_cap.py`의 함수·사유 코드 3종과, 주문 경로와 미리보기가
같은 함수를 써야 하는 이유(2026-07-29 손절가 사고와 같은 구조)를 적는다.

- [ ] **Step 3: 전체 검증**

```bash
pytest tests -q
pytest maps/tests -q
python -m compileall maps -q
python -m alembic heads
git diff --check
```

Expected: 전부 통과. Alembic head 는 `0028_holding_regime_audit` 그대로여야 한다
(이 작업은 마이그레이션을 만들지 않는다).

- [ ] **Step 4: 계획 요구사항과 diff 를 자체 리뷰한다**

발견된 결함은 새 실패 테스트부터 쓰고 고친다.

- [ ] **Step 5: HANDOFF 기록과 최종 커밋**

`HANDOFF.md` 맨 위에 구현·검증 결과, Step 1 의 탈락률 실측치, 미배포 상태를 적는다.

```bash
git add maps/data/CLAUDE.md maps/ops/CLAUDE.md HANDOFF.md
git commit -m "docs: record order liquidity cap implementation"
```
