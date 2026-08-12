# strategy/

매매 전략 정의 패키지. 운영 전략, 연구 격리 후보, 그리고 전략이 만들어 낸 신호를 점수·가격·
손절로 바꾸는 공용 규칙을 포함한다.

## Directory structure

```
strategy/
├── __init__.py                 # 빈 패키지 마커
├── base.py                     # BaseStrategy — 추상 베이스 + PositionExitPolicy
├── catalog.py                  # 화면용 전략 설명 (산문만)
├── live_rules.py               # 실거래 손절 — effective_stop_price() 가 정본
├── holding_type.py             # CORE/SWING/TRADING/WATCH/BAN 분류
├── price_calculator.py         # 코스톨라니 이중 목표가·손절가 산출
├── score_features.py           # 실측 가능한 점수 컴포넌트만 파생
├── scoring.py                  # legacy / 전략인지 최종 점수 계산기
├── pullback_v2.py              # PullbackV2Strategy
├── pullback_v3.py              # PullbackV3Strategy (주력)
├── pullback_v3_3.py            # PullbackV33Strategy (2R+트레일링 연구 격리)
├── ath_breakout_v1.py          # ATHBreakoutV1Strategy
├── ath_breakout_v2.py          # ATHBreakoutV2Strategy
├── contrarian_quality_v1.py    # ContrarianQualityAccumulationV1Strategy
├── donchian_v1.py              # DonchianV1Strategy
├── donchian_v2.py              # DonchianV2Strategy
└── multi_asset_trend_v1.py     # MultiAssetTrendV1Strategy
```

## base.py — BaseStrategy (ABC)

| 멤버 | 종류 | 설명 |
|---|---|---|
| `strategy_id` | 클래스 변수 | 고유 전략 ID (e.g. `"pullback_v3"`) |
| `strategy_group` | 클래스 변수 | `STRATEGY_GROUP_MAP` 참조 |
| `preferred_regimes` | 클래스 변수 | 이 장세가 아니면 후보 생성에서 진입이 막힌다 |
| `generate_signals(data, params)` | 추상 | `entry_signal`, `exit_signal`, `stop_price` 컬럼 추가 |
| `param_grid()` | 추상 | Plateau 그리드 탐색용 조합 |
| `default_params` | 추상 프로퍼티 | 기본 파라미터 |
| `required_bars(params)` | 구체 | 신호 생성 최소 봉 수 (기본 1) |
| `position_exit_policy()` | 구체 | R 기반 상태형 청산 정책. 기본은 레거시 청산 |

입력 DataFrame 최소 컬럼: `open, high, low, close, volume`.

## live_rules.py — 손절은 여기 하나로만 구한다

| 함수 | 설명 |
|---|---|
| `stop_loss_pct(strategy_id)` | 전략별 고정 손절 비율 |
| `atr_multiplier(strategy_id)` | ATR(14) 손절 배수 |
| `stop_loss_price(...)` | 고정% 손절가 |
| `atr_stop_price(...)` | ATR 기반 손절가 |
| **`effective_stop_price(...)`** | **정본** — 둘 중 넓은(가격이 낮은) 쪽 |

> ⚠️ 청산 판정·**사이징**·화면 표시가 모두 `effective_stop_price()` 를 거쳐야 한다.
> ATR 은 고정% 하한선을 느슨하게만 만들 수 있고 조이지는 못한다. 경로마다 다르면
> 백테스트와 실거래가 어긋난다(2026-07-29: 사이징이 고정%만 써서 포지션이 2배로 잡혔다).
> 예외는 `backtest/portfolio_replay._resolve_stop` 하나뿐이다 — 전략 신호의 `stop_price` 와
> 미등록 전략 폴백이라는 백테스트 전용 입력이 있다.
>
> 손절률·ATR 배수 **숫자는 여기에 옮겨 적지 않는다.** `live_rules.py` 를 읽는다.
> 알 수 없는 전략 ID나 유효하지 않은 진입가에는 `None` 을 돌려준다.

## scoring.py / score_features.py — 후보 점수

| 이름 | 설명 |
|---|---|
| `LegacyFinalScoreCalculator` | 기존 MAPS 랭킹식 (거래대금 factor + 추세강도) |
| `StrategyAwareScoreCalculator` | 전략 유형별 가중치. **없는 입력을 만들어 내지 않는다** |
| `strategy_extra_scores()` | 실제 OHLCV·피드 관측으로 뒷받침되는 컴포넌트만 파생 |

`MAPS_STRATEGY_AWARE_SCORING_ENABLED=true` 면 전략인지 계산기가 쓰인다(운영 기본). 결측
컴포넌트는 중립 50으로 채우지 않고 빠지며, 그 사실이 `ops/score_readiness.py` 의 커버리지
판정으로 이어진다.

## 그 밖의 공용 모듈

| 모듈 | 역할 |
|---|---|
| `catalog.py` | `STRATEGY_PROSE` / `STRATEGY_CLASSES`, `describe_strategy()`, `display_name()` |
| `price_calculator.py` | `KostolanyPriceCalculator.calculate()` — trading_target / value_target 분리 |
| `holding_type.py` | `HoldingTypeClassifier.classify()` → 보유 성격별 매매 규칙 |

> ⚠️ 카탈로그에는 **산문만** 넣는다. 손절률·파라미터·선호 장세·MDD 는 코드에서 읽어 오므로
> 값을 복사해 두면 조용히 어긋난다. 새 전략을 `STRATEGY_PROSE`/`STRATEGY_CLASSES` 에
> 등록하지 않으면 `tests/test_strategy_catalog.py` 가 실패한다 — 전략관리 화면에 식별자만
> 뜨는 상태를 막는 장치다.

## 전략 그룹 매핑 (`common/constants.py:STRATEGY_GROUP_MAP`)

| 전략 ID | 그룹 |
|---|---|
| `pullback_v2`, `pullback_v3`, `pullback_v3_3` | `pullback_short` |
| `ath_breakout_v1`, `ath_breakout_v2` | `ath_outlier` |
| `donchian_v1`, `donchian_v2` | `donchian_research` |
| `multi_asset_trend_v1` | `multi_asset` |
| `contrarian_quality_accumulation_v1` | `contrarian_quality` |

> 클래스 파일명은 `contrarian_quality_v1.py` 지만 **전략 ID 는
> `contrarian_quality_accumulation_v1`** 이다. 매핑·등록은 ID 기준이다.

## 구체 전략 등록

`ops/scheduler.py` 의 `_RUNNABLE_STRATEGIES` 에 등록된 전략만 일별 파이프라인에서 실행된다.

`pullback_v3_3` 은 연구 격리 예외다. 콘솔 백테스트와 수동 WFA 에는 등록하지만
`_RUNNABLE_STRATEGIES` 에는 넣지 않는다. 강세장 3구간·전체 기간·WFA/Plateau/MC 를 통과하고
운영용 HWM 영속화를 구현하기 전에는 자동 후보·승격·주문 경로에 넣지 않는다.

새 전략 추가 시 체크리스트:

1. `strategy/` 아래 구체 클래스 파일 생성
2. `live_rules.py` 의 손절 비율·ATR 배수 추가
3. `common/constants.py` 의 `STRATEGY_GROUP_MAP` + `ALLOWED_MDD` 그룹 추가
4. `strategy/catalog.py` 의 `STRATEGY_PROSE` / `STRATEGY_CLASSES` 등록
5. `docs/strategy_guides/` 에 가이드 원고 추가
6. `ops/scheduler.py` 의 `_RUNNABLE_STRATEGIES` 등록 (자동 운영에 넣을 때만)

## 의존성

```
maps.common.constants → STRATEGY_GROUP_MAP, ALLOWED_MDD
maps.market.regime    → preferred_regimes 판정에 쓰는 라벨
maps.backtest.engine  → BacktestEngine (generate_signals 호출)
```
