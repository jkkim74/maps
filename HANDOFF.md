# HANDOFF

> 작성일: 2026-07-27 (월, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제: 7/24 승격 데드락 수정의 실전 검증 → 첫 매수 체결 확인, 그 과정에서 드러난
>       **UTC/KST 날짜 경계 버그**를 규명·수정·배포. 첫 손절까지 완주 확인.
> 이전 핸드오프(승격 데드락 해소·워치리스트 실시간 시세, 7/24): git `226a468`·`99e7d3d` 참고.

## Goal

7/24 핸드오프 Next Step #1 — "7/27(월) 08:55 `order_cycle`에 `submitted_buy_orders > 0`이
나오는지" 확인. 계속 0이었으므로 이것이 승격 데드락 수정의 실질 검증이었다.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-26 23:55:18`처럼 전날로 찍힌다. psql로
"오늘 주문" 조회할 때 `WHERE created_at >= '오늘'` 하면 안 나온다. 이번 버그의 근원이다.

## Current Progress (2026-07-27, 전부 완료·배포됨 — 커밋 `240290a`)

### 1. 데드락 수정 검증 성공

```
08:55:19 order_cycle: success
  submitted_buy_orders: 1   ← 배포 후 계속 0이던 값
  skipped_buy_orders: 912
```
- 주문: **donchian_v2 / 475150 SK이터닉스 / 52주 / 지정가 81,800 / `mode=mock`**
  (`_order_log_mode()` 라벨 수정도 정상 동작 확인)
- 912건 스킵은 정상 — `preferred_regime_mismatch:mixed`, `entry limit reached (ratio=0.25)`.
  후보 전량을 훑고 장세 상한만큼만 낸 결과다.

### 2. 첫 진단은 틀렸다 (기록용 — 같은 함정 반복 방지)

로그에 `pykrx 배치 현재가 조회 실패`가 **하루 163회**, 이어서
`장중 현재가 조회 실패 — 손절 판단에 전일 종가 사용`이 매분 찍히고 있었다.
7/24 종가 80,900 vs 실제 62,200(-23%)이라 "손절이 전일 종가로 판단돼 발동 못 한다"고 결론냈는데
**틀렸다**:

- `KISAdapter`는 `update_prices()`를 **오버라이드하지 않는다** → base 구현이 no-op이라
  pykrx로 받은 가격은 KIS 경로에서 애초에 아무 데도 안 쓰인다.
- `_submit_exit_orders()`가 실제로 쓰는 값은 `position.current_price`이고, 이건
  `_broker_position_details()` → KIS 잔고 API(`prpr`)에서 **실시간으로** 온다.
  운영에서 직접 확인: `475150 qty=52 avg=79500.0 current=62200.0` — 시세는 처음부터 정확했다.

즉 저 WARNING 문구는 KIS 경로에서 **오해를 부르는 메시지**다. (Next Steps 2번 참고)

### 3. 진짜 원인 — `_reconcile_same_day_buys`의 UTC/KST 경계

`_submit_exit_orders()`는 진입 기록으로 `status in (filled, partially_filled)`인 BUY만 찾는다.
그런데 유일한 행(id 44)이 `pending`이라 **진입가를 못 찾아 스킵**했다 — 이게 매분 찍히던
`skipped_sell_orders: 1`의 정체다. 가격 문제가 아니었다.

그 `pending`이 안 풀린 이유:

```python
# maps/execution/order_manager.py _reconcile_same_day_buys (수정 전)
today_start = datetime.combine(date.today(), dt_time.min)   # 2026-07-27 00:00 naive
.filter(OrderLog.created_at >= today_start)                  # 행은 07-26 23:55:18 (UTC) → 제외
```

브로커 증거는 멀쩡히 있었다 (운영에서 직접 호출해 확인):
```
get_same_day_buys()      → {'475150': SameDayBuy(quantity=52, avg_price=79500.0)}   ✅
get_daily_order_results() → []      ← KIS VTS가 장전 주문을 CCLD에서 누락(코드 주석이 이미 예견)
```
`sync_broker_state()`는 이미 KST 보정(`- timedelta(hours=9)`)을 하고 있었는데
`_reconcile_same_day_buys`에만 반영이 안 돼 있었다.

**08:55 스케줄러 매수는 전부 이 버그를 맞는다.** 지금까지 매수가 0건이라 안 드러났을 뿐이다.

### 4. 조치 (커밋 `240290a`, 429 passed, 배포·검증 완료)

- `order_manager.py`: **`_kst_today_start_utc()`** 헬퍼 신설 → `_reconcile_same_day_buys`,
  `sync_broker_state`, **`_raise_if_duplicate_active_order`** 3곳이 동일 기준 사용.
- **곁다리 발견**: `_raise_if_duplicate_active_order`도 같은 naive 경계를 써서
  **08:55 주문에 대한 중복 주문 방지가 통째로 무력화**돼 있었다. 함께 수정.
- `scheduler.py`: `_fetch_intraday_prices(tickers, broker=None)`가 **브로커 실시간 시세 우선 →
  pykrx 폴백** 구조로 변경. 기존 pykrx 본문은 `_fetch_intraday_prices_pykrx`로 개명.
  경고 조건도 수정 — 기존엔 **전량 실패일 때만** 경고했으나 일부만 조회돼도 나머지는
  전일 종가를 쓰게 되므로 미조회 티커 목록과 함께 경고한다.
- 테스트 4건 추가 (브로커 우선/폴백/예외 흡수, **08:55 KST 체결 반영 회귀 테스트**).

### 5. 운영 실측 — 첫 손절까지 완주

배포(10:55) 직후 다음 `broker_sync`에서 전 구간이 연쇄 동작:
```
10:56:46  장중 현재가 갱신: 1/1종목                        ← 매분 "조회 실패"였던 자리
10:56:47  Exit submitted [donchian_v2 475150]: stop_loss current=60800 stop=62867
10:56:50  broker_sync: updated_orders=1, submitted_sell_orders=1, skipped_sell_orders=0
```
| id | side | qty | order_price | fill_price | fill_qty | status |
|---|---|---|---|---|---|---|
| 44 | buy | 52 | 81,800 | **79,500** | **52** | **filled** ← `pending/0`이었음 |
| 45 | sell | 52 | 60,800 | 60,800 | 52 | filled |

- 손절가 62,867 = ATR 손절(entry 79,500 − 2.0 × ATR14 8,316). 고정 10% 손절은 71,550.
- 실현손실 **-972,400원 (-23.5%)**, 현금 86,168,223 → 85,192,174. 모의계좌라 실손실 없음.
- **`mock_months` 축적이 오늘부터 시작된다** (`fill_qty > 0`인 BUY가 처음 생겼다).

손실은 났지만 매수→체결반영→손절→청산이 설계대로 돈 **첫 완주 사이클**이다.

## What Worked

- **브로커 API를 운영에서 직접 호출해 "증거는 있는데 조회가 안 됨"을 분리한 것**이 결정적이었다.
  `get_same_day_buys()`가 52주를 정확히 반환하는 걸 눈으로 본 순간 원인이 브로커/네트워크가
  아니라 **DB 쿼리 경계**로 좁혀졌다.
  ```bash
  cd /opt/maps && ./.venv/bin/python -c "from maps.execution.kis_adapter import KISAdapter; ..."
  ```
- 로그의 `skipped_sell_orders: 1`을 "왜 스킵인가"로 파고들어 `_submit_exit_orders`의
  `entries.get(ticker) is None` 분기까지 코드로 따라간 것. 7/24의 `skipped=0 vs >0` 구분과 같은 방법.
- 배포 후 60초 안에 로그로 실동작을 확인한 것. systemd 재기동만 믿지 않는다.

## What Didn't Work / 주의

- **WARNING 문구를 실제 영향 경로로 착각했다.** `장중 현재가 조회 실패 — 손절 판단에 전일 종가
  사용`은 KIS 경로에선 사실이 아니다(`update_prices`가 no-op). 경고 메시지를 보면
  **그 값이 실제로 어디서 소비되는지 코드로 확인**하고 결론 낼 것. 이번엔 어댑터의
  메서드 오버라이드 여부(`grep "def update_prices" kis_adapter.py` → 없음)가 판별점이었다.
- **`date.today()` + UTC 저장 컬럼 조합은 이 코드베이스의 상습 함정이다.** 08:55 KST 주문이
  전날 UTC로 찍히기 때문에 naive 경계는 반드시 하루를 통째로 놓친다. 새 쿼리를 쓸 때
  `_kst_today_start_utc()`를 쓸 것.
- 운영 psql로 "오늘 주문" 조회 시 `WHERE created_at >= '2026-07-27'`은 빈 결과를 준다.
  실제로 이번에 0 rows를 보고 잠깐 "주문이 DB에 없다"고 오판했다. `ORDER BY id DESC LIMIT n`이 안전.
- `order_log`에 `price` 컬럼은 없다 — `order_price` / `fill_price`다.
- `journalctl`에 `broker_sync`가 60초마다 찍혀 grep이 묻힌다 → `| grep -v broker_sync` 필수.
- **`analyze` 픽 0건과 스케줄러 주문은 완전히 다른 파이프라인이다** (7/24부터 이월, 혼동 금지):
  - `analyze`(cron 16:00) → `analysis_pick` → 워치리스트. 게이트 R:R ≥ 2.0. **7/8 이후 0건.**
  - 스케줄러(16:50 후보생성 → 08:55 주문) → `candidate_snapshot` → `order_log`.

## Next Steps

1. **ATR 손절 규칙이 실거래와 백테스트에서 다르다 (신규 발견, 미수정).**
   - `live_rules.py` 주석: "실제 손절가 = `max(고정%손절, ATR 손절)` 중 **넓은 쪽**을 선택한다"
   - `backtest/portfolio_replay.py:280`: `min(stop_from_signal, atr_stop)` — 넓은 쪽 (의도대로)
   - `ops/scheduler.py:1964`, `api/risk.py:200`: `atr_stop_price(...) **or** stop_loss_price(...)`
     — **ATR이 있으면 무조건 ATR**. ATR이 더 좁을 때도 ATR을 쓴다.

   이번엔 ATR(62,867)이 고정(71,550)보다 넓어 결과가 같았지만 규칙이 갈리는 케이스가 있다.
   **백테스트 성과와 실거래 성과가 체계적으로 어긋나는 원인**이 될 수 있으니 어느 쪽이
   정본인지 정하고 통일할 것. 주석·CLAUDE.md도 함께 맞춰야 한다.
2. **`장중 현재가 조회 실패` 경고 문구 정리** — KIS 경로에선 손절에 영향이 없는데 마치
   치명적인 것처럼 읽힌다(실제로 이번에 오진을 유발). 문구를 정확히 고치거나,
   `KISAdapter.update_prices()`를 구현해 실제로 의미가 생기게 할 것.
3. **pykrx 배치 조회가 장중 상시 실패한다** (오늘 163회). 지금은 브로커 실시간이 1순위라
   무해하지만, `market/regime.py`도 KRX 실패 → yfinance 폴백 중이다. KRX 접근 자체가
   막힌 것인지 확인 필요 (`krx-fundamental-blocked-naver-fallback` 메모와 연관 가능).
4. **내일(7/28) 08:55 재확인** — 매수가 다시 나가는지, 그리고 이번엔 `broker_sync`가
   같은 날 안에 `filled`로 반영하는지. 오늘은 배포 후 수동 확인이었다.
   ```
   sudo journalctl -u maps --no-pager | grep -v broker_sync | grep "order_cycle: success" | tail -1
   ```
5. **~2026-10월말: `mock_months ≥ 3` 충족 시점** 재확인. 단 **점수 34.7 < 임계값 75는 그대로**라
   승격은 여전히 안 된다 — Live Small 차단만 풀린다. 점수 개선은 별도 과제.
6. **새 APK 폰 설치·확인**(7/22부터 이월) — 홈 장세 배너, 주문 탭 예정주문, 워치 체결 전 현재가.
   모바일 UI는 systemd 배포로 안 나감, APK 재빌드 필요(JDK 21).
   `apps/mobile/google-services.json`이 워크트리 루트에 untracked로 있는데, 크래시 방지에
   필요한 위치는 `apps/mobile/android/app/google-services.json`이다.
7. **모바일 완료 탭(익절/손절)** 미반영(7/14부터 이월) — `WatchlistScreen.tsx`에 `?state=CLOSED` 탭.
   오늘 첫 손절 청산이 생겼으므로 이제 표시할 데이터가 실제로 있다.
8. 이월: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리,
   네트워크 테스트 mock화, 서명 릴리스 APK(`npm run build:apk:release`).
9. `order_log_backup_20260724`(42행) — 7/24 mode 백필 백업. 문제없으면 `DROP TABLE` 가능.

## 핵심 파일 맵

- **날짜 경계(이번 변경)**: `maps/execution/order_manager.py` — `_kst_today_start_utc()`,
  `_reconcile_same_day_buys`, `sync_broker_state`, `_raise_if_duplicate_active_order`.
- **장중 시세(이번 변경)**: `maps/ops/scheduler.py` — `_fetch_intraday_prices`(브로커 우선),
  `_fetch_intraday_prices_pykrx`(폴백), `sync_broker_state`(호출부·경고).
- **손절 판정**: `maps/ops/scheduler.py:_submit_exit_orders`(진입 기록 조회 = `filled`/
  `partially_filled`, `expired` 폴백), `maps/strategy/live_rules.py`(`_STOP_LOSS_PCTS`,
  `_ATR_MULTIPLIERS`), `maps/backtest/portfolio_replay.py`(백테스트 쪽 규칙).
- **승격**: `maps/promotion/gate.py`(`_MIN_MOCK_MONTHS_FOR_LIVE_SMALL=3`),
  `maps/ops/scheduler.py`(`_order_candidates` 단계 게이트, `_mock_track_months`),
  `maps/common/settings.py`(`is_paper_account`), `maps/common/constants.py`.
- **테스트**: `tests/test_sync_fill_reconciliation.py`(체결 동기화 회귀),
  `tests/test_scheduler.py`(H-2 장중 시세), `tests/test_order_manager.py`,
  `tests/test_candidate_snapshot_scheduler.py`, `tests/test_order_preview.py`.
- **모바일**: `apps/mobile/src/{api.ts,format.ts,App.tsx}`,
  `screens/{HomeScreen,OrdersScreen,WatchlistScreen}.tsx`, `hooks/useOrderPreview.ts`.
- **analyze 자동화(서버)**: `/etc/cron.d/maps-analyze`, `/opt/maps/scripts/run_analyze_cron.sh`,
  `/opt/maps/.claude/{commands/analyze.md,agents/*.md}`, `scripts/load_analysis_picks.py`.
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
