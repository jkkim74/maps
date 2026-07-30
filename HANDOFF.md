# HANDOFF

> 작성일: 2026-07-30 (목, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제 ①: **정본 사이징 첫 실측** — 7/29 핸드오프의 유일한 미실측 항목. 확인 완료.
> 주제 ②: **오발주 사고** — 한 달 된 픽을 무장하자 17초 만에 매수가 나갔다. 원복 완료.
> 주제 ③: **픽 만료(신선도) 처리** — ②의 재발 방지. 구현·배포·검증 완료.
> 주제 ④: **`disarm` 고아 포지션 버그** — 부분 체결을 무시하고 추적을 끊고 있었다.
> 이전 핸드오프(KRX 회로차단기·손절 통일·전략관리 화면·도메인 이전, 7/29): git `cc3d2b3` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요(계속 유효)**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-29 23:55:32`처럼 전날로 찍힌다. psql로
"오늘 주문"을 `WHERE created_at >= '오늘'`로 조회하면 **0 rows가 나온다**.
`ORDER BY id DESC LIMIT n`이 안전하고, 코드에서는 `order_manager.kst_day_bounds_utc()`를 쓸 것.

오늘 커밋 1개: **`5a9e07e`** (14:2x). 배포 완료 — 14:37:29 KST 기동, `active (running)`.
테스트 **568 passed** + 모바일 **18 passed**. 마이그레이션·requirements 변경 없음.

> ⚠️ `5a9e07e` 는 **두 세션의 작업이 합쳐진 커밋**이다. 내 픽 만료 작업과, 다른 세션의
> 블로그(네이버 형식)·리스크 KPI·모바일 작업이 `maps/api/schemas.py` 에서 얽혀
> 파일 단위 분리가 불가능했다. 커밋 본문에서 갈래를 나눠 적었다.
> 그 전 커밋 `f91c719`(7/30 00:13)도 다른 세션 작업이다(종목별 score_reason, 다이제스트 집계).

---

# 주제 ① 정본 사이징 첫 실측 — 7/29 Next Steps 1번 **해소**

7/29에 손절가를 `effective_stop_price()` 하나로 통일했는데, 사이징 변화는 실측을 못 했었다.
7/30 08:55 주문이 첫 적용이었고, **예고한 시나리오가 정확히 나왔다.**

```
089860  pullback_v3   매수 113주 @37,400(지정가) → 36,600 체결
총자산 85,129,874   1회 허용위험 425,649원(0.5%)   ATR14 1,874.4

수정 전(고정%만)  손절 35,530 (-5.00%)   손절폭 1,870  → qty 227  손실 851,023원 = 1.00%
수정 후(정본)     손절 33,651 (-10.02%)  손절폭 3,749  → qty 113  손실 423,623원 = 0.50%
```

ATR 손절이 고정 5%보다 **넓어서** 정본이 ATR을 골랐고, 손절폭이 2배가 되며 수량이 절반이 됐다.
**227 → 113, 2.01배** — 7/27 `donchian_v2`(54 → 25)와 같은 패턴이다.
수정 전 코드였다면 오늘도 계좌 위험 1.00%짜리 포지션이 나갔을 것이다.

검증 방법: 운영 서버에서 실제 OHLCV(400봉)와 `effective_stop_price`/`risk_based_qty` 를
그대로 호출해 재현했다. **재현값 113 = 실제 주문 수량**으로 일치.

> 📌 ATR14 재현 시 **lookback 400봉**을 맞춰야 한다. 60봉으로 계산하면 Wilder 평활 워밍업
> 차이로 1,886.8이 나와 수량이 112로 어긋난다(`scheduler._latest_strategy_signal` 이 400).

**부수효과(예고대로)**: 진입 금액이 4.22M으로 고정비중 상한 8.5M의 절반이다. 자본 투입
속도가 느려지고 `mock_months` 누적 거래 빈도에도 영향이 있다.

---

# 주제 ② 오발주 사고 — 한 달 된 픽 무장 → 17초 만에 진입

## 경위

사용자가 "넥센타이어가 매수가에 왔는데 왜 안 사냐"고 물었다. 조사 결과 **버그가 아니라
픽이 무장(ARMED)되지 않아서**였다 — 엔진은 `ARMED`/`BOUGHT` 만 조회한다. `WATCH` 픽은
아무도 안 본다. 화면 라벨 "관찰"이 정반대 인상을 준다.

그다음 매수가 재산정을 요청받아 4개 안을 냈고, 그중 **D안(MA20 회복 후 진입, 6,800)** 을
"지금 무장해도 즉시 체결되지 않고 대기한다"고 설명하며 권했다. **이 설명이 틀렸다.**

엔진의 진입 조건은 `현재가 <= 매수가` 다 — **"이 값까지 떨어지면 산다"는 지정가 하한**이지
돌파 확인용 상한이 아니다. 이 엔진에는 stop-buy 의미론이 없어서 **"MA20 회복 후 진입"은
애초에 구현 불가능**하다. 현재가 6,150 ≤ 6,800 이라 조건이 즉시 참이 됐다.

```
12:49:41  ARM (가격을 6,800/8,000/6,300 으로 PATCH 후)
12:49:56  전략매매 추적 [002350] ARMED: 현재가=6150 매수가=6800 (-9.56%, 진입조건 충족)
12:49:58  전략매매 진입 제출 [002350] qty=1253 @6800     ← 시장가나 다름없는 지정가
12:50:13  DISARM (잔량 취소 성공) — 그러나 이미 21주 체결
```

## 원복

| 단계 | 내용 |
|---|---|
| 체결 | order `0000031820` (id=49) buy 1253주 중 **21주 @6,150** 부분 체결 |
| 잔량 | disarm 시 취소 성공 (1,232주) |
| 매도 | **사용자가 MTS에서 직접** 21주 @6,120 매도 |
| 보정 | `order_log` id=50 `MANUAL-002350-20260730` sell 21@6120 `exit_reason=manual_unwind` |

**실현 손익 -630원**(매매손익) / **-687원**(수수료·세금 포함). 모의투자 계좌, 계좌 대비 0.15%.

MTS 매도는 시스템을 안 거쳐 `order_log` 에 매도가 없었고, 매매일지가
`"매도 기록 없이 포지션 소멸(비정상)"` 분기로 손익을 null 처리했다. 실제로 일어난 거래이므로
감사 로그에 반영했다. **보정 기록은 `order_id` 접두사 `MANUAL-` 로 브로커 채번 주문(숫자)과
구분**하고, 삽입 전 중복 여부와 **실제 보유 0** 을 확인하는 안전장치를 뒀다(보유가 남아 있으면
매도 기록이 거짓이 된다).

보정 후 매매일지: `002350 status=closed entry=6150 exit=6120 pnl=-630`.

## 배운 것

- **엔진의 진입 의미론을 코드로 확인하지 않고 설명했다.** `scheduler.py:2199` 한 줄만 읽었으면
  막을 수 있었다. 가격을 제안할 때는 그 가격이 **어느 방향 조건에 걸리는지** 먼저 확인할 것.
- 실주문 경로는 권한 분류기가 막는다(매도 스크립트 실행·업로드 모두 차단됐다). 우회하지 말고
  사용자에게 `!` 명령을 넘길 것. **이 차단은 유지하는 게 맞다** — 오늘 사고가 그 이유다.

---

# 주제 ③ 픽 만료(신선도) 처리 — 커밋 `5a9e07e`, 배포 14:37

②의 재발 방지. 계획 모드로 원인을 다시 파악하고 범위를 정해 구현했다.

## 원인 (6가지가 겹쳤다)

| # | 원인 | 위치 |
|---|---|---|
| 1 | 목록 조회에 신선도 필터 없음 (`state != CLOSED` 뿐) | `api/analysis_picks.py` |
| 2 | 생성이 순수 INSERT — 같은 종목 옛 픽을 대체 안 함 | `scripts/load_analysis_picks.py` |
| 3 | 만료 잡이 없음 (스케줄러 잡 7개 중 픽을 건드리는 게 없다) | `ops/scheduler.py` |
| 4 | `CANCELLED` 상태가 죽은 코드 — 정의·표시만 있고 **할당하는 곳이 없음** | `common/models.py` |
| 5 | **오래된 ARMED 픽이 실거래 가능** — 날짜 필터 없이 매 틱 평가 | `ops/scheduler.py` |
| 6 | 오래된 픽이 다이제스트 가격을 조용히 채움 (상한만 있고 하한 없음) | `ops/daily_digest.py` |

**대조군**: 자동 파이프라인엔 이미 있다 — `_order_candidates` 는 스냅샷이 `previous_trading_day`
보다 오래되면 `[]` 를 반환하고, `CandidateSnapshot` 은 생성 시 같은 키 옛 행을 지우고 넣는다.
**픽에만 둘 다 없었다.**

## 구현 범위 (사용자 선택)

**안전장치 + 화면 표시**. 자동 만료 잡과 생성 시 대체(원인 2·3·4)는 **이번에 안 했다.**
만료 기준 **5거래일**(`MAPS_ANALYSIS_PICK_MAX_AGE_TRADING_DAYS`, 기본값).

### 핵심 설계 — 파생 계산이지 상태 전이가 아니다

원인 3이 "만료시키는 주체가 없다"인데 가드를 잡에 의존시키면 같은 실패를 반복한다.
잡이 멈추거나 배포·DB 복원 직후에도 가드가 살아 있어야 한다. 그래서 `CANCELLED` 전이 대신
**요청 시점 계산**으로 판정한다.

```
maps/ops/pick_freshness.py:35  pick_cutoff_date(settings, *, today=None)
                          :54  is_pick_stale(pick, cutoff)      # ref_date >= cutoff 면 신선(경계 포함)
                          :66  pick_stale_reason(pick, cutoff)  # "expired" | None
                          :71  pick_age_trading_days(...)       # 화면용 "만료 N거래일"
maps/market/trading_rules.py:58  trading_days_ago(ref, n, ...)  # 거래일 기준, 60일 상한
```

> ⚠️ **신선도는 `ref_date` 로만 계산한다.** `created_at`/`updated_at`/`last_action_at` 은
> UTC naive 인데 `ref_date` 는 KST `Date` 다. `created_at` 으로 재면 매일 09:00 KST 이전에
> 하루씩 어긋난다.

### 차단 지점

| 위치 | 동작 |
|---|---|
| `scheduler.py:2051` `_active_strategy_trade_picks` | 만료된 **ARMED** 제외 + WARNING |
| `scheduler.py:2199~` 진입 분기 | 제출 직전 2차 가드 (돈이 나가는 줄) |
| `api/analysis_picks.py:306` `arm_pick` | 409 거부, **상태 검사보다 앞에** |

`arm_pick` 한 곳이 **웹·모바일·텔레그램을 동시에** 막는다. 특히 텔레그램은 한 달 전 푸시
메시지의 인라인 버튼이 영구히 살아 있어 서버 거부 외에 방법이 없다. 모바일은 `postAction` 이
`body.detail` 을 그대로 던지므로 **기존 APK도 앱 업데이트 없이 한글 사유를 표시**한다.

> 🔴 **`BOUGHT` 픽에는 만료를 적용하지 않는다.** 실제 보유 주식이고 익절·손절을
> `_process_strategy_trades` 가 단독 관리한다 — 제외하면 청산 관리 없이 방치되어
> 원래 문제보다 나빠진다. 회귀 테스트 2개로 고정했다
> (`test_stale_bought_pick_still_exits_on_stop` / `..._takes_profit`).

### 화면

기준일 컬럼 추가(`ref_date` 는 **이미 API가 내려주는데 렌더만 안 하고 있었다**),
`badge-alert` 만료 배지, 무장 버튼 비활성, 만료 건수 헤더. 모바일도 동일(필드는 전부 optional).

### 다이제스트

`_latest_picks`(`daily_digest.py:290`)에 `ref_date` 하한 추가.
**cutoff 는 다이제스트의 `ref_date` 기준**이다 — `date.today()` 로 잡으면 지난달 다이제스트를
재생성(블로그 백필)할 때 픽이 전부 빠지고 `price_source` 가 조용히 `analysis_pick` → `rule` 로
뒤집힌다. 이 함정을 `test_backdated_digest_uses_its_own_ref_date_window` 로 고정했다.

## 배포 후 실측

```
expected_ref_date: 2026-07-23   stale_count: 1
  002350 넥센타이어  ref=2026-06-30  state=WATCH  stale=True  reason=expired  age=21

POST /api/v1/analysis-picks/2/arm → 409
{"detail":"기준일 2026-06-30 픽은 21거래일 지나 만료됐습니다.
           (2026-07-23 이후 기준일만 무장 가능) 재분석 후 가격을 갱신하세요."}
```

> 📌 `age=21` 이다. 로컬에서는 22가 나오는데 **로컬에 `holidays` 패키지가 없어서**다
> (운영은 0.97 설치됨 → 공휴일을 실제로 건너뛴다). **운영 값이 정본**이다.
> 로컬 테스트 시 `holidays package unavailable` 경고가 대량으로 뜨는 것도 같은 이유.

---

# 주제 ④ `disarm` 고아 포지션 버그 — 커밋 `5a9e07e`

②에서 21주가 고아가 된 직접 원인이다.

`disarm` 이 잔량 취소 **성공만 확인**하고 `entry_order_id` 를 지웠다. 취소는 잔량에만 걸리므로
**이미 체결된 주식은 그대로 남는다.** 그 결과 21주가 브래킷도 %/ATR 손절도 관리하지 않는
포지션이 됐다(`strategy_trade` 는 `live_rules._STOP_LOSS_PCTS` 에 없어 스케줄러 손절도 안 탄다).

코드 주석은 "고아 포지션"을 우려하고 있었지만 방어는 **취소 실패** 경우만 했다.

**수정**(`api/analysis_picks.py:370~`): 취소 성공 여부보다 **체결 물량을 먼저 본다**.
`fill_qty > 0` 이거나 status 가 `filled`/`partially_filled` 면 해제하지 않고 `BOUGHT` 로 올려
브래킷이 계속 관리하게 하고 409로 거부한다. `fill_qty` 는 동기화가 늦을 수 있어 상태로도 판정한다
(과소평가보다 과대평가가 안전).

회귀 테스트 4개. 그중 `test_disarm_rejected_when_partially_filled_and_cancel_succeeds` 가
실제 사고 재현이다 — mock 브로커는 미등록 주문에 `False` 를 주므로 취소 **성공** 경로는
monkeypatch 로만 재현된다.

---

## What Worked

- **엔진 코드를 직접 읽어 "왜 안 사는가"를 판정한 것.** 버그를 찾으러 가지 않고 상태 머신과
  조회 필터를 따라가니 "무장 안 됨"이라는 설계상 정상 동작이 나왔다.
- **운영 코드로 재현해 사이징을 실측한 것.** `effective_stop_price`/`risk_based_qty` 를 그대로
  호출해 113을 재현했다. 숫자를 손으로 계산했으면 lookback 차이(400 vs 60)를 못 잡았다.
- **가드를 파생 계산으로 만든 것.** 만료 잡 없이도 오늘 배포 즉시 넥센 픽이 막혔다.
- **계획 모드에서 Explore/Plan 에이전트로 원인을 다시 판 것.** 급하게 봤을 때 놓친
  "오래된 ARMED 픽은 무기한 실거래 가능"(원인 5)과 다이제스트 백필(원인 6)이 그때 나왔다.
- **테스트가 수정 전 코드에서 실패하는지 확인한 것.** `git stash` 로 소스만 되돌려
  3건 실패를 확인했다 — 없었으면 무의미한 테스트를 넣었을 수 있다.

## What Didn't Work / 주의

- 🔴 **엔진 의미론을 확인 없이 설명해 실주문을 유발했다.** `현재가 <= 매수가` 는 지정가 하한이다.
  가격을 제안하기 전에 **그 값이 어느 방향 조건에 걸리는지** 코드로 확인할 것.
  이 엔진에 **stop-buy(돌파 매수)는 없다.**
- **하드코딩 날짜가 테스트를 조용히 썩힌다.** `test_strategy_trade._TODAY = date(2026,6,25)` 와
  `test_telegram_notifications._seed_pick(ref_date=date(2026,6,29))` 가 만료 가드 도입으로
  깨졌다. 둘 다 `date.today()` 상대값으로 바꿨다. **픽/스냅샷 시드는 항상 today 상대로.**
- **`trading_days_ago(base, n)` 을 루프 안에서 부르면 O(n²)** 다. `is_krx_closed_date` 가
  호출마다 `holidays.KR(...)` 을 만든다. 나이 계산은 **뒤로 한 번만** 걸어야 한다(1,830회 → 60회).
- **로컬에 `holidays` 가 없다.** 거래일 계산 결과가 운영과 1일 어긋나고 경고가 대량으로 찍힌다.
  로컬 숫자를 운영에 그대로 인용하지 말 것.
- **작업 트리에 다른 세션이 동시에 쓰고 있었다.** `git add -u` 전에 반드시 `git status` 로
  내 것이 아닌 변경을 확인할 것. 오늘은 `schemas.py` 가 얽혀 분리 커밋이 불가능했다.
- **실주문·프로덕션 쓰기는 권한 분류기가 막는다.** 우회하지 말고 `!` 명령으로 사용자에게 넘길 것.
- 이월 주의(계속 유효): `date.today()` + UTC 저장 컬럼 함정, `order_log` 컬럼명
  (`order_price`/`fill_price`, `qty`), `journalctl | grep -v broker_sync`,
  `analyze` 픽과 스케줄러 주문은 **다른 파이프라인**.

---

## Next Steps

### 이번 변경 관측

1. **워치리스트 화면 눈으로 확인** — 기준일 컬럼·만료 배지·무장 버튼 비활성.
   HTTP·API 페이로드까지만 확인했고 **브라우저로 본 적은 없다.**
2. **모바일 재빌드·재설치** — 만료 배지·버튼 비활성을 보려면 필요하다. 기존 APK도
   409 사유는 정상 표시되므로 안전 측면에서는 급하지 않다.
   ```
   JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot" ./gradlew assembleDebug
   ```
3. **가드 로그** — 현재 ARMED 픽이 0건이라 `전략매매 픽 만료 — 진입 제외` 는 아직 안 뜬다.
   ```
   sudo journalctl -u maps --no-pager | grep -v broker_sync | grep "픽 만료"
   ```

### 새로 발견 — 조사 필요

4. 🟡 **매매일지 `estimated_exit` 13건.** 대부분 `donchian_v2` 이고 "매도 체결가 미기록 →
   매도일 종가로 추정" 상태다. **`009150` 은 아예 "매도 기록 없음"으로 손익 null**이다.
   오늘 002350과 같은 계열의 정합성 구멍이 과거에도 쌓여 있다는 뜻이다.
   `GET /api/v1/trade-review` 로 재현되고, 손익 합계의 신뢰도에 직접 영향한다.

### 픽 만료 — 이번에 일부러 안 한 것

5. **자동 만료 잡** (`WATCH` → `CANCELLED`). 파생 가드가 이미 주문을 막으므로 급하지 않다.
   붙인다면 `run_eod_cleanup`(브로커 미체결 취소를 먼저 하므로 순서가 맞다)에.
   `CANCELLED` 는 지금도 **아무도 할당하지 않는 죽은 상태**다.
6. **생성 시 같은 종목 옛 픽 대체.** `CandidateSnapshot` 선례를 따르되 soft-cancel 로
   (`entry_order_id` → `order_log` 감사 추적이 끊기므로 DELETE 금지).
7. **라벨 `관찰` → `대기(미무장)`** 개명 검토. `WATCH` 는 "감시 중"이 아니라 "무장 안 됨"인데
   현재 라벨이 정반대 인상을 준다 — 이번 사고의 인지적 원인 중 하나다.

### 이월

8. **~2026-10월말 `mock_months ≥ 3`**. 단 **점수 34.7 < 임계값 75**라 승격은 여전히 안 된다.
9. **업종 필터 활성화** — 점수 가중치 7개 중 `_score_from_db` 가 채우는 건 3개(0.50)뿐이고
   `earnings_revision` 0.25 가 통째로 자리표시자다. 활성화 전 레거시 선택기의 임계값 부재부터.
10. **애드센스** — `maps.magable.kr` 사이트 등록 필요(사용자 계정 작업). 앱에 광고 코드가 없고
    대시보드는 로그인 벽 뒤라 실효는 블로그 쪽이 클 것이다.
11. 이월: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리,
    네트워크 테스트 mock 화, 서명 릴리스 APK. `order_log_backup_20260724`(42행) DROP 가능.

---

## 핵심 파일 맵

- **픽 신선도**: `maps/ops/pick_freshness.py` (정본 판정),
  `maps/market/trading_rules.py:58`(`trading_days_ago`),
  `maps/common/settings.py`(`maps_analysis_pick_max_age_trading_days`).
  소비처: `scheduler.py:2051`·`:2199`, `api/analysis_picks.py:168`(`_to_item`)·`:306`(`arm_pick`),
  `ops/daily_digest.py:290`(`_latest_picks`).
- **브래킷 엔진**: `maps/ops/scheduler.py` — `_active_strategy_trade_picks`(2051),
  `_process_strategy_trades`(진입 조건 `current <= buy_price` 는 2199),
  `_strategy_trade_qty`. 상태 전이는 `api/analysis_picks.py` 의 `arm`/`disarm` 뿐이다.
- **손절 정본**: `maps/strategy/live_rules.py` — `effective_stop_price()`(고정%와 ATR 중 **넓은** 쪽).
  소비처: `scheduler._submit_exit_orders`, `scheduler._order_qty`, `api/risk.py`.
  백테스트만 `backtest/portfolio_replay._resolve_stop` 별도.
  ATR14 재현 시 **lookback 400봉**(`_latest_strategy_signal`).
- **KRX 인증**: `maps/data/krx_auth.py` — 로그인 회로차단기. 설치 지점 4곳.
- **날짜 경계**: `maps/execution/order_manager.py` — `kst_day_bounds_utc()`.
- **매매일지**: `maps/api/trade_review.py` — 매수/매도 페어링. 매도 기록이 없으면
  `estimated_exit` 또는 손익 null. 위 Next Steps 4번 참고.
- **전략 설명**: `maps/strategy/catalog.py`(산문), `maps/api/strategies.py`,
  `templates/strategies.html`, 원고 `docs/strategy_guides/`.
- **블로그**: `maps/ops/daily_digest.py`, `maps/api/{daily_digest,blog}.py`,
  `scripts/{run_blog_cron.sh,verify_blog_numbers.py,check_naver_format.py}`,
  `docs/blog_style_naver.md`. 출력 `/opt/maps/blog/`, cron `/etc/cron.d/maps-blog`.
- **테스트**: `tests/test_pick_freshness.py`, `test_trading_rules.py`, `test_strategy_trade.py`,
  `test_analysis_picks_api.py`, `test_telegram_notifications.py`, `test_daily_digest.py`,
  `test_effective_stop_price.py`, `test_order_qty.py`.
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
