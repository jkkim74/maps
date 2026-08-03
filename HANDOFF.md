# HANDOFF

> 작성일: 2026-08-03 (월, 저녁) · 작성자: 세션 에이전트 (**회사 PC, 키 `D:\ssh_maps\`**)
> 서버 = **`bd222d6`** 배포 완료, alembic **`0018_candidate_entry_signal`**, 테스트 **640 passed**.
> 8/2 기록은 아래 "이전 날(8/2)" 절로 내렸다 — 전략 실험 결론은 여전히 유효하다.

## Goal — 이 작업이 향하는 곳

후보 생성이 **전략 신호를 보지 않는** 구조를 고쳐, "후보"를 유동성·추세 상위 종목이 아니라
**"이 전략이 오늘 사겠다고 말한 종목"** 으로 되돌리는 것. 그 위에 AI 스코어링을 붙인다
(Phase 2, 착수 전). 병행 목표는 승격 게이트가 **실제 성과로** 갈리게 만드는 것 —
Sharpe 왜곡 수정(8/2)과 자동 강등(8/2)이 8/3에 처음 실측됐다.

## 8/3 커밋·배포

| 해시 | 내용 |
|---|---|
| `d913cf5` | feat: KIS 연속조회 발동·보유 종목 수를 로그에 남긴다 (관측성 보강) |
| `0a186c0` | feat: 후보 퍼널을 신호 기반으로 재설계 (Phase 1) — `0018` |
| `bd222d6` | Merge feat/candidate-funnel-signal-gate |

배포 2회: 14:05 KST(`d913cf5`), 16:25 KST(`bd222d6` + `alembic upgrade head` → 0018).
둘 다 `active (running)`, 기동 후 오류 없음. requirements 변경 없음.

> ⚠️ **배포 시 `alembic upgrade head` 를 반드시 넣을 것.** CLAUDE.md 의 원라이너 배포
> 명령에는 마이그레이션 단계가 **빠져 있다**. 0018 은 수동으로 넣어 적용했다.

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
서버는 **`bd222d6`** 배포 완료 (8/3 16:25 KST), alembic **`0018_candidate_entry_signal`**(head).
테스트 **640 passed**. requirements 변경 없음. 로컬도 master = `bd222d6`, head 0018.

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
| 🔴 | ~~🟡 `gain_to_pain=inf`~~ → **8/3 승격 판정을 실제로 왜곡 중임이 확인됨. 승격**. `walk_forward_results.mean_g2p` 가 8전략 중 4개에서 비정상: `Infinity` 3(contrarian·multi_asset_trend_v1·ath_breakout_v1), `NaN` 1(ath_breakout_v2). **`NaN` 은 어떤 비교에도 False → G2P≥0.6 조건이 조용히 영구 실패**(Sharpe 가 양수로 돌아서도 통과 불가). **`Infinity` 는 조건을 통과시킨다** — "손실 거래 0" 이라는 뜻이라 표본이 적으면 무의미한 거짓 통과. multi_asset_trend_v1 은 Sharpe −0.015 / 음수폴드 1 로 **통과 직전**이라 실재 위험 |
| 🔵 | UI 진행률 5단계 장식(실제 1단계만), `progress_pct` 항상 100, "WFA 기준" 문구 낡음, `started_at` UTC naive(화면 9시간 어긋남) |
| 🔵 | `cost_sensitivity.py` `net_cagr=None` 고정 · `engine.run()` `universe` 인자 미사용 · `backtest/CLAUDE.md` 낡음(모듈 3개 누락·상수명 오기) · `/api/v1/wfa`·`robustness` 라우터 테스트 부재 |

## 미커밋 상태

- HANDOFF.md 이 갱신분만 미커밋. (로컬 master 는 `bd222d6` = origin 과 동일)
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

1. 🔴 **8/4(화) 08:55 주문 사이클 확인 — 최우선.** 이번 배포에서 **두 가지가 동시에**
   바뀌어 첫 주문일 관측이 중요하다:
   ⑴ 후보 풀이 10,288행 → 664행(신호 게이트), ⑵ 자동 강등으로 주문 가능 전략이 6개 → 2개
   (`ath_breakout_v1`, `donchian_v2`).
   확인할 것: 주문이 나갔는지, `skipped_orders` 사유 분포, **후보 생성 시점 신호 집합과
   08:55 재계산 신호 집합이 일치하는지**(어긋나면 그게 곧 버그 — 같은 OHLCV·같은 400봉을
   쓰므로 일치해야 정상). 단 장세가 여전히 `weak` 면 전량 차단이라 확인 불가 — 그때는
   strong/mixed 로 바뀌는 날까지 이월.
2. 🔴 **G2P `Infinity`/`NaN` 처리** (위 결함표 참고). 승격 판정을 이미 왜곡 중이다.
   `NaN` → 영구 실패, `Infinity` → 거짓 통과. 8전략 중 4개가 해당.
   손실 거래가 0인 폴드의 G2P 정의를 먼저 정해야 한다(상한 클램프 vs 별도 사유 코드).
3. 🟡 **자동 강등 4건에 대한 판단.** `mock_candidate` 6개 → 2개. 의도된 동작이지만 규모가
   커서 모의 매매 관측 표본이 줄어든다. 강등 임계(연속 10회 <50)를 유지할지,
   새 Sharpe 기준으로 재산정할지 사용자 판단 필요.
4. 🔵 **후보 퍼널 Phase 2 (AI 스코어링) — 착수 가능.** Phase 1 이 배포·실측을 마쳤으므로
   대상 선정이 비로소 의미를 갖는다(신호 종목 264건 중 상위 N). 사양·정정사항은
   `docs/plans/candidate-funnel-ai-scoring.md` 참고. **Bedrock 호출자 둘을 함께** 옮길 것
   (`technical_scorer` + `contrarian_analyzer`, `aws_bedrock_model_id` 공유).
5. 블로그 21편 발행 — 원고 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.
   (신규 기능·Sharpe 수정으로 일부 원고 내용이 낡았을 수 있음 — 발행 전 콘솔 관련
   편의 스크린샷·문구 확인)
6. `google-services.json` 커밋 여부 사용자 결정.
7. 2015년 OHLCV 백필 (운영 절차) — 연 단위 청크로
   `POST /api/v1/scheduler/backfill/ohlcv?start=2015-01-01&end=2015-12-31` → 2016-01-03까지.
   실패는 `job_run_log`에 남음. (운영 data_start 실측 2016-01-04)
8. ath_breakout_v1 × `recent_ipo` 유니버스 검증 (IPO 전략 글 가설 — 콘솔에서 바로 가능)
9. 🟡 **pullback_v3 청산 재설계 (v3.3) — 정식 과제.** 8/2 실험 11회로 좌표 확정:
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
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨
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

## 핵심 파일 맵 (8/2 점검에서 본 곳)

- `maps/backtest/engine.py` — `BacktestEngine.run`, `_compute_metrics`(Sharpe·rf 3%), 사이징 상수.
- `maps/api/backtest.py` — 콘솔 API (`GET /api/v1/backtest`, `POST /run`), `RUNNABLE_STRATEGIES` 7종,
  `float()`/`int()` 강제변환(8db50a9), `BacktestRunLog` INSERT.
- `maps/ops/scheduler.py` — `run_validation` → `_generate_validation_metrics` → WFA/Plateau/MC 저장.
- `maps/validation/walk_forward.py` — 2016봉 요구, 통과 3조건 (sharpe_mean>0 AND 음수폴드≤1 AND G2P≥0.6).
- 응답 스키마 필드는 `recent_runs` (`api/schemas.py:305`) — `runs` 아님.
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
