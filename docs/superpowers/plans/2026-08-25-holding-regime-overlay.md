# Holding Regime Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매수 당시 장세와 현재 확정 장세를 비교해 기존 전략을 바꾸지 않은 채 보유 종목을 `HOLD`, `WATCH`, `EXIT`로 판정하고, 기본 shadow 모드에서 감사·다이제스트를 축적한 뒤 승인된 경우에만 자동 청산을 집행한다.

**Architecture:** 순수 판정기는 `maps/risk/holding_regime_overlay.py`에 두고, 구조화된 전략 ID와 진입 장세가 모두 있는 자동후보 주문에 적용한다. 진입 장세는 `OrderLog.decision_context.market`, 현재 장세는 `MarketRegimeLog`의 최근 두 거래일 적용값을 사용한다. 모든 판정은 `holding_regime_audit`에 일자·포지션별로 upsert하며, `shadow`는 기록만 하고 `enforce`만 기존 청산 조건에 `regime_exit`를 OR로 추가한다.

**Tech Stack:** Python, FastAPI 설정(Pydantic), SQLAlchemy, Alembic, pytest, 기존 `MarketRegimeLog`·`OrderLog`·`AnalysisPick`·daily digest.

## Global Constraints

- 진입 시 정한 `strategy_id`와 매수 근거를 보유 기간 동안 변경하거나 새 전략으로 재분류하지 않는다.
- 기존 손절·목표가·트레일링·전략 청산 신호는 그대로 유지하며, 오버레이 데이터가 없거나 오류가 나도 기존 청산을 막지 않는다.
- 손절가 계산은 계속 `maps.strategy.live_rules.effective_stop_price()`만 사용한다. 1차 범위에서는 새 손절가 계산, 손절선 강화, 부분매도, 추가매수 기능을 만들지 않는다.
- 설정값은 `off | shadow | enforce`이며 기본값은 `shadow`다. 운영 서버에서 `enforce`로 변경하는 작업은 백테스트·shadow 관측 결과 검토와 사용자의 명시적 승인 전에는 수행하지 않는다.
- 강제 청산은 최근 서로 다른 두 `MarketRegimeLog.ref_date` 관측이 연속으로 같은 불리 조건을 만족할 때만 허용한다. 한 번만 관측되면 `WATCH`다.
- 최근 장세가 3일보다 오래됐거나, 진입 장세가 없거나, 전략 ID가 등록되지 않았으면 오버레이는 `HOLD`로 fail-open하고 구체적 reason code를 남긴다. 이는 오버레이만 fail-open한다는 뜻이며 기존 손절·익절은 계속 작동한다.
- `pullback_short`, `ath_outlier`, `donchian_research`만 1차 자동 `EXIT` 대상이다. `multi_asset`과 `contrarian_quality`는 1차에서 `HOLD` 또는 `WATCH`만 반환하고 원래 전략 신호가 청산을 담당한다.
- 불리 조건은 진입 장세가 `strong|mixed`이고 현재가 `weak`으로 하락했거나 현재 주간추세가 `fail`인 경우다. 현재 `high` 변동성 단독, 선호 장세 이탈, 미확정 불리 조건은 `WATCH`다.
- `AnalysisPick.strategy_context`는 자유문자열이고 신뢰할 수 있는 전략 ID가 아니므로 전략매매 단일·분할 보유는 1차 오버레이 대상에서 제외한다. 기존 `bracket_tickers` 분리와 `_process_strategy_trades`의 목표·손절 관리를 그대로 유지한다.
- `exit_reason`은 기존 `String(16)` 제약 안의 `regime_exit`을 사용한다. 매도 주문 `decision_context`에는 판정 전체를 함께 저장한다.
- 시간은 기존 규칙대로 DB에는 UTC naive, 거래일 판단에는 KST `ref_date`를 사용한다.

---

### Task 1: 순수 보유 장세 판정기

**Files:**
- Create: `maps/risk/holding_regime_overlay.py`
- Create: `tests/test_holding_regime_overlay.py`
- Modify: `maps/risk/CLAUDE.md`

**Interfaces:**
- Consumes: `STRATEGY_GROUP_MAP`, 전략 클래스의 `preferred_regimes`, 진입·현재·직전 장세 스냅샷.
- Produces: `evaluate_holding_regime(strategy_id, entry, previous, current, as_of, max_age_days) -> HoldingRegimeDecision`.

- [ ] **Step 1: 전략 전환이 아니라 별도 리스크 판정임을 고정하는 실패 테스트 작성**

```python
from maps.risk.holding_regime_overlay import (
    HoldingRegimeAction,
    HoldingRegimeSnapshot,
    evaluate_holding_regime,
)


def _state(day: int, regime: str, weekly: str = "pass", vol: str = "normal"):
    return HoldingRegimeSnapshot(
        ref_date=dt.date(2026, 8, day),
        regime=regime,
        weekly_trend=weekly,
        vol_regime=vol,
    )


def test_pullback_strong_to_two_confirmed_weak_observations_exits():
    decision = evaluate_holding_regime(
        strategy_id="pullback_v3",
        entry=_state(20, "strong"),
        previous=_state(24, "weak"),
        current=_state(25, "weak"),
        as_of=dt.date(2026, 8, 25),
        max_age_days=3,
    )
    assert decision.action is HoldingRegimeAction.EXIT
    assert decision.reason_code == "CONFIRMED_ADVERSE_REGIME"
    assert decision.strategy_id == "pullback_v3"
```

다음 경계 테스트도 같은 파일에 각각 독립 함수로 작성한다.

| 테스트 함수 | 입력 | 정확한 기대값 |
|---|---|---|
| `test_first_weak_observation_only_watches` | pullback, entry strong, previous mixed, current weak | `WATCH/ADVERSE_REGIME_UNCONFIRMED`, `confirmed=False` |
| `test_strong_to_mixed_ath_breakout_watches_as_nonpreferred` | ATH, entry strong, previous strong, current mixed | `WATCH/CURRENT_REGIME_NOT_PREFERRED` |
| `test_pullback_strong_to_mixed_holds_when_still_preferred` | pullback, entry strong, previous strong, current mixed | `HOLD/REGIME_COMPATIBLE` |
| `test_high_volatility_alone_watches_but_does_not_exit` | pullback, strong→strong, current vol high | `WATCH/HIGH_VOLATILITY` |
| `test_contrarian_never_forced_exits_in_v1` | contrarian, mixed→strong, weekly fail 2회 | action이 `EXIT`가 아니며 `WATCH` |
| `test_multi_asset_never_forced_exits_in_v1` | multi-asset, strong→weak, weekly fail 2회 | action이 `EXIT`가 아니며 `WATCH` |
| `test_missing_entry_regime_holds_with_reason` | entry `None` | `HOLD/ENTRY_REGIME_UNAVAILABLE` |
| `test_stale_current_regime_holds_with_reason` | current.ref_date가 as_of보다 4일 전 | `HOLD/CURRENT_REGIME_STALE` |
| `test_unknown_strategy_holds_with_reason` | strategy_id=`unknown` | `HOLD/UNKNOWN_STRATEGY` |
| `test_weekly_fail_twice_exits_momentum_group` | Donchian, entry mixed, previous/current weekly fail | `EXIT/CONFIRMED_ADVERSE_REGIME`, `confirmed=True` |

- [ ] **Step 2: 판정기 테스트가 모듈 부재로 실패하는지 확인**

Run: `python -m pytest tests/test_holding_regime_overlay.py -q`

Expected: FAIL with `ModuleNotFoundError: maps.risk.holding_regime_overlay`.

- [ ] **Step 3: 타입과 판정 순서를 최소 구현**

```python
class HoldingRegimeAction(str, Enum):
    HOLD = "hold"
    WATCH = "watch"
    EXIT = "exit"


@dataclass(frozen=True)
class HoldingRegimeSnapshot:
    ref_date: dt.date
    regime: str
    weekly_trend: str
    vol_regime: str


@dataclass(frozen=True)
class HoldingRegimeDecision:
    action: HoldingRegimeAction
    reason_code: str
    strategy_id: str
    strategy_group: str | None
    entry: HoldingRegimeSnapshot | None
    previous: HoldingRegimeSnapshot | None
    current: HoldingRegimeSnapshot | None
    confirmed: bool

    def to_dict(self) -> dict[str, object]:
        def snapshot(value: HoldingRegimeSnapshot | None) -> dict[str, str] | None:
            if value is None:
                return None
            return {
                "ref_date": value.ref_date.isoformat(),
                "regime": value.regime,
                "weekly_trend": value.weekly_trend,
                "vol_regime": value.vol_regime,
            }

        return {
            "action": self.action.value,
            "reason_code": self.reason_code,
            "strategy_id": self.strategy_id,
            "strategy_group": self.strategy_group,
            "entry": snapshot(self.entry),
            "previous": snapshot(self.previous),
            "current": snapshot(self.current),
            "confirmed": self.confirmed,
        }


_EXIT_ELIGIBLE_GROUPS = frozenset({
    "pullback_short", "ath_outlier", "donchian_research",
})
```

`evaluate_holding_regime()`은 아래 순서로 정확히 판정한다.

전략 클래스 조회는 `maps.strategy.catalog.STRATEGY_CLASSES`를 사용하고, 외부에서 받은
regime·weekly_trend·vol_regime 문자열은 비교 전에 `strip().lower()`로 정규화한다.

1. 전략 미등록 → `HOLD/UNKNOWN_STRATEGY`.
2. 진입 장세 없음 → `HOLD/ENTRY_REGIME_UNAVAILABLE`.
3. 현재 장세 없음 또는 `as_of - current.ref_date > max_age_days` → `HOLD/CURRENT_REGIME_UNAVAILABLE|CURRENT_REGIME_STALE`.
4. `hard_adverse = current.weekly_trend == "fail" or (entry.regime in {"strong", "mixed"} and current.regime == "weak")`.
5. 직전 관측도 같은 식으로 `hard_adverse`이고 그룹이 `_EXIT_ELIGIBLE_GROUPS`면 `EXIT/CONFIRMED_ADVERSE_REGIME`.
6. 현재만 `hard_adverse`면 `WATCH/ADVERSE_REGIME_UNCONFIRMED`.
7. 현재 장세가 해당 전략 클래스의 `preferred_regimes` 밖이면 `WATCH/CURRENT_REGIME_NOT_PREFERRED`.
8. 현재 변동성이 `high`면 `WATCH/HIGH_VOLATILITY`.
9. 그 밖에는 `HOLD/REGIME_COMPATIBLE`.

- [ ] **Step 4: 순수 판정기 테스트 통과 확인**

Run: `python -m pytest tests/test_holding_regime_overlay.py -q`

Expected: PASS for all policy matrix and missing/stale boundaries.

- [ ] **Step 5: 패키지 문서에 공개 인터페이스와 fail-open 경계 추가**

`maps/risk/CLAUDE.md` 디렉터리 표에 `holding_regime_overlay.py`를 추가하고, 자동 청산 대상 그룹·2회 확인·기본 shadow·기존 청산 불간섭을 명시한다.

- [ ] **Step 6: Task 1 커밋**

```powershell
git add maps/risk/holding_regime_overlay.py maps/risk/CLAUDE.md tests/test_holding_regime_overlay.py
git commit -m "feat: add holding regime overlay policy"
```

---

### Task 2: 판정 감사 테이블과 운영 모드 설정

**Files:**
- Modify: `maps/common/models.py`
- Modify: `maps/common/settings.py`
- Modify: `.env.example`
- Create: `alembic/versions/0028_holding_regime_audit.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_settings.py`
- Modify: `maps/common/CLAUDE.md`
- Modify: `alembic/CLAUDE.md`

**Interfaces:**
- Consumes: Task 1의 `HoldingRegimeDecision`.
- Produces: `HoldingRegimeAudit` ORM 모델과 `maps_holding_regime_overlay_mode`, `maps_holding_regime_max_age_days` 설정.

- [ ] **Step 1: 마이그레이션·설정 실패 테스트 작성**

`tests/test_migrations.py`의 기대 head를 `0028_holding_regime_audit`로 바꾸고 다음 컬럼과 유니크 제약을 검사한다.

```python
audit_columns = {c["name"] for c in inspector.get_columns("holding_regime_audit")}
assert {
    "id", "ref_date", "position_key", "ticker", "strategy_id",
    "entry_regime", "current_regime", "weekly_trend", "vol_regime", "action",
    "reason_code", "confirmed", "mode", "details", "exit_order_id", "created_at",
    "updated_at",
} <= audit_columns
```

`tests/test_settings.py`에는 기본값과 환경변수 파싱을 추가한다.

```python
def test_holding_regime_overlay_defaults_to_shadow():
    settings = MapsSettings(_env_file=None)
    assert settings.maps_holding_regime_overlay_mode == "shadow"
    assert settings.maps_holding_regime_max_age_days == 3
```

- [ ] **Step 2: 새 테스트의 RED 확인**

Run: `python -m pytest tests/test_settings.py tests/test_migrations.py -q`

Expected: FAIL because settings, model, migration head and table do not exist.

- [ ] **Step 3: 설정과 ORM 모델 구현**

```python
maps_holding_regime_overlay_mode: Literal["off", "shadow", "enforce"] = "shadow"
maps_holding_regime_max_age_days: int = Field(default=3, ge=1, le=10)
```

`HoldingRegimeAudit`는 `(ref_date, position_key)` 유니크 제약을 사용한다. `position_key`는 자동후보 포지션에서 `order:<OrderLog.id>`로 만든다. `details` JSON에는 entry·previous·current 스냅샷과 전략 그룹을 저장한다.
`OrderLog.exit_reason`의 모델 주석에도 `regime_exit`을 허용값으로 추가하되 컬럼 길이나 기존 데이터는 변경하지 않는다.

- [ ] **Step 4: Alembic 0028 작성 및 양방향 스키마 정의**

`upgrade()`는 `holding_regime_audit` 테이블, `ref_date`·`ticker`·`action` 인덱스, `(ref_date, position_key)` 유니크 제약을 만든다. `downgrade()`는 인덱스와 테이블만 제거하며 주문·장세 기존 테이블을 건드리지 않는다. revision 문자열은 32자 제한 안의 `0028_holding_regime_audit`, down_revision은 `0027_order_decision_context`다.

- [ ] **Step 5: 환경변수 예시와 패키지 문서 갱신**

`.env.example`에 다음을 추가한다.

```dotenv
# 보유 장세 오버레이: off | shadow | enforce (운영 enforce는 검증·승인 후만)
MAPS_HOLDING_REGIME_OVERLAY_MODE=shadow
MAPS_HOLDING_REGIME_MAX_AGE_DAYS=3
```

- [ ] **Step 6: 모델·설정·마이그레이션 테스트 통과 확인**

Run: `python -m pytest tests/test_settings.py tests/test_migrations.py -q`

Expected: PASS and `alembic heads` prints only `0028_holding_regime_audit (head)`.

- [ ] **Step 7: Task 2 커밋**

```powershell
git add maps/common/models.py maps/common/settings.py .env.example alembic/versions/0028_holding_regime_audit.py tests/test_migrations.py tests/test_settings.py maps/common/CLAUDE.md alembic/CLAUDE.md
git commit -m "feat: persist holding regime decisions"
```

---

### Task 3: 자동후보 보유 포지션에 shadow/enforce 연결

**Files:**
- Modify: `maps/ops/scheduler.py:2215`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_plan_exits.py`

**Interfaces:**
- Consumes: 자동후보 진입 주문의 `decision_context.market`, 최근 두 `MarketRegimeLog`, Task 1 판정기, Task 2 감사 모델·설정.
- Produces: 자동후보 포지션의 일일 감사 행과 enforce 모드의 `regime_exit` 시장가 매도.

- [ ] **Step 1: 기존 청산이 오버레이와 독립적으로 유지되는 실패 테스트 작성**

다음 통합 테스트를 추가한다.

| 테스트 함수 | 정확한 검증 |
|---|---|
| `test_shadow_overlay_records_exit_decision_without_selling` | confirmed adverse 입력에서 audit action=`exit`, mode=`shadow`, SELL 0건 |
| `test_enforce_overlay_sells_after_two_confirmed_weak_observations` | mode=`enforce`에서 SELL 1건, `exit_reason=regime_exit` |
| `test_off_overlay_neither_evaluates_nor_changes_existing_exit` | mode=`off`에서 audit 0건, SELL 0건 |
| `test_missing_legacy_decision_context_never_forces_exit` | entry context 없는 과거 주문은 audit reason=`ENTRY_REGIME_UNAVAILABLE`, SELL 0건 |
| `test_stop_loss_still_sells_when_overlay_data_is_missing` | entry context가 없어도 기존 가격이 stop 아래면 `stop_loss` SELL 1건 |
| `test_regime_exit_keeps_original_entry_strategy_id` | regime exit SELL의 strategy_id가 매수 OrderLog의 strategy_id와 동일 |
| `test_regime_exit_order_contains_overlay_decision_context` | SELL context의 origin, entry_order_id, action, reason_code, mode가 정확히 저장됨 |
| `test_same_day_broker_sync_upserts_one_audit_row` | 같은 ref_date로 두 번 호출 후 `(ref_date, position_key)` audit가 1행 |

테스트 진입 주문의 `decision_context`는 실제 v1 형태를 사용한다.

```python
decision_context={
    "version": 1,
    "origin": "live",
    "market": {
        "ref_date": "2026-08-20",
        "regime": "strong",
        "weekly_trend": "pass",
        "vol_regime": "normal",
    },
}
```

- [ ] **Step 2: 통합 테스트가 기존 청산 경로의 장세 무시로 실패하는지 확인**

Run: `python -m pytest tests/test_scheduler.py tests/test_plan_exits.py -q`

Expected: new overlay tests FAIL; all pre-existing stop/plan exit tests remain PASS.

- [ ] **Step 3: 스케줄러에 읽기·upsert 헬퍼 구현**

다음 책임을 작은 private helper로 분리한다.

| 헬퍼 | 입력·출력 | 구현 내용 |
|---|---|---|
| `_entry_regime_snapshot` | `OrderLog -> HoldingRegimeSnapshot | None` | `decision_context.market`의 ref_date/regime/weekly_trend/vol_regime을 검증해 변환하고 하나라도 필수값이 잘못되면 `None` |
| `_recent_regime_snapshots` | `Session, ref_date -> (previous, current)` | ref_date 이하 `MarketRegimeLog` 최신 2행을 날짜 역순 조회한 뒤 `(이전, 현재)` 순서로 반환 |
| `_upsert_holding_regime_audit` | ref_date·position_key·ticker·decision·mode -> `HoldingRegimeAudit` | `(ref_date, position_key)` 행을 생성 또는 갱신하고 decision의 모든 입력·출력을 저장한 뒤 commit |

현재·직전 장세는 `MarketRegimeLog.ref_date <= ref_date`에서 날짜가 다른 최신 2행을 읽고 `applied_regime`, `weekly_trend`, `vol_regime`을 사용한다. `_analyze_regime()`를 청산 루프에서 새로 호출해 장세 이력을 덮어쓰지 않는다.

- [ ] **Step 4: `_submit_exit_orders`의 기존 판정 뒤에 오버레이를 결합**

순서는 반드시 기존 `plan_exit_decision` 또는 stop/strategy exit 계산 후다.

```python
overlay_exit = mode == "enforce" and decision.action is HoldingRegimeAction.EXIT
if not should_exit and overlay_exit:
    should_exit = True
    reason = "regime_exit"
```

기존 손절·익절이 먼저 발생하면 그 기존 reason을 유지한다. 오버레이 때문에 매도할 때도 `Order.strategy_id=entry.strategy_id`를 유지하고 다음 정보를 `Order.decision_context`에 저장한다.

```python
{
    "version": 1,
    "origin": "holding_regime_overlay",
    "entry_order_id": entry.order_id,
    "holding_regime_overlay": decision.to_dict(),
    "mode": mode,
}
```

주문 성공 후 감사 행의 `exit_order_id`를 갱신한다. 중복 주문·브로커 오류가 나면 감사 action은 보존하고 `exit_order_id=None`으로 남겨 다음 broker sync가 기존 중복 방지 경계에서 재시도하도록 한다.

- [ ] **Step 5: 자동후보 통합 테스트 통과 확인**

Run: `python -m pytest tests/test_scheduler.py tests/test_plan_exits.py tests/test_exit_order_price_record.py -q`

Expected: PASS, 특히 진입 ATR 고정과 기존 청산 reason 우선순위가 변하지 않는다.

`tests/test_strategy_trade.py::test_submit_exit_orders_excludes_bracket_tickers`도 함께 실행해 전략매매 BOUGHT 종목이 오버레이 경로에 들어오지 않는지 확인한다.

- [ ] **Step 6: Task 3 커밋**

```powershell
git add maps/ops/scheduler.py tests/test_scheduler.py tests/test_plan_exits.py
git commit -m "feat: apply regime overlay to held candidates"
```

---

### Task 4: 다이제스트·운영 가시성

**Files:**
- Modify: `maps/api/schemas.py:1092`
- Modify: `maps/ops/daily_digest.py:562`
- Modify: `tests/test_daily_digest.py`
- Modify: `docs/OPERATIONS_CONFIG.md`
- Modify: `maps/ops/CLAUDE.md`
- Modify: `index.md`

**Interfaces:**
- Consumes: 해당 `ref_date`의 `HoldingRegimeAudit`.
- Produces: 보유 종목별 `regime_overlay`와 일일 action 집계, 운영 설정 문서.

- [ ] **Step 1: digest가 shadow 경고와 enforce 청산을 설명하는 실패 테스트 작성**

| 테스트 함수 | 정확한 검증 |
|---|---|
| `test_digest_holding_exposes_shadow_overlay_decision` | holding.regime_overlay에 action/reason/mode/entry/current/confirmed가 audit와 일치 |
| `test_digest_warns_when_overlay_entry_context_is_missing` | `ENTRY_REGIME_UNAVAILABLE`가 holding overlay reason에 그대로 노출 |
| `test_digest_execution_keeps_regime_exit_context` | regime SELL execution의 exit_reason과 decision_context가 보존됨 |
| `test_digest_overlay_summary_counts_hold_watch_exit` | audit fixture HOLD 2, WATCH 1, EXIT 1이면 summary가 정확히 `{"hold": 2, "watch": 1, "exit": 1}` |

- [ ] **Step 2: digest 테스트 RED 확인**

Run: `python -m pytest tests/test_daily_digest.py -q`

Expected: FAIL because digest schemas do not expose overlay data.

- [ ] **Step 3: API 스키마에 판정 객체 추가**

```python
class DigestHoldingRegimeOverlay(BaseModel):
    action: str
    reason_code: str
    mode: str
    entry_regime: str | None = None
    current_regime: str | None = None
    weekly_trend: str | None = None
    vol_regime: str | None = None
    confirmed: bool = False


class DigestHolding(BaseModel):
    # existing fields unchanged
    regime_overlay: DigestHoldingRegimeOverlay | None = None


class DigestPortfolio(BaseModel):
    # existing fields unchanged
    regime_overlay_summary: dict[str, int] = {}
```

- [ ] **Step 4: `_build_portfolio`가 같은 날짜 감사 행을 ticker별로 연결**

같은 ticker에 자동후보 감사 행이 여러 개면 실제 보유 수량의 근거로 선택된 최신 BUY OrderLog와 `position_key=order:<id>`가 일치하는 행만 연결한다. 전략매매 BOUGHT ticker에는 감사 행을 추정 생성하지 않고 `regime_overlay=None`을 유지한다.

- [ ] **Step 5: 운영 설정과 코드 색인 갱신**

`docs/OPERATIONS_CONFIG.md`에 세 모드, 기본 shadow, 3일 신선도, enforce 승인 조건과 롤백(`shadow`로 복귀 후 재시작)을 적는다. `index.md`의 보유 장세 오버레이 위치를 `maps/risk/holding_regime_overlay.py`로 추가한다. `maps/ops/CLAUDE.md`에는 청산 순서와 digest 감사 경로를 추가한다.

- [ ] **Step 6: digest·문서 정합성 테스트 통과 확인**

Run: `python -m pytest tests/test_daily_digest.py tests/test_docs_index.py -q`

Expected: PASS.

- [ ] **Step 7: Task 4 커밋**

```powershell
git add maps/api/schemas.py maps/ops/daily_digest.py tests/test_daily_digest.py docs/OPERATIONS_CONFIG.md maps/ops/CLAUDE.md index.md
git commit -m "feat: expose holding regime overlay audit"
```

---

### Task 5: 회귀 검증과 안전한 운영 인계

**Files:**
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: Tasks 1–4 전체 구현과 테스트 결과.
- Produces: 배포 가능한 shadow 모드 변경 묶음과 enforce 전환 금지 조건이 명시된 HANDOFF.

- [ ] **Step 1: 집중 테스트 실행**

```powershell
python -m pytest tests/test_holding_regime_overlay.py tests/test_scheduler.py tests/test_plan_exits.py tests/test_exit_order_price_record.py tests/test_strategy_trade.py tests/test_daily_digest.py tests/test_settings.py tests/test_migrations.py -q
```

Expected: all PASS.

- [ ] **Step 2: 시장 패키지와 전체 회귀 테스트 실행**

```powershell
python -m pytest tests -q
python -m pytest maps/tests -q
python -m compileall maps -q
git diff --check
alembic heads
```

Expected: all tests PASS, compile and diff checks return exit code 0, and Alembic reports only `0028_holding_regime_audit (head)`.

- [ ] **Step 3: shadow 재생으로 예상 action 분포 확인**

최근 보유 이력과 `market_regime_log`를 읽는 read-only 스크립트 또는 테스트 fixture로 최소 20거래일을 재생한다. 다음 불변식을 검사한다.

```text
legacy/missing entry context -> EXIT 0건
contrarian_quality 자동후보 -> EXIT 0건
multi_asset 자동후보 -> EXIT 0건
strategy_trade BOUGHT -> overlay audit 0건, 기존 브래킷 관리 유지
EXIT -> 항상 서로 다른 최근 2개 장세 관측에서 불리 조건 확인
기존 stop_loss/take_profit/strategy_exit 건수와 reason은 변경 없음
```

실제 주문을 제출하지 않도록 `MAPS_HOLDING_REGIME_OVERLAY_MODE=shadow`와 mock broker를 사용한다.

- [ ] **Step 4: HANDOFF에 구현·검증·운영 상태 기록**

다음을 수치와 커밋 해시로 기록한다.

```text
현재 운영 모드: shadow
집중/전체 테스트 결과
최근 20거래일 HOLD/WATCH/EXIT 분포
마이그레이션 head
운영 enforce 미승인 상태
enforce 전환 전 사용자 확인 필요
```

- [ ] **Step 5: shadow 코드 최종 커밋**

```powershell
git add HANDOFF.md
git commit -m "docs: hand off holding regime overlay shadow rollout"
```

- [ ] **Step 6: 배포 시 DB 백업·마이그레이션·shadow 확인**

운영 배포 권한을 별도로 받은 경우에만 기존 런북대로 PostgreSQL 전체 백업을 만든 후 `alembic upgrade head`, 서비스 재시작, `/health` 200을 확인한다. 운영 `.env`는 `MAPS_HOLDING_REGIME_OVERLAY_MODE=shadow`로 유지한다. `enforce` 변경이나 자동 `regime_exit` 실주문 시험은 이 단계에 포함하지 않는다.
