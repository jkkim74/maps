# HANDOFF

> 작성일: 2026-08-05 (수, 오전) · 작성자: 세션 에이전트 (**집 PC, 키 `D:\maps\`**)
> 서버 = **`debe036`** 배포 완료 (10:42 KST), alembic **`0018`**(head, 변경 없음), 테스트 **647 passed**.
> 8/3 기록은 "8/3 작업 ①~⑤" 절, 8/2 는 "이전 날(8/2)" 절 — 결론은 여전히 유효하다.

## Goal — 이 작업이 향하는 곳

후보 생성이 **전략 신호를 보지 않는** 구조를 고쳐, "후보"를 유동성·추세 상위 종목이 아니라
**"이 전략이 오늘 사겠다고 말한 종목"** 으로 되돌리는 것. 그 위에 AI 스코어링을 붙인다
(Phase 2, 착수 전). 병행 목표는 승격 게이트가 **실제 성과로** 갈리게 만드는 것 —
Sharpe 왜곡 수정(8/2)과 자동 강등(8/2)이 8/3에 처음 실측됐다.

## 8/5 세션 — KIS 모의계좌 만료 사고 · 복구 · Kill Switch 해제 버튼

### 🔴 사고 — 장세 게이트가 처음 열린 날, KIS 모의계좌가 만료돼 있었다

8/5 08:55 주문 사이클에서 장세 차단이 처음 풀려 실제 주문이 나갔는데, **전 주문이
KIS `40910000 "모의투자 주문이 불가한 계좌입니다"` 로 실패** (모의계좌 유효기간 만료).

- `ath_breakout_v1` 매수 5연속 실패 → **Kill Switch 자동 발동** (연속 실패 감지는 설계대로 동작)
- `multi_asset_trend_v1` 002810 청산 매도도 같은 오류로 실패
- 최종 submitted 0 / skipped 72 (매수 71, 매도 1)

### 복구 (완료)

| 단계 | 내용 |
|---|---|
| 계좌 재생성 | 구 `50185813-01` → 신 **`50200591-01`** (사용자, KIS 홈페이지) |
| 키 재발급 | 새 appkey/appsecret → 서버 `.env` `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT_NO` 교체 후 재기동 |
| 실효 확인 | 09:48 broker_sync 성공 — cash 1억, 보유 0, sync_errors 0 |
| Kill Switch 해제 | `kill_switch_log` 에 deactivate 이벤트 직접 삽입 (당시 UI 버튼 부재 → 아래 신규 기능) |

> ⚠️ **계좌번호만 바꾸면 `OPSQ2000 INVALID_CHECK_ACNO` 가 난다.** appkey 가 계좌에
> 묶여 있어 **키도 같이** 재발급해야 한다. 같은 40910000 이 재발하면 만료를 먼저 의심.

**후과 — 성과 이력 단절:**
- 구 계좌의 002810 포지션(~1,020만)은 청산 없이 브로커 쪽에서 소멸. DB 전략 트레이드에
  열린 기록이 남아 있을 수 있다 — 다음 사이클 로그에서 관찰 (8/5 오전 broker_sync 는 조용함).
- **모의 성과 이력이 8/5 에서 끊긴다.** 6/1~7/30(-14.7%)과 새 계좌(1억 리셋)를 잇지 말 것.
  `mock_months` 재산정에도 영향 (이월 15번과 연결).

### 8/4 실측 확인 (8/3 Next Steps 1·2번 처리)

- **주문 사이클(1번) → 이월**: 8/4 도 weak — 64건 전량 `preferred_regime_mismatch:weak`.
  주문 가능 전략 2개(ath_breakout_v1, donchian_v2)는 강등 반영대로. 신호 집합 일치 검증은
  8/6 이후 정상 주문일로 이월.
- **G2P 수정 실효(2번) → ✅ 종결**: 8/4 17:10 검증에서 `Infinity`/`NaN` **0건**. 무거래 사유가
  예상 3전략에 부착 — contrarian **5/5**, multi_asset **3/5**, ath_v2 **2/5** (예상 3/5,
  fold 1개가 이번엔 거래 — 무해). pullback_v2 유일 WFA 통과 유지, **passed=0/failed=8 불변**
  (통과 수 변화 없음 = 기대 충족). 부수: ath_breakout_v1 점수 78.7 → **65.4** — 이제
  mock_months 외에 점수로도 차단된다.

### 신규 기능 — Kill Switch 해제 버튼 (`debe036`, 배포 완료)

해제 수단이 API 직접 호출뿐이었다 → 리스크 화면(SCR-06)에 발동 중 목록 + 해제 버튼.
`RiskResponse.active_kills` 신설(`api/risk.py`, `api/schemas.py`), 기존
`POST /api/v1/live-monitor/{id}/release` 를 화면에 연결(`static/js/app.js`,
`templates/risk.html`). 발동이 없으면 아무것도 안 보인다. `created_at` 은 UTC ISO 로
내려 브라우저에서 KST 변환 (9시간 함정 회피). 테스트 1건 추가, 전체 647 passed.
마이그레이션·requirements 변경 없음.

## 8/3 커밋·배포

| 해시 | 내용 |
|---|---|
| `d913cf5` | feat: KIS 연속조회 발동·보유 종목 수를 로그에 남긴다 (관측성 보강) |
| `0a186c0` | feat: 후보 퍼널을 신호 기반으로 재설계 (Phase 1) — `0018` |
| `bd222d6` | Merge feat/candidate-funnel-signal-gate |
| `e4aaa1b` | docs: 8/3 세션 핸드오프 |
| `3728958` | fix: G2P가 무거래를 inf로 내 승격 게이트를 거짓 통과시키던 것 차단 (+ analyze 타임아웃 2700) |
| `e32fa04` | Merge fix/g2p-no-trade-guard |

배포 3회: 14:05 KST(`d913cf5`), 16:25 KST(`bd222d6` + `alembic upgrade head` → 0018),
17:48 KST(`e32fa04`, 마이그레이션 없음). 전부 `active`, 기동 후 오류 없음.
requirements 변경 없음.

> ⚠️ **배포 시 `alembic upgrade head` 를 반드시 넣을 것.** CLAUDE.md 의 원라이너 배포
> 명령에는 마이그레이션 단계가 **빠져 있다**. 0018 은 수동으로 넣어 적용했다.
> (CLAUDE.md `!deploy` 안전 수칙에 이 경고와 아래 배포 금지 시간대를 8/3에 추가했다.)

## 8/3 작업 ① — broker_sync 잔고 정합 확인 (이월 7번) → 종결

DB·로그만으로 확인(외부 KIS 호출 없음). 결론: **무해하나 미검증**.
모의계좌 보유가 6/15~8/3 내내 **0~3종목**이라 연속조회(tr_cont)가 발동한 적이 없다.
재시도 소진 WARNING 배포 전/후 모두 0건, `sync_errors` 전 사이클 0, 하트비트 정상
(거래일 1,435~1,439회/일).

> ⚠️ **EGW00201은 로그로 셀 수 없다.** 재시도 단계가 `logger.debug`
> (`kis_adapter.py:662`)라 INFO 레벨 journalctl 에 안 남는다. 관측 가능한 신호는 전 재시도
> 소진 시의 WARNING 뿐이다. 핸드오프에 적혀 있던 "EGW00201 로그 증가 확인"은 실행 불가능한
> 지표였다.

**관측성 보강(배포 완료)** — 20종목을 넘는 순간 자동으로 드러난다:
`_fetch_paged` 가 2페이지 이상일 때만 INFO(`KIS 연속조회 N페이지 병합`), broker_sync
`collection_log` note 에 `holdings=<n>`(조회 미지원은 `n/a`). 실효 확인 완료 —
재기동 후 첫 사이클에 `holdings=2` 기록됨.

## 8/3 작업 ② — 후보 퍼널 재설계 Phase 1 (이월 10번) → 배포·실측 완료

계획서: `docs/plans/candidate-funnel-ai-scoring.md` (**개정본으로 갱신됨**).
TDD로 진행(테스트 6건 먼저 실패시킨 뒤 구현), `tests/test_candidate_funnel.py`.

| 변경 | 내용 |
|---|---|
| 신호 계산 일원화 | `_signal_from_frame` 이 정본, `_latest_strategy_signal` 은 DB 래퍼. 봉 수는 `_SIGNAL_LOOKBACK_BARS=400` 공유 |
| 종목 컨텍스트 1회 | `TickerContext` + `_build_ticker_contexts` — 전략 루프 **밖**에서 호출 |
| 저장 정책 | 신호 종목 전수 ∪ 나머지 상위 N (`maps_candidate_snapshot_top_n`, 기본 50) |
| 스키마 | `candidate_snapshot.entry_signal`(nullable) + `0018` |
| 0건 원인 구분 | 전략별 로그 + 잡 details `universe_size`/`signal_count`/`dropped_count` |

**8/3 16:50 실측 (성공):**

| 지표 | 7/31 | 8/3 |
|---|---|---|
| 저장 행수 | 10,288 | **664** (−93.5%) |
| 진입 신호 | — | **264건** |
| 소요 시간 | 17분 23초 | **4분 13초** (−76%) |

불변식 `saved 664 + dropped 8,864 = universe 1,191 × 전략 8` 성립.
전략별로 `stored = signals + min(50, 나머지)` 정확히 일치.
`_save_candidate_snapshot` 반환값이 **유니버스 크기 → 저장된 행 수**로 바뀌었다.

> 📌 **미검증으로 남은 것**: 8/3 은 `weak` 장세라 8전략 전부 진입 차단(`strategies_updated`
> 빈 배열)이었다. **신호 게이트와 08:55 주문 사이클의 집합 일치**는 국면이 strong/mixed 로
> 바뀌어 실제 주문이 나가는 날까지 확인할 수 없다.

**계획 대비 이탈 1건**: 계획은 "AI 대상 pre-scoring 루프 **삭제**"였으나 컨텍스트를 쓰도록
**변경만** 했다. 비싼 부분(OHLCV 재조회)은 사라졌지만 루프는 남는다 — 삭제하면 AI 대상
선정이 통째로 없어지는데 그 재설계가 Phase 2 범위이기 때문.

## 8/3 작업 ③ — 17:10 검증 잡 관전 (이월 15·20번) → 새 Sharpe·자동 강등 첫 실측

### 새 Sharpe 공식: 실효 확인

| 전략 | 7/31(구) | 8/3(신) | 음수폴드 | G2P | WFA |
|---|---|---|---|---|---|
| **pullback_v2** | −7.045 | **+0.518** | 1 | 1.086 | ✅ **PASS** |
| donchian_v1 | −2.925 | +0.175 | 2 | 1.167 | fail |
| contrarian_quality | 0.000 | 0.000 | 0 | `Infinity` | fail |
| multi_asset_trend_v1 | −5.822 | −0.015 | 1 | `Infinity` | fail |
| ath_breakout_v1 | −3.467 | −0.054 | 2 | `Infinity` | fail |
| pullback_v3 | −5.535 | −0.112 | 4 | 0.882 | fail |
| donchian_v2 | −8.262 | −0.394 | 4 | 1.179 | fail |
| ath_breakout_v2 | −7.572 | −0.412 | 1 | `NaN` | fail |

−2.9~−8.3의 비현실적 음수가 전부 0 근처로 정상화됐다. **pullback_v2 가 WFA 3조건을 처음
통과** — 8/2 실험에서 "구조적 결함 확정" 판정을 받은 pullback_v3 의 전신이라는 점이 얄궂다.

### 자동 강등 첫 가동 — 4전략 강등

`ath_breakout_v2`(48.6) · `donchian_v1`(48.1) · `multi_asset_trend_v1`(48.4) ·
`pullback_v3`(34.9) → **mock_candidate → research** (점수 <50 연속 10회).
핸드오프는 pullback_v3 하나를 예상했으나 4개가 걸렸다.

> 🔴 **운영 영향 — 판단 필요**: `mock_candidate` 이상이 **6개 → 2개**
> (`ath_breakout_v1`, `donchian_v2`)로 줄었다. `_order_candidates` 가 승격 단계로
> 필터하므로 **주문 가능 전략이 1/3로 축소**됐다. 의도된 동작이지만 규모가 예상보다 크다.

### 승격은 여전히 passed=0, failed=8 — 단 이유가 바뀌었다

- **pullback_v2**: WFA 통과했으나 tradeability **43.5 < 60** 에서 차단
- **ath_breakout_v1**: 점수 **78.7**(live_candidate 임계 75 초과)인데 `mock_months=0.0` 으로 차단

## 8/3 작업 ④ — G2P `Infinity`/`NaN` (이월 결함표 🔴) → 수정·배포 완료

### 8/3 낮 보고 정정

낮에 "`NaN` 은 어떤 비교에도 False → G2P 조건이 **영구 실패**"라고 보고했는데 **반대였다.**
`_evaluate` 는 `if mean_g2p < WF_OOS_IS_G2P_MIN: 실패사유 추가` 구조라 `NaN < 0.6` 도
`inf < 0.6` 도 False → 사유가 붙지 않고 **조용히 통과**한다. 제한이 아니라 **과잉 허용**이었다.

### 근본 원인 — "무손실"이 아니라 "무거래"

`gains`/`losses` 는 일별 수익률에서 나온다. 거래가 0건이면 둘 다 0 → `losses > 0` 이
False → `inf`. **"손실이 없다"와 "거래를 안 했다"가 같은 값**이 됐다.

8/3 fold 15건 실측에서 `oos_g2p = Infinity` 는 **예외 없이 `oos_sharpe = 0.000`**
(그 구간 무거래)과 동반했다. 관측된 13건이 전부 무거래다.

| 전략 | 실태 |
|---|---|
| contrarian_quality | **5/5 폴드 OOS 무거래**인데 G2P 조건 통과 중이었다 |
| multi_asset_trend_v1 | 3/5 무거래 + 음수폴드 1(통과) + Sharpe −0.015 → **Sharpe 가 0만 넘으면 WFA 전체 통과**할 상태였다 |
| ath_breakout_v2 | fold 1 은 IS·OOS 둘 다 무거래 → `inf/inf` = `NaN` |

부수: `_selection_score` 가 비유한 G2P 를 캡으로 바꿔 **파라미터 선택이 "거래 안 하는
조합"에 G2P 만점**을 주고 있었다. 소스 수정으로 함께 해소.

### 변경

| 파일 | 내용 |
|---|---|
| `common/constants.py` | `GAIN_TO_PAIN_CAP=3.0`, `WF_NO_TRADE_FOLD_MAX=1` 신설 |
| `backtest/engine.py` | `_gain_to_pain()` — 손실 있음→비율, 무손실→cap, **무거래→0.0**. `inf` 를 절대 반환하지 않는다 |
| `validation/walk_forward.py` | `g2p_ratio` 비유한 시 0.0 / `FoldResult.oos_trades` + `no_trade_folds` / `_evaluate` 에 비유한 실패 처리 + 무거래 전용 사유 |

> 📌 `oos_trades` 기본값은 **`None`(미지)** 이다. `0` 으로 뒀더니 값을 안 채운 경로가 전부
> "무거래"로 오인돼 기존 테스트가 깨졌다 — **미지와 실측 0을 반드시 구분**할 것.

WFA 통과 조건이 **3개 → 4개**가 됐다. `validation/CLAUDE.md` 와 모듈 docstring 갱신함.
테스트 6건 추가(`test_backtest.py` 2, `test_walk_forward.py` 4). **DB 컬럼 추가 없음.**

**예상 영향**: 8/3 데이터 기준 **판정이 뒤집히는 전략은 없다.** 잠재 거짓통과만 제거된다.

## 8/3 작업 ⑤ — analyze 배치는 "안 돈 게" 아니라 **타임아웃으로 죽었다**

`/etc/cron.d/maps-analyze` 정상, **16:00:01 에 실행됨**. `analysis_run` id=25:
`status=failed`, `picks_count=0`, `error_message=timeout(1800s)`. 16:30:03 강제 종료
— **$8.25 쓰고 0건**.

**원인 1 — 여유가 이미 없었다**: 7/27 17분 → 7/30 28분 → 7/31 29분 → 8/3 **30분 초과**.
→ 조치: `ANALYZE_TIMEOUT` **1800 → 2700**(배포 완료, 서버 반영 확인).

**원인 2 🔴 — strategy-selector 가 승격 단계를 문서에서 추측한다 (미해결)**:
`/opt/maps/.claude/agents/strategy-selector.md` 의
`tools: Read, Task*, WebFetch, WebSearch` — **`Bash` 가 없다.** psql 을 못 써
`promotion_history` 를 볼 수 없으니 사람용 문서인 `HANDOFF.md` 를 데이터 소스로 삼는다.
8/3 에는 그 추측이 틀려 "stage 확인 불가 → 전량 배제"를 냈고, 오케스트레이터가 정정 후
**재선정을 다시 돌려**(서브에이전트 1건 9.6분) 예산을 까먹었다. **타임아웃 상향은 증상
완화일 뿐이다** → Next Steps 참고.

**원인 3 — 내 배포가 실행 중에 겹쳤다 (과실)**: `HANDOFF.md` mtime = **16:25:20**.
analyze 가 16:00~16:30 에 도는 중 `git pull` 이 **에이전트가 읽는 작업 트리를 바꾸고**
`systemctl restart` 까지 했다. → 조치: CLAUDE.md `!deploy` 에 **16:00~16:45 배포 금지**
와 `flock -n /tmp/maps_analyze.lock` 확인 절차 명시(17:48 배포 시 실제로 확인 후 진행).

## 이전 날(8/2) — 전략 실험 시리즈 (운영 콘솔 API 실행, backtest_run_log 에 전부 저장)

새 백테스트 기능(기간·유니버스·판정)으로 pullback_v3 부진의 뿌리를 추적한 결론:

1. **pullback_v3 — 구조적 결함 확정, 게이트 차단이 정당했음.**
   전 기간/3개 강세 구간 × all/KOSPI/KOSDAQ × 진입 파라미터 코너 4개 = 11회 실행 전부에서
   승률 58~75% & **손익비 <1.25** (대부분 <1). 문제는 기간·유니버스·진입이 아니라
   **청산 구조**(MA5 상향 크로스 즉시 익절 — 하드코딩, 파라미터로 못 넓힘).
   rsi<8 개선 효과는 2020 V반등장 한정(2017·2023 재현 실패 — 과적합).
   유일한 PASS: 2020-04~2021-06 + KOSPI 한정(샤프 0.39 턱걸이).
   → 청산 재설계(v3.3, 이익목표/트레일링 파라미터화)는 **보류** — 우선순위 낮음.
2. **donchian_v2 — 실증 승자.** 같은 3개 강세 구간에서 유일하게 유의미한 수익
   (손익비 2.2~6.1, 승률 39~58%). **전 기간(2016~2026) 포트폴리오 리플레이:
   CAGR +8.0%/yr, MDD 19.3%, 샤프 0.67, 449거래, PASS** — 단 연도별로 5/11개 해가
   마이너스(2022 −12.6%), 수익은 2017/2020/2025/2026에 집중. 생존자 편향(현재 ADV 상위
   30 풀) 감안해 할인해 볼 것.
3. **ath_breakout_v1 — KOSPI 대형주에서는 판단 불가** (ATH 갱신 희소 → 거래 8~10건, 표본
   미달). 자연 서식지는 신규상장·중소형 — `recent_ipo` 유니버스로 별도 검증 가치 있음.
4. 참고 관찰: 샤프 단일 잣대는 추세추종의 울퉁불퉁한 수익 곡선에 구조적으로 불리
   (donchian_v2 2023: CAGR +2.3%인데 샤프 0.01). 게이트 설계 재론 시 참고.

**후속 조사·조치 (저녁):**
- 롯데렌탈(089860) pullback_v3 매수 조사 → **버그 아님**: 7/30 08:55 주문 사이클의
  mock(모의) 주문. pullback_v3는 5/23 점수 71.75로 mock_candidate 승격돼 있었음
  (이후 점수 붕괴 28~48에도 강등 메커니즘 부재) → **자동 강등 기능 구현** (`3aaf4fa`).
  다음 검증 잡부터 최근 10회 연속 점수 <50이면 mock→research 강등 + Slack WARN.
  단 새 Sharpe 공식으로 점수가 오르면 연속성이 끊겨 강등 안 될 수 있음 (정당한 재평가).
- 블로그 원고 신규: `docs/blog_series_backtest/11_눌림목_전략_부검기.txt` —
  오늘 실험 시리즈를 리서치 포스팅 스타일로 정리 (붙여넣기 검사 통과, 발행 대기)

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
운영 DB PostgreSQL. **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.
서버는 **`debe036`** 배포 완료 (8/5 10:42 KST), alembic **`0018_candidate_entry_signal`**(head).
테스트 **647 passed**. requirements 변경 없음. 로컬도 master = `debe036`, head 0018.
KIS 모의계좌는 **`50200591-01`** (8/5 재생성 — 구 50185813-01 만료, 위 "8/5 세션" 절).

> 🔴 **16:00~16:45 KST 는 배포 금지 시간대다** — `/etc/cron.d/maps-analyze` 가 매 거래일
> 16:00 에 `/analyze` 를 45분 상한으로 돌린다. 배포 전 확인:
> `flock -n /tmp/maps_analyze.lock true && echo 배포가능 || echo 대기`

## 8/2 낮 점검 세션 기록 (읽기 전용 — 발견 당시 기준)

백테스트 점검 — 계획서 `C:\Users\jack\.claude\plans\maps-expressive-wreath.md`, 보고서는 채팅으로 전달.

**확인된 정상 동작:**
- 콘솔 E2E (로컬 8001 스모크): `GET /api/v1/backtest` → `POST /run`(pullback_v3, donchian_v1) → `recent_runs` 반영까지 정상. 로컬 alembic이 깨져 있어도 기동 시 `create_all()`이 `backtest_run_log`를 만들어 동작.
- `8db50a9`(numpy 강제변환) 운영 실효 확인: journalctl에 8/2 00:27 `schema "np"` 500이 마지막, 00:30 배포 후 콘솔 실행 3건 연속 200 → `backtest_run_log` 3행 정상 기록.
- 검증 잡: 7/29·30·31 매일 17:12 KST success, 8전략 × WFA/Plateau/MC 각 8건, skipped 없음.
- 운영 데이터: `historical_ohlcv` 2016-01-04~2026-07-31, 585만 행, **2016봉 이상 1,841티커** — WFA 데이터 충분.

**신규 발견 결함 (심각도순, 미수정 — 보고만):**

| 심각도 | 내용 |
|---|---|
| 🔴 | **Sharpe 왜곡** → 이월 20번 |
| 🔴 | **로컬 `maps.db` alembic 스탬프 깨짐** — `alembic_version`이 체인에 없는 `0013_market_regime_korea_weak_guard`. `alembic upgrade head` 불가. `alembic stamp`로 교정 필요 (운영은 정상) |
| 🟡 | `engine.run()` 전 호출 경로가 `market_cap=0.0` → 항상 소형주 슬리피지 0.15% (대형주 비용 과대) |
| 🟡 | `ohlcv_repo.to_dataframe` `index.name` 미설정 → 콘솔 경로 `TradeRecord.ticker=="unknown"` |
| 🟡 | `api/wfa.py:142` `tickers[0]` — 스케줄러 `_pick_wfa_ticker` 개선 미반영 |
| 🟡 | `scheduler._wfa_required_bars()` 2016 하드코딩 — analyzer 기본값과 어긋날 수 있음 |
| 🟡 | `portfolio_replay.py:134` entry-only 신호 시 `sig["exit_signal"]` KeyError 가능; `exit_reason` 항상 `""` |
| ✅ | ~~`gain_to_pain=inf`~~ → **8/3 저녁 수정·배포 완료** (`3728958`). 원인은 "무손실"이 아니라 **"무거래"** 였다. 상세는 "8/3 작업 ④" 절. 낮에 "NaN 은 영구 실패"라고 적었던 것은 **오보** — 실제로는 `inf` 도 `NaN` 도 조건을 **조용히 통과**시켰다 |
| 🔵 | UI 진행률 5단계 장식(실제 1단계만), `progress_pct` 항상 100, "WFA 기준" 문구 낡음, `started_at` UTC naive(화면 9시간 어긋남) |
| 🔵 | `cost_sensitivity.py` `net_cagr=None` 고정 · `engine.run()` `universe` 인자 미사용 · `backtest/CLAUDE.md` 낡음(모듈 3개 누락·상수명 오기) · `/api/v1/wfa`·`robustness` 라우터 테스트 부재 |

## 미커밋 상태

- HANDOFF.md 이 갱신분만 미커밋. (로컬 master 는 `debe036` = origin = 서버)
- `apps/mobile/google-services.json` — 커밋 여부 사용자 결정 대기 (이월).

**부수 확인 (8/3)**: `order_log` `0000031820` = buy `expired` qty 1253 / fill_qty 21 —
이월 8번(부분체결이 만료 처리된다)의 **실물 사례**. 별건으로 남긴다.

## 주의 (이월, 계속 유효)

- **alembic revision ID는 32자 이내** (varchar(32) — 8/1 배포 사고 1).
- **DB에 넣는 수치가 pandas/numpy를 거쳤으면 `float()`/`int()` 강제** — SQLite는 np.float64를
  받아줘서 로컬 테스트로 못 잡는다 (8/1 배포 사고 2, 8/2 운영 로그로 수정 실효 확인됨).
- 배포 실패가 alembic 단계면 서비스는 구 코드로 살아 있다 — 마이그레이션만 고쳐 재배포.
- PowerShell here-string 커밋은 한 호출에 하나만. 닫는 `'@` 뒤에 아무것도 잇지 말 것.
- `date.today()`+UTC 저장 컬럼 함정, `order_log` 컬럼명, `journalctl | grep -v broker_sync`,
  analyze 픽과 스케줄러 주문은 다른 파이프라인, `git add -u` 전에 `git status`.
- 로컬 `.env`는 `MAPS_AUTH_ENABLED=true` — 로컬 API 스모크도 로그인 세션 필요.

## Next Steps

1. 🔴 **8/6(목) 08:55 주문 사이클 확인 — 최우선.** 8/5 는 계좌 만료로 관측 실패.
   이제 ⑴ 신호 게이트, ⑵ 강등(주문 가능 2전략), ⑶ 새 계좌가 모두 정상 조건에서
   처음 돌아간다. 확인할 것: 주문이 실제 나갔는지(40910000 재발 여부), `skipped_orders`
   사유 분포, **후보 생성 시점 신호 집합과 08:55 재계산 신호 집합이 일치하는지**
   (같은 OHLCV·같은 400봉이므로 일치해야 정상 — 어긋나면 그게 곧 버그).
   장세가 weak 로 돌아가면 전량 차단이라 또 이월. 부수: 002810 잔여 전략 트레이드가
   DB 에 열린 채 남아 오류를 내는지도 같이 볼 것 (브로커 보유는 0).
2. ~~🔴 8/4 17:10 검증 잡 — G2P 수정 실효 확인~~ → **✅ 8/5 확인·종결** (위 "8/5 세션" 절).
   Infinity/NaN 0건, 무거래 사유 정상 부착, 통과 수 불변.
3. 🔴 **strategy-selector 의 승격 단계 입력원** (8/3 작업 ⑤ 원인 2).
   에이전트에 `Bash` 가 없어 `promotion_history` 를 못 읽고 사람용 문서인 `HANDOFF.md` 를
   파싱한다. 타임아웃 상향(2700)은 증상 완화일 뿐이다. 두 방향 —
   (a) 에이전트에 `Bash` 부여(무인 실행에 셸 권한을 늘린다),
   (b) **파이프라인이 stage 를 미리 조회해 입력 JSON 으로 주입**(권장 — 에이전트가 사람용
   문서를 파싱하는 구조 자체를 없앤다). 설계 필요.
4. 🟡 **자동 강등 4건에 대한 판단.** `mock_candidate` 6개 → 2개. 의도된 동작이지만 규모가
   커서 모의 매매 관측 표본이 줄어든다. 강등 임계(연속 10회 <50)를 유지할지,
   새 Sharpe 기준으로 재산정할지 사용자 판단 필요.
5. 🔵 **후보 퍼널 Phase 2 (AI 스코어링) — 착수 가능.** Phase 1 이 배포·실측을 마쳤으므로
   대상 선정이 비로소 의미를 갖는다(신호 종목 264건 중 상위 N). 사양·정정사항은
   `docs/plans/candidate-funnel-ai-scoring.md` 참고. **Bedrock 호출자 둘을 함께** 옮길 것
   (`technical_scorer` + `contrarian_analyzer`, `aws_bedrock_model_id` 공유).
6. 블로그 21편 발행 — 원고 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.
   (신규 기능·Sharpe 수정으로 일부 원고 내용이 낡았을 수 있음 — 발행 전 콘솔 관련
   편의 스크린샷·문구 확인)
7. `google-services.json` 커밋 여부 사용자 결정.
8. 2015년 OHLCV 백필 (운영 절차) — 연 단위 청크로
   `POST /api/v1/scheduler/backfill/ohlcv?start=2015-01-01&end=2015-12-31` → 2016-01-03까지.
   실패는 `job_run_log`에 남음. (운영 data_start 실측 2016-01-04)
9. ath_breakout_v1 × `recent_ipo` 유니버스 검증 (IPO 전략 글 가설 — 콘솔에서 바로 가능)
10. 🟡 **pullback_v3 청산 재설계 (v3.3) — 정식 과제.** 8/2 실험 11회로 좌표 확정:
   문제는 진입이 아니라 청산(MA5 상향 크로스 즉시 익절 하드코딩 → 손익비 상한 <1.25,
   승률 58~75%로도 비용을 못 이김).
   - 방향: 청산을 파라미터화 — ① 이익목표 P%/트레일링 스탑 도입(IPO 글의 P=20%/L=10%,
     손익비 2:1 교훈) 또는 ② MA5 대신 MA_long 크로스 청산(보유 연장). 목표 손익비 ≥ 1.3.
   - 검증 프로토콜: 세 강세 구간(2020-04~2021-06 / 2017-01~11 / 2023-01~07) × KOSPI에서
     전부 재현돼야 채택 — rsi<8이 2020만 통과했던 과적합 함정 재발 방지.
   - 규약: 전략 버전업이므로 param_grid·strategy_guides·catalog 동반 갱신 (CLAUDE.md),
     live_rules 손절률 재검토. WFA 정식 검증 통과까지는 research 단계 유지.
   - 착수 시점 판단 재료(8/3 실측): pullback_v3 새 Sharpe **−0.112 / 음수폴드 4**,
     tradeability 34.9 로 8전략 중 최하위이며 **자동 강등돼 research 로 내려갔다.**
     donchian_v2 도 −0.394 / 음수폴드 4 로 부진. 반면 **pullback_v2 가 WFA 유일 통과**
     (+0.518) — v3.3 재설계 전에 "왜 전신이 더 나은가"를 먼저 보는 편이 값싸다.

### 이월 (번호 유지 · 22번 8/3 신규 · 7·10·20·21번 종결)

5. 워치리스트·보유 화면 브라우저 CSS 확인 (네비 1줄, 카드 2열)
6. 픽 만료 가드 로그 — ARMED 픽 0건이라 아직 안 뜸
7. ~~KIS 잔고·주문 페이지네이션~~ → `86d5b7e` 해소. **8/3 운영 확인 완료 — 단 "미검증"**:
   모의계좌 보유가 6/15~8/3 내내 **0~3종목**이라 페이지네이션이 발동한 적이 없다
   (`portfolio_snapshot.holdings` 이력). 수정은 무해하나 실효는 입증 불가.
   재시도 소진 WARNING은 배포 전(7/31~8/2) 0건 / 후(8/2~8/3) 0건, `sync_errors` 전 사이클 0,
   broker_sync 하트비트 거래일 1,435~1,439회/일 정상. **초당 한도(EGW00201) 압박 징후 없음.**
   → 20종목을 넘는 순간 자동으로 드러나도록 **관측성 보강**함(아래 절). 재조사 불필요.
8. 🟡 부분체결이 만료 처리된다 (`expire_pending_orders`)
9. 🟡 매매일지 페어링이 티커 단위 (`trade_review.py:119`)
10. ~~🟡 후보 퍼널 재설계~~ → **Phase 1 배포·실측 완료 (8/3)**. 위 "8/3 작업 ②" 절 참고.
    저장소 정본 `docs/plans/candidate-funnel-ai-scoring.md` 를 개정본으로 갱신해 뒀다
    (원안 정정 4건 포함). **Phase 2(AI)만 남았다** → Next Steps 4번.
11. 🟡 후보 생성 누락일 2건 (7/01, 7/17) 잡 실패 로그 확인
12. 🟡 분석 워치리스트 누적 2건뿐 — 게이트 전량 탈락 중
13. `analysis_pick` id=1 CLOSED인데 exit_reason 빈 것
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨.
    **8/5 갱신**: 계좌 재생성으로 잔고 1억 리셋 — 이 이력과 새 계좌를 잇지 말 것
15. **8/3 실측으로 갱신**: 새 Sharpe 적용 후에도 승격은 여전히 passed=0/failed=8 이지만
    **이유가 바뀌었다.** `ath_breakout_v1` 은 점수 **78.7**(live_candidate 임계 75 초과)인데
    `mock_months=0.0` 으로 차단, `pullback_v2` 는 WFA 유일 통과인데 점수 43.5 < 60 으로 차단.
    즉 병목이 "점수가 낮아서"에서 **"mock 기간/점수 중 하나가 어긋나서"** 로 이동했다.
    ~2026-09-01 `mock_months ≥ 3` 충족 예정이던 계산은 강등 4건으로 무효 — 재산정 필요.
16. 업종 필터 활성화 (`earnings_revision` 0.25 자리표시자)
17. 애드센스 — `maps.magable.kr` 등록 (사용자 계정 작업)
18. 블로그 기획서 수정본 — 원고(시리즈 11편 + 백테스트 10편)를 정본으로 삼는 편이 빠름
19. 이월: KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트 mock화,
    서명 릴리스 APK, `order_log_backup_20260724` DROP 가능
20. ~~🔴 Sharpe 산출 구조 왜곡~~ → **수정 완료 (8/2 오후 작업 세션, 미커밋)**.
    방식: rf를 **일별 투자 비중(노출)만큼만 차감** — `engine._compute_metrics`와
    `portfolio_replay._metrics` 동일 규칙. 완전 투자 시 종전 공식과 동치, 노출 규모에
    수학적으로 불변. 같은 조건 실측: 콘솔 pullback_v2 3개월 샤프 **−12.52 → +0.650**.
    회귀 테스트 5건(`test_backtest.py`·`test_portfolio_replay.py`).
    🟡 4번(ticker="unknown")도 동반 수정(`to_dataframe`이 `index.name=ticker` 설정).
    🟡 3번(market_cap=0 슬리피지)은 **미처리** — DB에 시총 데이터가 없어 원천 확보가 선행.
    **잔여 후속 → 8/3 해소**: 8/3 17:10 검증 잡에서 새 공식 첫 산출. 8전략 전부
    −2.9~−8.3 → 0 근처로 정상화, `pullback_v2` WFA 첫 통과. **이 항목 종결** (상세는
    "8/3 작업 ③" 절). 다만 G2P `Infinity`/`NaN` 이 새 병목으로 드러났다 → 결함표 참고.
21. ~~🔴 로컬 `maps.db` alembic 스탬프 깨짐~~ → **해소**. 현재 로컬·운영 모두
    head `0018_candidate_entry_signal`.
22. 🟡 **자동 강등 규모** (8/3 신규) — 첫 가동에 4전략이 `mock_candidate → research`.
    주문 가능 전략 6개 → 2개. 임계(연속 10회 <50) 유지 여부 사용자 판단 필요
    (Next Steps 3번).

## 핵심 파일 맵 — 8/3 변경분

- `maps/ops/scheduler.py` — `TickerContext`(dataclass), `_build_ticker_contexts()`,
  `_signal_from_frame()`(신호 정본), `_latest_strategy_signal()`(DB 래퍼),
  `_SIGNAL_LOOKBACK_BARS=400`, `_save_candidate_snapshot(contexts=, stats=)`.
- `maps/execution/kis_adapter.py` — `_fetch_paged()` 2페이지 이상 INFO 로그.
- `maps/common/models.py` — `CandidateSnapshot.entry_signal`.
- `maps/common/settings.py` — `maps_candidate_snapshot_top_n`(기본 50).
- `tests/test_candidate_funnel.py` — 신규 6건 (신호 경로 일치, N회 로드, 게이트, 카운터).
- `alembic/versions/0018_candidate_entry_signal.py`.
- `maps/backtest/engine.py` — `_gain_to_pain()` (무거래=0.0 / 무손실=cap, **inf 반환 없음**).
- `maps/validation/walk_forward.py` — `FoldResult.oos_trades`(기본 `None`=미지),
  `WalkForwardResult.no_trade_folds`, `g2p_ratio` 유한 보장, `_evaluate` 조건 4개.
- `maps/common/constants.py` — `GAIN_TO_PAIN_CAP`, `WF_NO_TRADE_FOLD_MAX`.
- `scripts/run_analyze_cron.sh` — `ANALYZE_TIMEOUT` 2700.
- `CLAUDE.md` — `!deploy` 안전 수칙에 alembic 경고 + 16:00~16:45 배포 금지.

## 핵심 파일 맵 (8/2 점검에서 본 곳)

- `maps/backtest/engine.py` — `BacktestEngine.run`, `_compute_metrics`(Sharpe·rf 3%), 사이징 상수.
- `maps/api/backtest.py` — 콘솔 API (`GET /api/v1/backtest`, `POST /run`), `RUNNABLE_STRATEGIES` 7종,
  `float()`/`int()` 강제변환(8db50a9), `BacktestRunLog` INSERT.
- `maps/ops/scheduler.py` — `run_validation` → `_generate_validation_metrics` → WFA/Plateau/MC 저장.
- `maps/validation/walk_forward.py` — 2016봉 요구, 통과 3조건 (sharpe_mean>0 AND 음수폴드≤1 AND G2P≥0.6).
- 응답 스키마 필드는 `recent_runs` (`api/schemas.py:305`) — `runs` 아님.
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
