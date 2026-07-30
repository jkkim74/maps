# HANDOFF

> 작성일: 2026-07-30 (목, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제 ①: **정본 사이징 첫 실측** — 7/29 핸드오프의 유일한 미실측 항목. 확인 완료.
> 주제 ②: **오발주 사고** — 한 달 된 픽을 무장하자 17초 만에 매수가 나갔다. 원복 완료.
> 주제 ③: **픽 만료(신선도) 처리** — ②의 재발 방지. 구현·배포·검증 완료.
> 주제 ④: **`disarm` 고아 포지션 버그** — 부분 체결을 무시하고 추적을 끊고 있었다.
> 주제 ⑤: **네이버 블로그 원고 포맷** — 마크다운·이모지를 걷어냈다. 배포 완료, 첫 배치 미관측.
> 주제 ⑥: **보유종목 화면 개선** — "앱에 보유가 1개만 나온다" 신고. **데이터는 정상, 화면 문제였다.**
>          서버는 배포·실측 완료. **모바일은 APK 재빌드 전이라 폰에 반영 안 됨.**
> 이전 핸드오프(KRX 회로차단기·손절 통일·전략관리 화면·도메인 이전, 7/29): git `cc3d2b3` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요(계속 유효)**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-29 23:55:32`처럼 전날로 찍힌다. psql로
"오늘 주문"을 `WHERE created_at >= '오늘'`로 조회하면 **0 rows가 나온다**.
`ORDER BY id DESC LIMIT n`이 안전하고, 코드에서는 `order_manager.kst_day_bounds_utc()`를 쓸 것.

오늘 커밋 2개: **`5a9e07e`**(14:2x) → **`06902f5`**(문서). 서버는 **`06902f5`** 까지 배포 완료
— 14:43:46 KST 기동, `active (running)`, 기동 로그 에러 0건, `https://maps.magable.kr` 303(정상).
테스트 **568 passed** + 모바일 **30 passed**(주제 ⑥으로 모바일 18 → 30). 마이그레이션·requirements 변경 없음.

> ⚠️ `5a9e07e` 는 **두 세션의 작업이 합쳐진 커밋**이다. 픽 만료·블로그 작업(주제 ②③④⑤)과,
> 다른 세션의 보유종목 화면 작업(**주제 ⑥** — 리스크 KPI·모바일)이 `maps/api/schemas.py`
> 에서 얽혀 파일 단위 분리가 불가능했다. 커밋 본문에서 갈래를 나눠 적었다.
> 그 전 커밋 `f91c719`(7/30 00:13)도 다른 세션 작업이다(종목별 score_reason, 다이제스트 집계).
> **커밋 메시지 "holdings KPI" 다섯 글자가 주제 ⑥ 전체(55파일 중 14개)를 가리킨다** —
> 나중에 이 변경을 추적할 때 `git log --oneline` 만으로는 못 찾는다. 주제 ⑥ 절을 볼 것.

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

# 주제 ⑤ 네이버 블로그 원고 포맷 — 커밋 `5a9e07e`, 배포 완료

## 전제가 바뀌었다

블로그는 **이미 운영 중인 네이버 블로그**에 사용자가 **직접 복사해서** 올린다.
새 블로그를 만들지도, 자동 발행하지도 않는다. MAPS 가 할 일은 붙여넣기만 하면 되는
원고를 만드는 것뿐이다.

그런데 `/blog` 가 만들던 건 **Markdown** 이었다. 스마트에디터는 마크다운을 렌더링하지
않으므로 `##`, `**`, 백틱, `|표|` 가 **기호 그대로 독자에게 보인다.** 복사해 붙이면 깨졌다.

같은 문제를 `docs/strategy_guides/*.txt` 가 이미 풀어 놨다(구분선·들여쓰기만 사용).
새로 발명하지 않고 그 규약을 일일 기록으로 확장했다.

## 규약과 검사기

`docs/blog_style_naver.md` 가 규약, `scripts/check_naver_format.py` 가 **유일한 구현**이다.
셸과 파이썬에 패턴을 따로 두면 한쪽만 고쳐진다. 검사는 두 갈래다.

| 갈래 | 잡는 것 |
|---|---|
| `paste` | `#` `**` 백틱 `\|표\|` `---` `>` `[](...)` — 지키지 않으면 **글이 깨진다** |
| `style` | 이모지, em dash(`—`), 상투구 8종 — 지키지 않으면 **AI 생성물로 읽힌다** |

구분선 `─`(U+2500)·화살표 `▶ ▸`·가운뎃점 `·`·원문자 `①` 는 기하 문자라 통과한다.
`✅ ⚠️ ★ ✓` 는 이모지로 잡힌다. 오탐 없음을 테스트로 고정했다.

**표는 만들지 않는다.** 네이버 기본 폰트가 가변폭이라 공백 정렬이 깨진다.
한 줄 이어쓰기(`▸ 082640 · donchian_v1 · 636주 · 8,180원`)나 `라벨 : 값` 세로 나열로 편다.
세로 나열은 라벨을 한글로 통일하고 글자 수를 맞출 때만 정렬이 유지된다.

> 📌 수치 기준은 감이 아니라 **가이드 8편에서 실측**했다 — 본문 줄 중앙값 29자,
> 상위 10% 38자, 최대 62자 / 빈 줄 2연속 101회, 3연속 0회. 규약을 그 실측에 맞췄다.
> 처음엔 "빈 줄 1개까지, 30자 내외"로 썼다가 본보기와 어긋나서 고쳤다.

## 바뀐 것

| 파일 | 내용 |
|---|---|
| `.claude/commands/blog.md` | 출력을 평문 규약으로 교체, 문체 규칙 7개, **모의투자 면책 문단 필수** |
| `scripts/run_blog_cron.sh` | 산출 `.md` → **`.txt`**, 4단계 포맷·문체 검사 추가 |
| `maps/api/blog.py` | `.txt` 우선, 구 `.md` 도 계속 읽음(같은 날짜면 `.txt` 정본) |
| `templates/blog.html` | 마크다운 렌더러 **제거** → 원문 그대로 + **[전체 복사]** |
| `static/js/app.js` | `copyGuideText(btn, sourceId)` 로 일반화 — 전략가이드와 구현 공유 |
| `docs/strategy_guides/*.txt` | 이모지 153건·em dash 34건 제거 (9편 전부) |

화면에서 가공하면 **복사한 내용과 발행될 내용이 달라진다.** 그래서 렌더러를 없앴다.

가이드 정리는 diff **156줄 수정 / 156줄 삭제로 정확히 1:1** — 산문은 한 글자도 안 건드렸다.
`👍 장점` → `장점`, `✅/❌` → `O/X`(대비 유지), `⭐ 필터 1` → `(필터 1)`,
`· 강세(strong) — 설명` → `· 강세(strong) : 설명`(` — `와 ` : ` 둘 다 3글자라 정렬 유지).

이제 가이드가 규약을 전부 지키므로 `tests/test_naver_blog_format.py` 가 **`paste`+`style`
양쪽**을 검사한다. 본보기가 규약을 어기면 규약이 무의미해진다.

## 검증 (7/29 다이제스트로 실제 1편 생성)

```
[포맷] 2026-07-29.txt: 붙여넣기 안전, AI 표기 없음
[검증] 다이제스트에서 못 찾은 숫자 1개 — 106   ← 23,450 − 23,344.03, 허용된 파생값
구분선 33자 균일 / 1줄짜리 문단 22개 / 4,400자
```

서버에서도 `check_naver_format.py` 실행과 `run_blog_cron.sh:104` 의 `.txt` 산출을 확인했다.
`/etc/cron.d/maps-blog` 주석의 `.md` 도 `.txt` 로 고쳤다(백업 `.bak.txtfmt.20260730_144519`).

> ⚠️ **네이버 에디터에 실제로 붙여넣어 본 적은 없다.** 구분선 줄바꿈·들여쓰기·문단 간격은
> 붙여넣어야 안다. 이번 변경에서 **유일한 미검증 지점**이다.

> 📌 제목의 날짜에는 **연도를 붙여야 한다.** `verify_blog_numbers.py` 가 날짜를 지우고
> 숫자를 대조하는데 "7월 29일"처럼 연도가 없으면 `29` 가 출처 불명으로 보고된다.

## 발행량

배치는 **거래일당 정확히 1편**이다(루프 없음, `claude -p /blog` 1회). 주말·공휴일 0편,
같은 날 재실행은 덮어쓰기. 월 20편 안팎.
기획서 권장은 주 3~4편이므로 **만드는 것과 올리는 것은 다르다** — 전부 올리면
기획서가 경계한 "매일 같은 표 반복"이 된다. 사용자가 골라 올리는 구조다.

## 첨부 기획서(PDF) 검토 결과 — 원고에 반영 전에 고칠 것

사용자가 `블로그활성화 - 블로그 활성화 방안 분석.pdf`(25쪽) 검토를 요청했다.
브랜드 전략(종목추천 배제, 시스템 중심, 종목분석 10~20% 제한)은 타당하나
**기술 서술이 MAPS 실물과 어긋난다.** 그대로 쓰면 첫 글부터 거짓이 된다.

| 기획서 | 실제 |
|---|---|
| `Java·Spring` 대상 독자, "Spring Boot로 실행 엔진을 만든 과정", 강의·SEO 키워드 전부 | **Python/FastAPI/SQLAlchemy/APScheduler**. Java 는 Capacitor 껍데기뿐 |
| 전략연구에 "주봉 W자" | 그런 전략 없음. 실제 8개에 **돈치안 v1·v2·역발상**이 빠져 있다 |
| "실제 돈으로 검증", "실제 투자 계좌 운용" | **KIS 모의투자**. 승격 점수 34.7 < 75 라 `live` 전략 0개 |
| "AWS Bedrock으로 종목 분석" | 맞다(`maps/ai/*`, claude-sonnet-4-6). 기술 항목 중 유일하게 정확 |
| "8개 자산의 5주 이동평균" | **맞다**(아래 주의 참고) |

Java→Python 불일치는 오히려 소재다("25년차 Java 개발자가 투자 시스템은 왜 Python으로").
카테고리 "05. MAPS 실전운용"은 **"모의운용"으로 바꿔야 한다** — 기획서가 19장에서
스스로 경고한 신뢰 훼손을 기획 단계에서 저지르는 셈이다.

기획서가 모르는 자산: `docs/strategy_guides/` **8편이 이미 완성**돼 있어 "전략연구 5편"은
사실상 끝났고, 일일 기록은 **이미 자동 생성**된다. 12주 로드맵이 크게 단축된다.
그 밖에 워드프레스(`magable.kr`)와 중복 게시 시 네이버 유사문서 위험, 21장 3단계
(대시보드·신호 유료화)의 유사투자자문업 신고 문제는 전자책·강의와 분리해서 봐야 한다.

---

# 주제 ⑥ 보유종목 화면 개선 — 커밋 `5a9e07e`, 서버 배포 완료 / **APK 미반영**

사용자가 앱 캡처와 함께 "보유종목이 1개만 나온다, 실제 3개"라고 신고했다.

## 진단 — 데이터는 처음부터 정상이었다

운영에서 직접 확인한 결과 **서버는 3건을 제대로 내려주고 있었다.**

```
_broker_holdings → status=ok, count=3
/api/v1/mobile/summary → risk.position_count=3, holdings 3건
portfolio_snapshot 7/30 → {"002810":226, "082640":636, "089860":113}
로그 → 장중 현재가 갱신: 3/3종목
```

1개로 보인 건 사용자가 본 화면이 **주문 탭 "주문 및 체결"** 이었기 때문이다.
그 목록은 `pending + fills_today + expired`(`OrdersScreen.tsx:98`)여서 오늘 체결된
089860만 들어가고 7/27·7/28 매수분은 빠진다. **버그가 아니라 보유를 제대로 보여주는
화면이 없는 것**이 문제였다.

실제 보유 목록은 리스크 탭 "보유 현황" 한 곳뿐이었는데, 서버가 `name`·`pnl_pct`·
`stop_price` 를 다 내려주는데도 **티커와 노출 비중만** 찍고 있었다. 모바일 `Holding`
타입에 `name`·`stop_price` 가 **선언조차 없었다** — 7/29 전략관리 화면과 **똑같은 계열의
결함**이다(서버는 주는데 화면이 안 읽는다).

> 📌 **KIS 잔고 페이지네이션 미구현은 이번 원인이 아니다.** `kis_adapter.py:372-394` 가
> `CTX_AREA_FK100/NK100` 을 빈 문자열로 고정하고 `tr_cont` 헤더를 아예 안 보내서
> `inquire-balance` 1페이지(~50행)만 읽는다. 실재하는 결함이고 보유가 늘면 터지지만,
> 3종목은 1페이지에 들어간다. **별건으로 남겼다**(아래 Next Steps).

## 함께 고친 표시 결함 4건

| 증상 | 원인 |
|---|---|
| 하단 네비가 **2줄로 깨지고 본문 아래가 잘린다** (캡처에서 보인다) | `styles.css` 가 `repeat(4, 1fr)` 인데 버튼은 5개(홈/주문/리스크/워치/알림). `.app-shell` 하단 패딩 72px 은 1줄 기준 |
| 체결 카드가 `089860 · broker`, 수량 없는 `buy` | `FillItem` 에 `strategy_id`·`qty` 가 없어 `order.strategy_id \|\| 'broker'` 가 항상 'broker', `order.qty` 는 `undefined` |
| 브로커 조회 실패 시 **조용히 축소된 목록**만 보인다 | `broker_status`/`broker_error` 가 모바일 타입에 없어 앱이 무시. 웹은 경고를 띄우고 있었다 |
| 보유 KPI 가 실제 행 수보다 커질 수 있다 | `risk.py` 가 `position_count = len(active_kills)` 를 보유 수와 `max()` 로 합침. 웹 KPI 라벨 "Kill Switch 또는 보유 종목" 이 그 흔적 |

## 조치

**서버** — 전부 **기본값 있는 추가 필드**라 기존 소비자가 안 깨진다.

```
schemas.py  HoldingItem  +quantity +market_value
            FillItem     +strategy_id +qty
            RiskResponse +active_kill_count
risk.py     보유 루프에서 이미 계산된 position.quantity / market_value 를 그대로 채움
            position_count = 보유 수 단독 (Kill 수는 active_kill_count 로 분리)
orders.py   체결 항목에 strategy_id / qty 전달
app.js      KPI 라벨 정리 + 보유 표에 수량·평가금액 열
```

**모바일** — `components/HoldingCard.tsx` 신설(종목명·전략·수량·평가금액·진입/현재/손절·
노출). 리스크 탭을 보유 우선으로 재배치하고 브로커 실패 배너·합계 요약·`활성 Kill` KPI 추가.
홈에 `보유 요약` 섹션과 `보유 전체 보기 >` 진입점. 주문 탭 제목을 **`오늘 주문 및 체결`** 로
바꿔 보유 목록과의 혼동을 없앴다. `buy`→`매수 113주`, `filled`→`체결` 한글화.
하단 네비는 `grid-auto-flow: column` 으로 바꿔 **탭 개수를 하드코딩하지 않는다**
(`repeat(5, 1fr)` 로 고치면 6번째 탭에서 똑같이 깨진다).

### 합계 손익은 비중 가중이어야 한다

`format.holdingsTotals()` 가 **`Σ평가금액 / Σ(진입가 × 수량) − 1`** 로 계산하고
홈·리스크가 **같은 함수를 공유**한다. `pnl_pct` 단순 평균은 큰 포지션과 작은 포지션을
같게 취급해 **부호까지 뒤집힌다** — 회귀 테스트에서 큰 포지션 -10%, 작은 포지션 +30%
이면 실제 -6%인데 단순 평균은 +10%가 된다. `quantity` 를 스키마에 추가한 이유가 이것이다.

`market_value == null`(fallback 경로)이면 수량·평가금액·노출 줄을 **아예 숨긴다** —
0원/0%로 찍으면 실제 값으로 읽힌다.

## 배포 후 실측 (`06902f5`, 14:43 기동)

```
position_count 3  active_kill_count 0  len(holdings) 3
  002810 삼영무역  226  5,085,000  손절 21,359
  082640 동양생명  636  5,164,320  손절  7,343
  089860 롯데렌탈  113  4,226,200  손절 32,416
```

수량·평가금액·손절가가 전부 채워지고 `position_count` 가 Kill 수와 분리됐다.
**웹 대시보드는 지금 바로 보인다.**

> ⚠️ **모바일은 APK 재빌드 전이라 폰에서는 아직 옛 화면이다.** `PROD_DEFAULT` 와 마찬가지로
> 화면 코드는 APK 안에 박힌다 — 서버 재배포로는 반영되지 않는다.
> 신 APK가 구 서버를 만나는 경우는 `active_kill_count ?? 0` 으로 방어했다.

> ⚠️ **브라우저·폰으로 눈으로 본 적은 없다.** jsdom 테스트가 렌더 문자열까지 확인했지만
> **CSS 레이아웃은 검증 못 했다**(네비 1줄 복귀, 카드 2열 그리드). 로컬 dev 서버에는
> 보유 데이터가 없어 의미 있는 확인이 불가능했다.

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
- **규약 수치를 기존 원고에서 실측한 것.** 줄 길이·빈 줄 규칙을 감으로 썼다가 본보기
  8편과 어긋나는 걸 실측으로 잡았다. 규약이 본보기를 부정하면 아무도 안 지킨다.
- **검사기를 셸이 아니라 파이썬 모듈 하나로 둔 것.** 배치와 테스트가 같은 함수를 쓴다.
  처음엔 cron 에 `grep -nE` 로 넣었다가 테스트와 이중 관리가 되는 걸 보고 되돌렸다.
- **기획서를 코드와 대조한 것.** "Spring Boot로 만든 과정" 같은 글이 그대로 나갈 뻔했다.
- **"보유가 안 나온다"를 화면 탓으로 단정하지 않고 서버부터 실측한 것**(주제 ⑥).
  `_broker_holdings` 를 운영에서 직접 호출해 3건·`status=ok` 를 확인하고 나서야 원인이
  화면임을 알았다. 반대로 코드부터 고쳤다면 KIS 페이지네이션 같은 엉뚱한 곳을 팠을 것이다.
- **캡처를 근거로 쓴 것**(주제 ⑥). 하단 네비 2줄 깨짐과 수량 없는 `buy` 는 **캡처에 이미
  찍혀 있었다.** CSS 를 세어 보니 버튼 5개 / `repeat(4, 1fr)` 로 바로 확인됐다.
  신고 내용 외의 결함 3건이 같은 이미지에서 나왔다.

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
- 🔴 **낡은 docstring 때문에 사실을 틀리게 보고했다.** `market/regime.py` 첫 줄이
  "5개 자산군"이라 기획서의 "8개 자산"을 오류라고 말했는데, `_ASSETS` 는 실제로 **8개**였다
  (`maps/market/CLAUDE.md` 도 8개로 적고 있었다). 다이제스트 `total_assets: 8` 을 보고 잡았다.
  docstring 은 고쳤다. **모듈 설명은 정본이 아니다. 상수·코드를 볼 것.**
- **원고를 두 벌(`.md` 화면용 / `.txt` 붙여넣기용)로 만들 뻔했다.** 값이 두 곳에 있으면
  조용히 어긋난다(CLAUDE.md 의 반복 교훈). 평문 하나로 통일하고 화면 렌더러를 없앴다.
- **윈도우 콘솔(cp949)이 원고를 못 찍는다.** 구분선·이모지에서 `UnicodeEncodeError` 로
  검사기 자신이 죽었다. `sys.stdout.reconfigure(encoding="utf-8")` 로 막았다.
  `verify_blog_numbers.py` 도 같은 문제가 있었다(운영 리눅스에서만 돌아 안 드러났다).
- 🟡 **"서버는 주는데 화면이 안 읽는다"가 반복 패턴이다.** 7/29 전략관리(전략명), 7/30
  워치리스트(`ref_date`), 7/30 보유현황(`name`·`stop_price`) — **세 번 연속** 같은 결함이다.
  응답 스키마에 필드를 추가할 때 **소비 화면까지 같이 보지 않으면** 조용히 사장된다.
  모바일은 TS 타입에 필드를 선언하지 않으면 존재 자체가 안 보이므로 특히 잘 새어 나간다.
- **탭·열 개수를 CSS 에 하드코딩하면 늘릴 때 조용히 깨진다.** `repeat(4, 1fr)` 에 5번째
  버튼이 들어가 2줄이 되고 본문이 잘렸는데, 탭을 추가한 커밋은 CSS 를 안 건드렸다.
  `grid-auto-flow: column` 처럼 **개수에 무관한 규칙**을 쓸 것.
- **합계 손익을 `pnl_pct` 평균으로 내면 부호가 뒤집힌다.** 비중 가중(`Σmv / Σ(진입×수량) − 1`)
  이어야 한다. 화면이 두 곳(홈·리스크)이면 **계산식을 함수 하나로 공유**할 것.
- 이월 주의(계속 유효): `date.today()` + UTC 저장 컬럼 함정, `order_log` 컬럼명
  (`order_price`/`fill_price`, `qty`), `journalctl | grep -v broker_sync`,
  `analyze` 픽과 스케줄러 주문은 **다른 파이프라인**.

---

## Next Steps

### 이번 변경 관측

1. **워치리스트 화면 눈으로 확인** — 기준일 컬럼·만료 배지·무장 버튼 비활성.
   HTTP·API 페이로드까지만 확인했고 **브라우저로 본 적은 없다.**
2. 🔵 **모바일 재빌드·재설치 — 이제 이게 가장 급하다.** 주제 ⑥ 의 보유 화면이 **전부 APK
   안에 있다.** 재설치 전까지 사용자는 신고한 그 화면(보유 1건처럼 보이는 주문 탭, 2줄 네비)을
   계속 본다. 픽 만료 배지도 같이 들어온다(만료 차단 자체는 서버가 이미 막으므로 급하지 않다).
   ```
   cd apps/mobile && npm run cap:sync && cd android
   JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.11.10-hotspot" ./gradlew assembleDebug
   ```
   `JAVA_HOME` 을 명시하지 않으면 jdk-17 로 빌드가 깨진다 — PATH 의 `java` 는 21이라
   `java -version` 만 보면 속는다(7/29 실측).
   설치 후 볼 것: **하단 네비 1줄**, 리스크 탭 보유 3건(종목명·수량·평가금액·손절가),
   홈 `보유 요약` → `보유 전체 보기` 이동, 주문 탭 `매수 113주` / `체결` / `pullback_v3`.
3. **가드 로그** — 현재 ARMED 픽이 0건이라 `전략매매 픽 만료 — 진입 제외` 는 아직 안 뜬다.
   ```
   sudo journalctl -u maps --no-pager | grep -v broker_sync | grep "픽 만료"
   ```
4. 🔵 **오늘 18:30 블로그 배치가 새 포맷의 첫 실전이다.** `[포맷] 2026-07-30.txt:
   붙여넣기 안전, AI 표기 없음` 이 뜨면 성공. `.md` 가 나오면 배포가 안 된 것이다.
   ```
   grep -A5 "\[포맷\]" /opt/maps/logs/blog_cron_*.log | tail -10
   ls -la /opt/maps/blog/ | tail -3
   ```
5. 🔵 **네이버 에디터에 실제로 붙여넣어 볼 것.** 블로그 변경의 **유일한 미검증 지점**이다.
   대시보드 `/blog` → [전체 복사] → 스마트에디터. 구분선 줄바꿈, 들여쓰기,
   문단 간격을 본다. 어긋나면 규약(`docs/blog_style_naver.md` 2절)의 33자·50자·빈 줄
   기준을 실측대로 고친다.

### 새로 발견 — 조사 필요

6. 🟡 **매매일지 `estimated_exit` 13건.** 대부분 `donchian_v2` 이고 "매도 체결가 미기록 →
   매도일 종가로 추정" 상태다. **`009150` 은 아예 "매도 기록 없음"으로 손익 null**이다.
   오늘 002350과 같은 계열의 정합성 구멍이 과거에도 쌓여 있다는 뜻이다.
   `GET /api/v1/trade-review` 로 재현되고, 손익 합계의 신뢰도에 직접 영향한다.
7. 🟡 **KIS 잔고 페이지네이션 미구현** (주제 ⑥ 조사 중 발견, 이번 건의 원인은 아니다).
   `kis_adapter.py:372-394` 가 `CTX_AREA_FK100/NK100` 을 `""` 로 고정하고, `_request` 는
   `tr_cont` 헤더를 보내지도, 응답 헤더를 돌려주지도 않는다(`payload` 만 반환). 즉
   `inquire-balance` 는 **1페이지(~50행)만** 읽는다. 지금은 3종목이라 안 드러나지만
   보유가 늘면 `/api/v1/risk` 보유 목록·`position_count`·`PortfolioSnapshot.holdings`·
   `_submit_exit_orders`(청산 감시)가 **동시에** 잘린다.
   > 🔴 표시 문제로 끝나지 않는다. `order_manager.sync_broker_state` 는 잔고에 없는
   > 티커의 미체결 SELL 을 `FILLED` 로 바꾼다(`current_positions` 비교). 잘린 페이지가
   > **주문 상태를 오염시킨다.** `_fetch_daily_order_rows`(260-279)도 같은 결함이다.
   `scripts/diag_kis_balance.py` 가 `tr_cont` 를 출력용으로 이미 읽고 있어 착수점이 된다.

### 픽 만료 — 이번에 일부러 안 한 것

8. **자동 만료 잡** (`WATCH` → `CANCELLED`). 파생 가드가 이미 주문을 막으므로 급하지 않다.
   붙인다면 `run_eod_cleanup`(브로커 미체결 취소를 먼저 하므로 순서가 맞다)에.
   `CANCELLED` 는 지금도 **아무도 할당하지 않는 죽은 상태**다.
9. **생성 시 같은 종목 옛 픽 대체.** `CandidateSnapshot` 선례를 따르되 soft-cancel 로
   (`entry_order_id` → `order_log` 감사 추적이 끊기므로 DELETE 금지).
10. **라벨 `관찰` → `대기(미무장)`** 개명 검토. `WATCH` 는 "감시 중"이 아니라 "무장 안 됨"인데
    현재 라벨이 정반대 인상을 준다 — 이번 사고의 인지적 원인 중 하나다.

### 보유 화면 — 이번에 일부러 안 한 것 (주제 ⑥)

11. **보유 카드 드릴다운.** `HoldingDetail.tsx` 는 워치리스트 픽 전용이고 보유 행에는
    연결돼 있지 않다. 카드에 이미 필요한 값이 다 있어 급하지 않다.
12. **`risk/manager.py:373` 섹터·테마 노출이 항상 0.** `get_positions()` 가 `dict[str,int]`
    인데 객체 리스트로 순회하고 `hasattr(p, "ticker")` 로 걸러서 전부 탈락한다
    (dict 순회는 `str` 키를 준다). 화면이 아니라 **리스크 한도 로직**이라 별건이다.

### 이월

13. **~2026-10월말 `mock_months ≥ 3`**. 단 **점수 34.7 < 임계값 75**라 승격은 여전히 안 된다.
14. **업종 필터 활성화** — 점수 가중치 7개 중 `_score_from_db` 가 채우는 건 3개(0.50)뿐이고
    `earnings_revision` 0.25 가 통째로 자리표시자다. 활성화 전 레거시 선택기의 임계값 부재부터.
15. **애드센스** — `maps.magable.kr` 사이트 등록 필요(사용자 계정 작업). 앱에 광고 코드가 없고
    대시보드는 로그인 벽 뒤라 실효는 블로그 쪽이 클 것이다.
16. **블로그 기획서 수정본** (주제 ⑤ 말미). Java/Spring → Python/FastAPI, "주봉 W자" 삭제,
    "실전운용" → "모의운용", 이미 있는 가이드 8편 반영. **원고를 쓰기 전에 고쳐야 한다** —
    그대로 두면 첫 글부터 사실과 어긋난다. 아직 손대지 않았다.
17. 이월: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리,
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
  `estimated_exit` 또는 손익 null. 위 Next Steps 6번 참고.
- **전략 설명**: `maps/strategy/catalog.py`(산문), `maps/api/strategies.py`,
  `templates/strategies.html`, 원고 `docs/strategy_guides/`.
- **보유 현황(주제 ⑥)**: 서버 `maps/api/risk.py` — `_broker_holdings()` 가 실시간 잔고,
  실패 시 `_fallback_holdings()`(= `AnalysisPick.state == 'BOUGHT'` 만, `broker_status='fallback'`).
  **`Position`/`Holding` ORM 테이블은 없다** — 보유는 매 요청 브로커 조회이고, 스냅샷만
  `PortfolioSnapshot.holdings`(JSON, 전량 교체) 에 남는다.
  화면: `apps/mobile/src/components/HoldingCard.tsx`, `screens/RiskScreen.tsx`(보유 현황),
  `screens/HomeScreen.tsx`(`HoldingSummary` + 리스크 탭 이동), 웹은 `static/js/app.js:494~`.
  합계 손익은 `apps/mobile/src/format.ts:holdingsTotals()` **한 곳**만 쓴다(비중 가중).
  주문/체결 라벨은 같은 파일의 `sideLabel`/`orderStatusLabel`(대소문자 정규화 필수 —
  서버가 `filled`/`FILLED` 를 섞어 준다). pill CSS 클래스는 **영문 상태값 유지**.
  하단 네비는 `styles.css` `.bottom-nav { grid-auto-flow: column }` — **개수 하드코딩 금지**.
- **블로그**: 숫자 `maps/ops/daily_digest.py` → 글 `.claude/commands/blog.md` →
  검증 `scripts/{verify_blog_numbers.py,check_naver_format.py}`. 배치 `scripts/run_blog_cron.sh`
  (거래일당 **1편**, 18:30). 조회 `maps/api/blog.py` + `templates/blog.html`(평문 + 전체 복사).
  **규약 `docs/blog_style_naver.md` 가 문서, `check_naver_format.py` 가 유일한 구현**이다.
  출력 `/opt/maps/blog/YYYY-MM-DD.txt`(구 원고는 `.md`), cron `/etc/cron.d/maps-blog`.
  원고 본보기는 `docs/strategy_guides/*.txt` 9편 — 규약을 전부 지킨다.
- **테스트**: `tests/test_pick_freshness.py`, `test_trading_rules.py`, `test_strategy_trade.py`,
  `test_analysis_picks_api.py`, `test_telegram_notifications.py`, `test_daily_digest.py`,
  `test_effective_stop_price.py`, `test_order_qty.py`, `test_naver_blog_format.py`,
  `test_blog_api.py`, `test_risk_api.py`, `test_mobile_api.py`.
  모바일 30건: `apps/mobile/src/{App,Detail}.test.tsx`, `format.test.ts`(보유 합계·라벨),
  `api.test.ts`, `auth.test.ts`, `push.test.ts` — `npm test` (`apps/mobile` 에서).
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
