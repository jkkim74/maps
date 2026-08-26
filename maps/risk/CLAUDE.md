# risk/

Kill Switch와 노출 한도를 포함한 리스크 관리 패키지.

## Directory structure

```
risk/
├── __init__.py       # 빈 패키지 마커
├── holding_regime_overlay.py # 보유 종목 HOLD/WATCH/EXIT 순수 shadow 판정
└── manager.py        # RiskManager, KillSwitchEvent, RiskConfig, KillSwitchReason
```

## holding_regime_overlay.py

`evaluate_holding_regime()`은 자동후보 진입 당시 장세와 최근 두 장마감 관측을 비교한다.
같은 불리 원인(`weekly_fail` 또는 `weak_transition`)이 두 관측에 공통으로 지속될 때만
`confirmed=True`다. v1은 감사용 `HOLD/WATCH/EXIT`만 반환하며 주문을 제출하지 않는다.
누락·노후·잘못된 입력은 `HOLD`로 fail-open한다.

## manager.py — 주요 구성 요소

### 상수

| 이름 | 값 | 설명 |
|---|---|---|
| `_CONSEC_FAILURE_THRESHOLD` | `5` | 연속 주문 실패 횟수 → Kill Switch 자동 발동 |

### `RiskConfig` (dataclass)

| 필드 | 기본값 | 설명 |
|---|---|---|
| `daily_loss_limit` | `0.015` | 일일 손실 한도 (1.5%) |
| `mdd_limit` | `0.15` | 포트폴리오 MDD 한도 (15%) |
| `position_size_limit` | `0.10` | 단일 종목 최대 비중 (10%) |

### `KillSwitchReason` (str Enum)

| 값 | 의미 |
|---|---|
| `daily_loss_limit` | 일일 손실 한도 초과 |
| `mdd_limit` | MDD 한도 초과 |
| `manual` | 수동 발동 |
| `risk_metric_breach` | 리스크 지표 위반 |
| `consecutive_failure` | 연속 주문 실패 5회 |

### `KillSwitchEvent` (dataclass)

Kill Switch 발동 이벤트 상태를 담는다. `new_entry_blocked`, `liquidation_approved`, `approved_by` 세 필드가 DB 기록 시 `event_type` 분류(`trigger` / `approved` / `deactivate`)로 변환된다.

### `RiskManager`

```
RiskManager(broker, db, config?, notifier?)
```

#### 공개 메서드

| 메서드 | 호출 시점 | 설명 |
|---|---|---|
| `check_before_order(order, account, daily_pnl)` | 매 주문 직전 | Kill Switch·일일 손실·단일 종목 노출 한도 체크; 위반 시 예외 |
| `on_order_success(strategy_id)` | 주문 체결 성공 후 | 연속 실패 카운터 리셋 |
| `on_order_failure(strategy_id)` | 주문 실패 후 | 카운터 증가; 5회 도달 시 Kill Switch 자동 발동 |
| `check_and_trigger(strategy_id, daily_pnl, current_mdd)` | 주기적 모니터링 | 손실/MDD 초과 시 Kill Switch 발동 |
| `approve_liquidation(strategy_id, approved_by)` | 사용자 승인 후 | 보유 포지션 청산 허용 (사용자 승인 필수) |
| `release(strategy_id, released_by)` | 관리자 조작 | Kill Switch 완전 해제 |
| `deactivate(strategy_id, approved_by)` | — | `release()`의 별칭 |
| `is_new_entry_blocked(strategy_id)` | 참조용 | 메모리 + DB 동시 확인; 외부 deactivate도 반영 |

#### 내부 메서드

| 메서드 | 설명 |
|---|---|
| `_trigger_kill(strategy_id, reason, detail)` | Kill Switch 발동 + 로그 + Slack 알림 |
| `_log_kill_switch(event)` | `kill_switch_log` 테이블에 감사 로그 기록 |
| `_notify_kill_switch(event)` | `SlackNotifier.send_kill_switch()` 호출 |

## Kill Switch 원칙

- **신규 진입 차단**: 자동 (사용자 승인 불필요)
- **보유 포지션 청산**: `approve_liquidation()` 통해 **사용자 승인 필수**
- `is_new_entry_blocked()`는 메모리 캐시와 DB를 모두 확인해 API를 통한 외부 상태 변경도 즉시 반영한다.

## DB 연동

`kill_switch_log` 테이블에 모든 이벤트를 기록. `event_type` 컬럼은 3가지 값:

| event_type | 의미 |
|---|---|
| `trigger` | Kill Switch 발동 (신규 진입 차단) |
| `approved` | 청산 승인 (신규 진입 차단 유지) |
| `deactivate` | Kill Switch 해제 (신규 진입 허용) |

## 의존성

```
maps.common.exceptions   → KillSwitchError, ExposureCapError, UnauthorizedLiquidationError
maps.common.models       → KillSwitchLog
maps.execution.broker_adapter → AccountBalance, BrokerAdapter, Order
maps.ops.notifications   → SlackNotifier
```
