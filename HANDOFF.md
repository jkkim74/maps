# HANDOFF

> 작성일: 2026-07-27 (월, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제 ①: 7/24 승격 데드락 수정의 실전 검증 → **UTC/KST 날짜 경계 버그** 규명·수정·배포. 첫 손절까지 완주.
> 주제 ②: **일일 매매 기록 블로그 자동 생성** 구현·배포·cron 등록. 3단계(다이제스트 → 서술 → 검증).
> 주제 ③: **KRX 계정 잠금** 규명·복구. 시스템의 무한 재시도가 스스로 계정을 잠갔다.
> 이전 핸드오프(승격 데드락 해소·워치리스트 실시간 시세, 7/24): git `226a468`·`99e7d3d` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-26 23:55:18`처럼 전날로 찍힌다. psql로
"오늘 주문"을 `WHERE created_at >= '오늘'`로 조회하면 **0 rows가 나온다**(실제로 오판했다).
`ORDER BY id DESC LIMIT n`이 안전하고, 코드에서는 `order_manager.kst_day_bounds_utc()`를 쓸 것.

---

# 주제 ① UTC/KST 경계 버그 (커밋 `240290a`, 배포 완료)

## 1. 데드락 수정 검증 성공

```
08:55:19 order_cycle: success   submitted_buy_orders: 1   ← 계속 0이던 값
```
donchian_v2 / 475150 SK이터닉스 / 52주 / 지정가 81,800 / `mode=mock`.
912건 스킵은 정상(`preferred_regime_mismatch:mixed`, `entry limit reached ratio=0.25`).

## 2. 첫 진단은 틀렸다 (같은 함정 반복 방지용 기록)

`pykrx 배치 현재가 조회 실패`가 하루 163회, 이어 `장중 현재가 조회 실패 — 손절 판단에
전일 종가 사용`이 매분 찍혀 "손절이 전일 종가로 판단돼 발동 못 한다"고 결론냈는데 **틀렸다**:

- `KISAdapter`는 `update_prices()`를 **오버라이드하지 않는다** → base가 no-op이라
  pykrx로 받은 가격은 KIS 경로에서 아무 데도 안 쓰인다.
- `_submit_exit_orders()`가 실제로 쓰는 값은 `position.current_price`이고 KIS 잔고
  API(`prpr`)에서 **실시간으로** 온다. 운영 확인: `475150 avg=79500 current=62200` — 정확했다.

**교훈**: WARNING 문구를 보면 그 값이 실제로 어디서 소비되는지 코드로 확인하고 결론낼 것.
이번엔 `grep "def update_prices" kis_adapter.py` → 없음이 판별점이었다.

## 3. 진짜 원인 — `_reconcile_same_day_buys`의 naive 날짜 경계

`_submit_exit_orders()`는 진입 기록으로 `status in (filled, partially_filled)`인 BUY만 찾는데
유일한 행(id 44)이 `pending`이라 **진입가를 못 찾아 스킵**했다 — 이게 `skipped_sell_orders: 1`이다.

```python
today_start = datetime.combine(date.today(), dt_time.min)  # 2026-07-27 00:00 naive
.filter(OrderLog.created_at >= today_start)                 # 행은 07-26 23:55:18 UTC → 제외
```

브로커 증거는 멀쩡했다: `get_same_day_buys() → {'475150': qty=52, avg=79500}` ✅,
`get_daily_order_results() → []` (KIS VTS가 장전 주문을 CCLD에서 누락 — 코드 주석이 이미 예견).

## 4. 조치 (429 passed)

- `order_manager.py`: **`kst_day_bounds_utc(ref_date)`** 신설 → `_reconcile_same_day_buys`,
  `sync_broker_state`, **`_raise_if_duplicate_active_order`**, `daily_digest` 전부 동일 기준.
- **곁다리**: `_raise_if_duplicate_active_order`도 같은 버그라 **08:55 주문 중복 방지가
  통째로 무력화**돼 있었다. 함께 수정.
- `scheduler.py`: `_fetch_intraday_prices`가 브로커 실시간 우선 → pykrx 폴백. 부분 조회도 경고.

## 5. 실측 — 첫 손절까지 완주

```
10:56:46 장중 현재가 갱신: 1/1종목
10:56:47 Exit submitted [donchian_v2 475150]: stop_loss current=60800 stop=62867
10:56:50 broker_sync: updated_orders=1, submitted_sell_orders=1, skipped_sell_orders=0
```
| id | side | fill_price | fill_qty | status | exit_reason |
|---|---|---|---|---|---|
| 44 | buy | **79,500** | **52** | **filled** ← `pending/0`이었음 | — |
| 45 | sell | 60,800 | 52 | filled | stop_loss |

손절가 62,867 = ATR 손절(79,500 − 2.0 × ATR14 8,316). 고정 10% 손절은 71,550.
실현손실 **-972,400원 (-23.5%)**, 현금 86,168,223 → 85,192,174. **`mock_months` 축적 개시.**

---

# 주제 ② 일일 매매 기록 블로그 (커밋 `2c306ca`~`a10c87b`, 배포·cron 등록 완료)

## 설계 — 객관성은 문체가 아니라 구조로 담보한다

```
1단계  maps/ops/daily_digest.py    → DB에서 하루치 JSON 조립 (수치의 유일한 출처)
2단계  claude -p /blog             → 그 JSON만 읽고 Markdown 작성 (조회 도구 없음)
3단계  verify_blog_numbers.py      → 글의 숫자를 다이제스트와 대조해 보고
```

LLM에 DB 조회를 맡기면 매일 다른 쿼리를 짜서 같은 날 수치가 흔들린다. 미측정 항목은
`measured=false`로 내려보내 글에서 "미측정"으로 쓰게 강제한다.

### ⚠️ `--allowedTools` 는 도구를 제한하지 않는다 (실측)

처음엔 `--allowedTools Read Write`만 주면 된다고 봤는데 **틀렸다.** 첫 실행 로그에
`▶ Bash:` 가 찍혀서 파고든 결과:

| 시도 | 결과 |
|---|---|
| `--allowedTools Read Write` | Bash **그대로 실행됨** — 배제 목록이 아니라 **자동승인** 목록이다 |
| `+ --disallowedTools Bash` | Bash는 막히나 **`ToolSearch`로 `Monitor`를 불러와 셸 우회** |
| 실행·조회·위임 계열 전면 차단 | 에이전트가 "명령 실행 도구가 없다"고 보고 ✅ |

`ToolSearch`가 지연 도구로 가는 통로라 이걸 빼면 나머지 차단이 무의미하다. 최종 차단 목록은
`run_blog_cron.sh`의 `BLOG_DENY` 배열 참고. **차단 목록은 본질적으로 블랙리스트이므로
예방만 믿지 않고 3단계 검증을 함께 둔다.** 파생값(차이·비율)은 정상 서술이라 실패로
처리하지 않고 보고만 한다 — 실제 글에서 미매칭 8개가 나왔고 전부 파생값·건수였다.

| 파일 | 역할 |
|---|---|
| `maps/ops/daily_digest.py` | 7개 섹션 조립, 섹션별 오류 흡수(`errors[]`) |
| `maps/api/daily_digest.py` | `GET /api/v1/daily-digest?date=` (검증·디버깅) |
| `scripts/run_blog_cron.sh` | digest 생성 → claude 호출 → `blog/YYYY-MM-DD.md` |
| `.claude/commands/blog.md` | 6개 섹션 지시 + 금지 규칙 |
| `maps/api/blog.py`, `templates/blog.html` | 대시보드 `/blog` (경로순회 차단, 무의존 MD 렌더러) |

## 부수 수정 1 — `order_log.exit_reason` (마이그레이션 `0012`)

왜 팔았는지가 journald에만 있고 DB엔 없었다. `submit_exit(exit_reason=)`로 손절 경로와
브래킷 청산 양쪽 연결. 값: `stop_loss|plan_stop|emergency_stop|trailing_stop|take_profit|strategy_exit`.
**과거 청산분은 백필 불가**(journald 파싱 필요). 7/27 45번 행만 근거 확인 후 수동 보정함.

## 부수 수정 2 — 강세업종을 매매와 분리해 항상 관측

`MAPS_SECTOR_FILTER_ENABLED`는 "이 결과로 후보 유니버스를 자를지"만 정한다. 계산 자체는
DB만으로 결정적이므로 **꺼져 있어도 기록은 남긴다**. `applied_to_trading: false`로 구분하고,
선택기는 실제 매매 경로와 동일하게 골라 "켰다면 이렇게 됐을 것"이 정확하게 한다.
필터 활성화를 판단할 근거 데이터가 지금 없는데, 이걸로 매일 쌓인다.

## 왜 업종 필터가 꺼져 있나 (조사 결과)

1. **의도적** — `e042c69`(6/19)에서 처음부터 `false` 기본값. 코스톨라니 14단계 전체가
   같은 패턴(구현 머지 → 실데이터 → 백테스트 → 단계적 활성화). 업종 필터는 5단계.
2. **데이터 없음은 해소됨** — 과거 메모의 "sector 전량 NULL"은 **로컬 DB** 얘기다.
   운영은 `2775/2781 = 99.8%` 채워져 있다.
3. **진짜 이유: 점수의 절반이 가짜** — `SectorScorer` 가중치 7개 중 `_score_from_db`가
   채우는 건 3개(momentum_20d 0.20 / momentum_60d 0.15 / flow 0.15 = **0.50**)뿐.
   나머지 0.50은 중립 50이고, **단일 최대 가중치인 `earnings_revision` 0.25가 통째로 가짜**다.
   운영 실측: 상위 5개 점수가 49.5~54.0에 뭉치고, **전기·전자가 m20=0.0(최하)인데도 선정**된다.
4. 단, `maps_sector_kostolany_mode_enabled`도 `false`라 **지금 마스터 플래그만 켜면
   레거시 선택기**(자리표시자 없는 순수 기간 수익률 순위)가 돈다. 두 선택기는 5개 중 2개만 겹친다.

## 관측 켜자마자 나온 발견

레거시 선택기는 **임계값 없이 상위 N개를 자르기만** 한다. 7/24 실측에서 "강세업종" 5개 중
하위 2개가 마이너스였다(부동산 -0.98%, 농업임업어업 -3.27%). 필터를 켰다면 -3.27% 업종
종목도 매수 후보에 남았을 것이다. **활성화 전에 손볼 지점.**

---

# 주제 ③ KRX 계정 잠금 (7/27, 복구 완료 — 재발 방지는 미완)

## 사건 경위

- 증상: `pykrx 배치 현재가 조회 실패`가 하루 163회. `market/regime.py`도 yfinance 폴백.
- 1차 원인: 계정 `jack68` 비밀번호 만료 → `_error_code=CD010`(패스워드 변경 필요).
- **2차 원인(더 중요): 시스템이 스스로 계정을 잠갔다.** 만료된 비밀번호로 **158회** 로그인을
  재시도했고 그 누적이 오류횟수 잠금을 유발했다 → `_error_code=CD007 패스워드 오류수에 의한 잠금`.
  이 상태에서는 **올바른 비밀번호를 넣어도 CD007만 반환**되므로 "비밀번호가 틀렸다"로 오진하기 쉽다.
- 진단 방법 — 래퍼가 삼키는 원본 코드를 직접 봐야 한다:
  ```python
  # pykrx/website/comm/auth.py 의 login_krx 를 그대로 재현해 _error_code 를 찍는다
  # CD001=정상 / CD007=잠금 / CD010=변경필요 / CD011=중복로그인
  ```
- 복구 순서(중요): 잠금 해제만 하고 두면 **16:40·16:50·17:10·18:00·18:30 잡이 곧바로 재시도**해
  비밀번호가 틀릴 경우 몇 분 만에 재잠금된다. 실제로 이 순서로 처리했다:
  1. `.env`의 `KRX_ID`/`KRX_PW`를 `#LOCKED_` 접두사로 주석 처리 → 자동 로그인 시도 정지
     (pykrx가 환경변수 없음으로 조기 종료, HTTP 요청 자체를 안 보낸다). 장세는 yfinance 폴백 유지.
  2. 사용자가 krx.co.kr에서 잠금 해제 + 비밀번호 재설정
  3. **되살리기 전에** 1회 수동 로그인 검증 → `CD001 정상` 확인
  4. `sed -i 's|^#LOCKED_KRX_|KRX_|' .env && sudo systemctl restart maps`
- 복구 확인: KOSPI 943행 / KOSDAQ 1822행 / 지수 6행, 배치 현재가 정상 반환.
- 백업: `/opt/maps/.env.bak_krxlock_*`.

---

## What Worked

- **브로커 API를 운영에서 직접 호출해 "증거는 있는데 조회가 안 됨"을 분리**한 것이 결정적이었다.
  `get_same_day_buys()`가 52주를 정확히 반환하는 걸 본 순간 원인이 DB 쿼리 경계로 좁혀졌다.
  ```bash
  cd /opt/maps && ./.venv/bin/python -c "from maps.execution.kis_adapter import KISAdapter; ..."
  ```
- 로그의 `skipped_sell_orders: 1`을 "왜 스킵인가"로 파고들어 `entries.get(ticker) is None`
  분기까지 코드로 따라간 것. 7/24의 `skipped=0 vs >0` 구분과 같은 방법.
- 블로그를 **도구 권한으로 제약**한 것. 프롬프트로 "지어내지 마"라고 쓰는 것보다,
  수치를 가져올 도구를 안 주는 쪽이 확실하다.
- 플래그를 켜는 대신 **관측과 적용을 분리**한 것. 매매를 안 건드리고 근거를 모은다.

## What Didn't Work / 주의

- **WARNING 문구를 실제 영향 경로로 착각했다** (주제 ① 2절 참고).
- **`date.today()` + UTC 저장 컬럼은 이 코드베이스의 상습 함정.** 새 쿼리는
  `kst_day_bounds_utc()`를 쓸 것.
- `order_log`에 `price` 컬럼은 없다 — `order_price` / `fill_price`.
- **`.claude/commands/`가 gitignore돼 있었다.** 서버 cron이 실행하는 실행물인데 버전관리
  밖이라 `analyze.md`가 서버에서만 갱신돼 로컬보다 **최신**이었다(0종목 기록 절차 추가분).
  서버 버전을 정본으로 커밋하고 `analyze.md`/`blog.md`만 예외 처리했다.
  배포 시 서버의 untracked 파일을 먼저 치워야 `git pull`이 통과한다.
- `journalctl`에 `broker_sync`가 60초마다 찍혀 grep이 묻힌다 → `| grep -v broker_sync` 필수.
- **`analyze` 픽 0건과 스케줄러 주문은 다른 파이프라인이다** (혼동 금지):
  - `analyze`(cron 16:00) → `analysis_pick` → 워치리스트. 게이트 R:R ≥ 2.0. 7/8 이후 0건.
  - 스케줄러(16:50 후보생성 → 08:55 주문) → `candidate_snapshot` → `order_log`.

---

## 7/27 중 해결된 것 (다음 세션이 다시 파지 않도록)

- **서버 claude OAuth 재인증 완료.** 7/24부터 만료돼 analyze cron이 죽어 있었다.
  블로그 수동 1회 성공 확인 후 **cron 등록 완료**: `/etc/cron.d/maps-blog`, `30 18 * * 1-5`.
- **`MAPS_KOSTOLANY_REGIME_ENABLED=true` 적용.** 시장 팩터 5개가 이제 채워진다.
  안전 확인 근거: `composite` 소비처는 `api/market.py`(화면 표시)뿐이고 진입 판단
  (`entry_limit_ratio`·`market_mode`)은 `regime`/`vol_regime`만 본다.
  단 `psychology`/`liquidity`는 여전히 자리표시자 50이고 digest가 `measured=false`로 표기한다.
- **KRX 계정 복구** — 주제 ③ 참고. 잠금 해제·비밀번호 재설정 후 `.env` 복구, pykrx 정상.

---

## Next Steps

### 🔴 재발 방지 (미완)

1. **KRX 로그인 실패에 백오프·회로차단기가 없다.** 자격증명이 만료되면 시스템이 스스로
   계정을 잠근다(위 사건). 스케줄러가 돌 때마다 무한정 재시도한다.
   필요한 것: 연속 실패 N회 시 일정 시간 로그인 시도 중단, 그동안 yfinance 폴백 유지.
   오늘 하루 실제로 폴백만으로 돌았고 장세 판정에 문제가 없었으므로 안전한 설계다.
   지금은 계정이 정상이라 재현 검증이 어렵다 — mock 레벨에서 테스트할 것.
   `pykrx`가 벤더 코드라 우리 쪽 래퍼(`data/krx_adapter.py`, `market/regime.py`)에서 감싸야 한다.

### 판단 필요

2. **업종 필터 활성화** — 위 "왜 꺼져 있나" 참고. 관측 데이터가 쌓인 뒤 판단.
   활성화 전 **레거시 선택기의 임계값 부재**부터 손볼 것(7/24 관측에서 "강세업종" 5개 중
   하위 2개가 마이너스 수익률이었다).

### 코드 정합성

3. **ATR 손절 규칙이 실거래와 백테스트에서 다르다 (미수정).**
   - `live_rules.py` 주석: "`max(고정%, ATR)` 중 **넓은 쪽**"
   - `backtest/portfolio_replay.py:280`: `min(stop_from_signal, atr_stop)` — 넓은 쪽 (의도대로)
   - `ops/scheduler.py:1964`, `api/risk.py:200`: `atr_stop_price(...) **or** stop_loss_price(...)`
     — **ATR이 있으면 무조건 ATR**. ATR이 더 좁을 때도 ATR을 쓴다.

   이번엔 ATR(62,867)이 고정(71,550)보다 넓어 결과가 같았지만 규칙이 갈리는 케이스가 있다.
   **백테스트와 실거래 성과가 체계적으로 어긋나는 원인**이 될 수 있다. 정본을 정하고 통일할 것.
4. **`장중 현재가 조회 실패` 경고 문구** — KIS 경로에선 손절에 영향이 없는데 치명적으로
   읽힌다(실제로 오진 유발). 문구를 고치거나 `KISAdapter.update_prices()`를 구현할 것.

### 관측/이월

5. **7/28 08:55 재확인** — 매수가 다시 나가는지, 이번엔 `broker_sync`가 같은 날 안에
   `filled`로 반영하는지(오늘은 배포 후 수동 확인이었다).
   `sudo journalctl -u maps --no-pager | grep -v broker_sync | grep "order_cycle: success" | tail -1`
6. **~2026-10월말 `mock_months ≥ 3`** 재확인. 단 **점수 34.7 < 임계값 75**라 승격은 여전히
   안 된다 — Live Small 차단만 풀린다. 점수 개선은 별도 과제.
7. **새 APK 폰 설치·확인**(7/22 이월) — 홈 장세 배너, 주문 탭 예정주문, 워치 체결 전 현재가.
   `apps/mobile/google-services.json`이 워크트리 루트에 untracked인데, 필요한 위치는
   `apps/mobile/android/app/google-services.json`이다. JDK 21 필요.
8. **모바일 완료 탭(익절/손절)** 미반영(7/14 이월) — `WatchlistScreen.tsx`에 `?state=CLOSED` 탭.
   오늘 첫 손절 청산이 생겼으므로 이제 표시할 데이터가 실제로 있다.
9. 이월: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리,
   네트워크 테스트 mock화, 서명 릴리스 APK. `order_log_backup_20260724`(42행) DROP 가능.

---

## 핵심 파일 맵

- **날짜 경계**: `maps/execution/order_manager.py` — `kst_day_bounds_utc()`,
  `_reconcile_same_day_buys`, `sync_broker_state`, `_raise_if_duplicate_active_order`.
- **장중 시세**: `maps/ops/scheduler.py` — `_fetch_intraday_prices`(브로커 우선),
  `_fetch_intraday_prices_pykrx`(폴백).
- **손절 판정**: `scheduler._submit_exit_orders`(진입 기록 = `filled`/`partially_filled`,
  `expired` 폴백), `maps/strategy/live_rules.py`, `maps/backtest/portfolio_replay.py`.
- **블로그**: `maps/ops/daily_digest.py`, `maps/api/{daily_digest,blog}.py`,
  `templates/blog.html`, `scripts/run_blog_cron.sh`(`BLOG_DENY` 차단 목록),
  `scripts/verify_blog_numbers.py`, `.claude/commands/blog.md`.
  출력 `/opt/maps/blog/`(gitignore), cron `/etc/cron.d/maps-blog`.
- **KRX 인증**: `.venv/.../pykrx/website/comm/auth.py` — `login_krx()`가 `_error_code`를
  삼키고 "자격 증명을 확인하세요"로 뭉갠다. 진단하려면 이 함수를 재현해 코드를 직접 찍을 것.
  자격증명은 서버 `.env`의 `KRX_ID`/`KRX_PW`(코드가 아니라 환경변수로만 읽는다).
- **업종**: `maps/market/sector_selector.py` — `SectorScorer._WEIGHTS`(가중치),
  `SectorRegimeSelector._score_from_db`(실제 채우는 입력 3개), `SectorSelector.select_strong_sectors`(레거시).
- **승격**: `maps/promotion/gate.py`(`_MIN_MOCK_MONTHS_FOR_LIVE_SMALL=3`),
  `scheduler`(`_order_candidates`, `_mock_track_months`), `settings.is_paper_account`.
- **테스트**: `tests/test_daily_digest.py`, `tests/test_blog_api.py`,
  `tests/test_sync_fill_reconciliation.py`, `tests/test_scheduler.py`, `tests/test_order_manager.py`.
- **analyze 자동화(서버)**: `/etc/cron.d/maps-analyze`, `scripts/run_analyze_cron.sh`,
  `.claude/commands/analyze.md`, `scripts/load_analysis_picks.py`.
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
