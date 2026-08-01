# HANDOFF

> 작성일: 2026-07-31 (금, KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제 ①: **운영 매매기록 검토 → 차단 버그 2건** — 승격이 영구 차단돼 있었다. 수정·배포 완료.
> 주제 ②: **블로그 시스템 소개 시리즈 11편** 신규 작성.
> 주제 ③: **구분선 규약 정정(33자 → 20자)** — 실붙여넣기로 33자가 접히는 것을 확인.
> 이전 핸드오프(정본 사이징·오발주 사고·픽 만료·블로그 포맷·보유 화면, 7/30): git `06de7ed` 참고.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.

**중요(계속 유효)**: 운영 DB `TimeZone=Etc/UTC`인데 서버 OS는 KST다. `order_log.created_at`은
**UTC naive 저장** — 08:55 KST 주문은 `2026-07-30 23:55:32`처럼 전날로 찍힌다. psql로
"오늘 주문"을 `WHERE created_at >= '오늘'`로 조회하면 **0 rows가 나온다**.
`ORDER BY id DESC LIMIT n`이 안전하고, 코드에서는 `order_manager.kst_day_bounds_utc()`를 쓸 것.

오늘 커밋 3개: **`2e0142e`**(블로그 11편) → **`0e795e5`**(구분선 20자) → **`21a840a`**(버그 2건).
서버는 **`21a840a`** 배포 완료 — 15:25:18 KST 기동, `active (running)`, 기동 로그 에러 0건,
`https://maps.magable.kr` 303(정상). 테스트 **573 passed**(568 → 신규 5건).
마이그레이션·requirements 변경 없음.

> ⚠️ **`.claude/commands/blog.md` 수정분은 아직 커밋 전이다.** 58행의 구분선 33자를
> 20자로 고쳤다. 커밋·배포하지 않으면 **오늘 18:30 일일 배치가 다시 33자 원고를 만든다.**

---

# 주제 ① 운영 매매기록 검토 — 차단 버그 2건 (커밋 `21a840a`, 배포 완료)

`order_log` 50건 전량과 `portfolio_snapshot` 일별 보유 이력을 대조했다.
둘 다 화면·전략이 아니라 **기록 계층**의 문제이고, 그 잘못된 기록이 승격 판정과
손익 집계의 입력으로 다시 쓰이고 있었다.

## 버그 1 — mock_months 가 영구히 0.0 이었다

`scheduler._mock_track_months` 가 `OrderLog.side == "BUY"` 로 비교하는데
저장값은 소문자 `"buy"` 다(`OrderSide.BUY.value`).

```
SELECT count(*) FROM order_log WHERE side='BUY';   -->  0
```

코드베이스에서 대문자 리터럴은 **이 한 줄뿐**이고 나머지 8곳은 전부
`OrderSide.BUY.value` 또는 `"buy"` 를 쓴다. 그래서 `promotion_history` 최신 8건이
전부 아래 사유로 실패하고 있었다.

```
mock_months=0.0, replay_days=0, replay_passed=False
```

**배포 후 실측** (운영 DB에 직접 호출):

```
donchian_v2           1.97 개월      pullback_v3   1.97 개월
donchian_v1           1.51 개월      strategy_trade 1.15 개월
multi_asset_trend_v1  0.10 개월      집계 전략 5개 (배포 전 0개)
```

> 📌 **3개월 충족 시점은 2026-09-01 경이다.** 7/30 핸드오프의 "10월 말"은 이 버그를
> 전제로 한 추정이라 함께 틀렸다. donchian_v2·pullback_v3 의 최초 체결이 6/01 이므로
> `6/01 + 91.3일 = 8/31`, 게이트가 3.0 을 넘는 건 9/01 이다.

> 🔴 **점수 게이트는 그대로다.** 최근 평가 점수가 28.6~48.0 이라 `mock_candidate →
> live_candidate` 임계값 60에 여전히 막힌다. 이 수정은 차단을 푸는 게 아니라
> **잘못 잠긴 조건 하나를 정상화**한 것이다. 17:10 검증 후 단계가 실제로 올라가면
> 분석과 다른 것이므로 즉시 확인할 것.

## 버그 2 — 매도 체결이 감사 로그에 남지 않았다

매도 17건 중 13건이 `expired` 인데, 스냅샷을 보면 **그 종목들은 만료된 바로 그날
계좌에서 사라졌다.** 실제로는 체결됐다는 뜻이다.

```
06-04  8종목 보유  →  06-05  5종목   (당일 매도 6건 전부 "expired")
06-08  1종목                          (당일 매도 5건 전부 "expired")
06-11  017670 소멸                    (매도 1건 "expired")
06-18  012330 소멸                    (매도 1건 "expired")
```

상관관계가 100%에 가깝다.

```
order_price NULL   →  expired 13건 / filled 1건
order_price 있음   →  expired  0건 / filled 3건
```

원인은 두 갈래이고 둘 다 **가격·수량이 비어 있는 것**이 핵심이다.

| # | 위치 | 내용 |
|---|---|---|
| 2a | `order_manager.py` 브로커 결과 루프 | 브로커가 `status=filled` + `filled_quantity=0` 을 주면 `if result.filled_quantity and ...` 때문에 **상태만 바뀌고 수량은 0으로 남는다** |
| 2b | `scheduler._submit_exit_orders` | 청산 주문의 현재가·최근 종가가 모두 0이면 `_log_order` 의 `limit_price or current_price or None` 이 **NULL** 이 되고, 포지션 폴백이 `fill_price = order_price` 로 채우므로 체결가까지 빈다 |

`fill_qty=0` 은 조용히 남는 게 아니라 **하위 집계에서 통째로 탈락**한다.
`trade_review` 와 `_mock_track_months` 가 전부 `fill_qty > 0` 을 요구한다.
즉 **버그 2가 버그 1의 분모까지 깎고 있었다.** 실제 잔재 2건:

```
id=43  004490 sell  filled  fill_qty=0   (07-13 브래킷 청산)
id=37  012330 buy   filled  fill_qty=0   (06-09)
```

## 수정

```
scheduler.py:1076   "BUY" → OrderSide.BUY.value
order_manager.py    _normalize_filled_row() 신설 — 브로커 결과 경로와 포지션 폴백
                    경로가 같은 함수를 쓴다 (규칙이 두 곳에 있으면 한쪽만 고쳐진다)
scheduler.py:1973   record_price — 현재가·종가가 모두 0이면 평균 단가로 폴백
```

> 🔴 **`record_price` 는 기록용이다. 청산 판정에는 쓰지 않는다.**
> 폴백 가격으로 손절을 발동시키면 시세를 못 읽었다는 이유로 가짜 손절이 나간다.
> `stop_triggered` 판정은 지금처럼 유효한 현재가가 있을 때만 참이 된다.
> 회귀 테스트 `test_fallback_price_does_not_trigger_a_stop` 로 고정했다.

> ⚠️ **부분체결(`partially_filled`)에는 수량 보정을 적용하지 않는다.**
> 주문 수량으로 덮으면 보유하지 않은 수량이 체결로 남는다.
> `test_partial_fill_quantity_is_not_overwritten` 로 고정했다(7/30 오발주 21주 재현).

## 테스트가 버그를 통과시키고 있었다

`test_mock_track_months_counts_from_first_filled_buy` 가 `side="BUY"` **대문자로
시드**하고 있었다. 그래서 운영에서 0건인 동안 테스트는 계속 통과했다.
실제 저장값으로 바꾸고 회귀 테스트를 추가했다. 신규 5건 전부
`git stash` 로 소스만 되돌려 **수정 전에 실패하는 것을 확인**했다.

## 과거 데이터는 손대지 않았다

사용자 결정. 6월의 13건은 `expired` 로 남는다. 앞으로 기록되는 주문만 바로잡는다.
따라서 **매매일지의 과거 손익은 지금도 추정값**이다(아래 Next Steps 참고).

---

# 주제 ② 블로그 시스템 소개 시리즈 11편 (커밋 `2e0142e`)

`docs/blog_series/*.txt` 11편. 독자는 AI 자동화 투자를 직접 만들려는 사람이고,
기존 `docs/strategy_guides/` 8편이 개별 전략을 다루는 것과 달리 **시스템 전체**
(구조·파이프라인·위험한도·AI 위치·로드맵)를 다룬다. 편당 1,264~1,673자.

수치와 규칙은 **전부 코드에서 읽었다.** 기획서가 아니라 코드가 기준이다
(7/30 에 기획서가 Java/Spring 으로 잘못 적고 있던 전례).

| 근거 | 글에 반영된 값 |
|---|---|
| `risk/manager.py`, `settings.py` | 1회 0.5%, 일일 1.5%, MDD 15%, 단일종목 10%, 연속실패 5회 |
| `market/regime.py` | 8자산 5주선 70%/40%, 진입한도 100/50/25%, 변동성 1단계 하향 |
| `ai/technical_scorer.py`·`contrarian_analyzer.py` | 가중치 상한 0.20, REJECT −20점/WATCH −5점, **기본값 꺼짐** |
| `scheduler._register_jobs` | 거래일 5잡 + 잔고 동기화 60초 |
| `.claude/commands/analyze.md` | 7단계 에이전트 → 워치리스트까지, 무장은 사람 |

작성 중 **코드 확인으로 사실 오류 3건을 잡았다** — 국면 강제값이 화면에 표시된다고
썼다가 참조처가 `order_preview` 한 곳뿐인 것을 확인해 "표시되지 않는다"로 정정,
잔고 동기화 30초 → 실제 기본값 60초, 웹 화면 23개 → 20여 개(부분 템플릿 2개 제외).

---

# 주제 ③ 구분선 규약 정정 33자 → 20자 (커밋 `0e795e5`)

사용자가 1편을 **네이버 스마트에디터에 실제로 붙여넣었다.** 구분선 33자가 접혔다.
본문 줄(최장 60자)·들여쓰기 2칸·원문자·가운뎃점은 전부 정상이었다.

> 📌 33자는 붙여넣어 보지 않고 **계산으로 정한 값**이었다. `─`(U+2500)는 전각이라
> 같은 글자 수의 한글 본문보다 실제 폭이 넓다. **본문 30자가 안 접힌다고
> 구분선 30자가 안 접히는 게 아니다.**

- `docs/blog_series/*.txt` 11편 (구분선 70개)
- `docs/strategy_guides/*.txt` 9편 (구분선 160개) — 본보기가 규약을 어기면 규약이
  무의미해지므로 같이 내렸다. **산문은 한 글자도 안 건드렸다**(HEAD 대비 프로그램 대조로
  구분선 외 변경 0건 확인)
- `docs/blog_style_naver.md` 2절 — 정정 사유와 실측 근거 기록
- `.claude/commands/blog.md` 58행 — **오늘 고쳤으나 미커밋**

`check_naver_format.py` 는 구분선 길이를 검사하지 않는다(이모지·em dash·상투구만).
길이는 사람이 실측으로만 잡을 수 있다.

---

## What Worked

- **매매기록을 먼저 읽고 코드를 나중에 본 것.** "매도 만료율이 높다"는 이월 항목을
  코드부터 뒤졌으면 KIS 어댑터를 팠을 것이다. `order_log` 와 `portfolio_snapshot` 을
  날짜로 대조하니 **만료된 날 포지션이 사라졌다**는 사실이 먼저 나왔고,
  그때부터 "체결은 됐는데 기록이 틀렸다"로 문제가 바뀌었다.
- **상관관계를 SQL 한 줄로 뽑은 것.** `GROUP BY side, (order_price IS NULL), status`
  하나로 "가격 없는 매도는 만료된다"가 즉시 보였다. 개별 행을 읽어서는 못 봤을 패턴이다.
- **테스트가 왜 통과하고 있었는지 확인한 것.** 버그를 찾은 뒤 "그럼 테스트는 왜 안
  잡았지"를 물었더니 시드가 대문자였다. 이걸 안 봤으면 수정 후 기존 테스트가 깨질 때
  테스트를 되돌렸을 수도 있다.
- **`git stash` 로 수정 전 실패를 확인한 것.** 신규 5건 중 4건이 실패하고 1건
  (부분체결 보존)은 통과했다 — 통과한 1건은 기존 동작을 고정하는 가드라 정상이다.
- **배포 후 운영 DB에 함수를 직접 호출한 것.** `_mock_track_months` 를 그대로 불러
  5개 전략·1.97개월을 확인했다. 화면이나 로그로는 이 값을 볼 수 없다.
- **글을 쓰면서 코드를 대조한 것.** 블로그 원고를 쓰다가 사실 오류 3건이 나왔다.
  문서화가 코드 점검을 겸했다.

## What Didn't Work / 주의

- 🔴 **테스트 시드가 실제 저장값과 다르면 버그를 영구히 숨긴다.** `side="BUY"` 로
  시드한 테스트가 소문자로 저장되는 운영을 통과시켰다. **열거형 값은 리터럴로 쓰지 말고
  `OrderSide.BUY.value` 를 쓸 것.** 시드도 마찬가지다.
- 🔴 **집계가 조용히 0을 반환하면 아무도 모른다.** `_mock_track_months` 는 2개월간
  빈 dict 를 돌려주고 있었는데 에러도 경고도 없었다. 게이트 사유 문자열에
  `mock_months=0.0` 이 찍히고 있었지만 "아직 3개월이 안 됐구나"로 읽혔다.
  **0이 정상값인지 결함인지 구분되는 로그가 필요하다.**
- **규약을 여러 파일에 적으면 한쪽만 고쳐진다.** 구분선 20자를 `blog_style_naver.md`
  와 가이드·시리즈 원고에는 반영했는데 `.claude/commands/blog.md` 를 빠뜨릴 뻔했다
  (핸드오프 작성 중에 발견). 규약 수치를 바꿀 때 `grep -rn "33자"` 를 돌릴 것.
- **계산으로 정한 값에 "실측"이라고 적었다.** 구분선 33자는 본문 줄 길이 실측에서
  유추한 값인데 규약 문서에는 상한으로 단정돼 있었다. **재보지 않은 값에는
  근거를 함께 적을 것.**
- **`─` 는 전각이다.** 한글 본문 기준으로 폭을 유추하면 어긋난다.
- 이월 주의(계속 유효): `date.today()` + UTC 저장 컬럼 함정, `order_log` 컬럼명
  (`order_price`/`fill_price`, `qty`), `journalctl | grep -v broker_sync`,
  `analyze` 픽과 스케줄러 주문은 **다른 파이프라인**, 로컬에 `holidays` 없음,
  작업 트리에 다른 세션이 동시에 쓸 수 있으니 `git add -u` 전에 `git status`.

---

## Next Steps

### 오늘 안에 확인

1. 🔵 **`.claude/commands/blog.md` 커밋·배포.** 미커밋 상태다. 안 하면 오늘 18:30
   배치가 33자 원고를 만든다. 서버 `/opt/maps` 는 git pull 로 반영된다.
2. 🔵 **17:10 검증 잡 결과.** 오늘이 mock_months 수정 후 첫 실행이다.
   ```
   sudo -u postgres psql -d maps -c "SELECT DISTINCT ON (strategy_id) strategy_id, \
     passed, round(tradeability_score::numeric,1), left(fail_reasons_json,90) \
     FROM promotion_history ORDER BY strategy_id, evaluated_at DESC;"
   ```
   `mock_months=0.0` 이 사라지고 1.5~2.0 으로 찍혀야 한다.
   **단계는 그대로 `mock_candidate` 여야 정상이다**(점수 게이트 60에 막힘).
   비교 기준: 배포 시점 `promotion_history` 399행, 6개 전략 전부 `mock_candidate`.
3. **오늘 18:30 배치가 20자 구분선 첫 실전.** 7/30 배치는 `.txt` 생성 + 포맷 검사
   통과를 확인했다(7/30 Next Steps 4번 해소).
   ```
   grep -A3 "\[포맷\]" /opt/maps/logs/blog_cron_*.log | tail -8
   ```

### 이월 (7/30 에서 그대로 남음)

4. ~~모바일 APK 재빌드·재설치~~ — ✅ **7/31 완료**(사용자 확인, 8/1 기록).
5. **워치리스트·보유 화면 브라우저 확인.** HTTP·API 페이로드까지만 봤고 CSS 레이아웃
   (네비 1줄 복귀, 카드 2열)은 검증 못 했다.
6. **픽 만료 가드 로그** — 현재 ARMED 픽 0건이라 아직 안 뜬다.
   `journalctl -u maps | grep -v broker_sync | grep "픽 만료"`

### 매매기록 검토에서 새로 발견 (범위 밖으로 남김)

7. 🔴 **KIS 잔고·주문 페이지네이션 미구현** (`kis_adapter.py:372-394`).
   `CTX_AREA_FK100/NK100` 을 `""` 로 고정하고 `tr_cont` 헤더를 보내지 않아
   **1페이지(~50행)만** 읽는다. 6/05 처럼 하루에 주문이 몰린 날 체결 누락의
   유력한 원인이다. 표시 문제로 끝나지 않는다 — `sync_broker_state` 가 잘린 잔고로
   미체결 SELL 을 `FILLED` 로 바꾼다. `scripts/diag_kis_balance.py` 가 착수점.
8. 🟡 **부분체결이 만료 처리된다.** `expire_pending_orders` 가 `PENDING` 과
   `PARTIALLY_FILLED` 를 일괄 `expired` 로 바꾼다. 7/30 오발주의 21주가 그렇게
   `expired` 로 남았다(fill_qty=21). 체결분이 있는 주문은 다른 상태가 필요하다.
9. 🟡 **매매일지 페어링이 티커 단위**(`trade_review.py:119`). 같은 종목을 두 전략이
   사면 매도 하나가 양쪽에 귀속되고, `exit_proceeds` 를 **매수 수량**으로 곱한다.
   과거 13건을 안 고치기로 했으므로 `estimated_exit` 은 그대로 남는다.
10. 🔵 **후보 퍼널 재설계 + AI 스코어링 — 계획 확정, 구현 착수 전.**
    계획서: **`docs/plans/candidate-funnel-ai-scoring.md`** (착수점·행번호 포함).
    요지: 후보 생성이 **전략 신호를 전혀 안 본다.** 유니버스 1,260 × 8전략 =
    하루 10,080행을 유동성·추세 점수만으로 저장하고, `entry_signal` 은 다음 날
    08:55 에야 계산한다. 그래서 AI 대상(상위 5)이 초대형주 1개 + 유동성 0 종목
    4개로 채워진다 — "제대로 된 스코어링"이 안 되는 원인은 모델이 아니라 대상 선정.
    함께 확인된 것: `_save_candidate_snapshot` 이 전략마다 호출되며 종목별 OHLCV 를
    **전 기간** 다시 읽어 하루 10,080회(AI 켜면 20,160회) 로드한다.
    승인 범위 = 신호 게이트 + OHLCV 1회 로드 + 저장 축소(신호 전수 ∪ 상위 N) +
    AI 프롬프트 구조 분리·캐싱·구조화 출력, 모델 **Claude Opus 5**. 성과 A/B 는 제외.
    아래 10-1(용량)은 이 작업으로 함께 해소된다.
10-1. 🟡 **`candidate_snapshot` 387,141행 / 143MB**, 보존 정책 없음. 하루 약 1만 행씩
    늘어난다(전 종목 × 전략). Lightsail 디스크 기준으로 1년이면 1GB 대.
    과거 행 정리는 별건이다 — 위 계획은 **앞으로 쌓이는 양**만 줄인다.
11. 🟡 **후보 생성 누락일 2건** — 2026-07-01, 07-17. 둘 다 평일인데 `candidate_snapshot`
    에 행이 없다. 잡 실패 로그가 남아 있는지 확인 필요.
12. 🟡 **분석 워치리스트 누적 2건뿐**(`analysis_pick` 전체). `analyze` 는 매 거래일
    cron 으로 돌지만 게이트에서 전량 탈락한다. 버그는 아니지만 파이프라인이
    사실상 산출물을 못 내고 있다.
13. **`analysis_pick` id=1 이 `state=CLOSED` 인데 `exit_reason` 이 비어 있다.**
    `exit_order_id=0000010407` 은 채워져 있다. 브래킷 청산 경로가 사유를 못 남긴
    경우가 있다는 뜻이다.
14. 📌 **모의계좌 실적**: 6/01 `99,773,500` → 7/30 `85,129,874` (약 **-14.7%**).
    대부분 6월 `donchian_v2` 구간에서 발생했다. 매도 기록이 비어 있어 매매일지로는
    이 손실이 설명되지 않는다. 버그 2 수정으로 **앞으로는 남는다.**

### 이월 (계속)

15. **~2026-09-01 `mock_months ≥ 3`** (버그 1 수정 반영). 단 **점수 28.6~48.0 <
    임계값 60** 이라 승격은 여전히 안 된다. 점수를 올리는 게 실제 병목이다.
16. **업종 필터 활성화** — 가중치 7개 중 `_score_from_db` 가 채우는 건 3개뿐이고
    `earnings_revision` 0.25 가 통째로 자리표시자다.
17. **애드센스** — `maps.magable.kr` 사이트 등록(사용자 계정 작업).
18. **블로그 기획서 수정본** — Java/Spring → Python/FastAPI, "주봉 W자" 삭제,
    "실전운용" → "모의운용". 아직 손대지 않았다. 다만 시리즈 11편이 이미 정확한
    사실로 쓰였으므로 기획서보다 **원고를 정본으로 삼는 편이 빠르다.**
19. 이월: KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트
    mock 화, 서명 릴리스 APK, `order_log_backup_20260724`(42행) DROP 가능.

---

## 핵심 파일 맵

- **승격 트랙레코드**: `maps/ops/scheduler.py:_mock_track_months`(1067~) —
  `side` 는 반드시 `OrderSide.BUY.value`. `maps/promotion/gate.py:195` 가 소비.
  임계값 `common/constants.py:min_mock_months=3`.
- **체결 동기화**: `maps/execution/order_manager.py` — `sync_broker_state`,
  `_normalize_filled_row`(체결수량·가격 보정 **단일 정본**),
  `expire_pending_orders`(부분체결도 만료시킨다 — 위 Next Steps 8번),
  `kst_day_bounds_utc`(날짜 경계).
- **청산 제출**: `maps/ops/scheduler.py:_submit_exit_orders`(1903~) —
  `record_price` 는 기록용, `current_price` 는 판정용. **둘을 섞지 말 것.**
  브래킷 청산은 `_process_strategy_trades`(2246~) 별도 경로.
- **손절 정본**: `maps/strategy/live_rules.py:effective_stop_price()`
  (고정%와 ATR 중 **넓은** 쪽). ATR14 재현 시 **lookback 400봉**.
  백테스트만 `backtest/portfolio_replay._resolve_stop` 별도.
- **픽 신선도**: `maps/ops/pick_freshness.py`(정본 판정), 소비처
  `scheduler.py:2051`·`:2199`, `api/analysis_picks.py:306`.
  `BOUGHT` 픽에는 만료를 적용하지 않는다.
- **매매일지**: `maps/api/trade_review.py` — 매수/매도 페어링(티커 단위),
  `fill_qty > 0` 요구. 매도 기록이 없으면 `estimated_exit` 또는 손익 null.
- **블로그**: 규약 `docs/blog_style_naver.md`(**구분선 20자**), 검사기
  `scripts/check_naver_format.py`(길이는 검사 안 함), 일일 배치 프롬프트
  `.claude/commands/blog.md`, 배치 `scripts/run_blog_cron.sh`(거래일 1편, 18:30),
  출력 `/opt/maps/blog/YYYY-MM-DD.txt`, 화면 `maps/api/blog.py` + `templates/blog.html`.
  원고: 시스템 소개 `docs/blog_series/` 11편, 전략 `docs/strategy_guides/` 9편.
- **테스트**: `tests/test_candidate_snapshot_scheduler.py`(mock_months),
  `test_sync_fill_reconciliation.py`(체결 보정·부분체결 보존),
  `test_exit_order_price_record.py`(기록가 폴백·가짜 손절 방지),
  `test_naver_blog_format.py`, `test_pick_freshness.py`, `test_effective_stop_price.py`.
  모바일 30건은 `apps/mobile` 에서 `npm test`.
- **운영 접속**: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
