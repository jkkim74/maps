# HANDOFF

> 작성일: 2026-08-02 (일, 낮) · 작성자: 세션 에이전트 (집 PC, 키 `D:\maps\`)
> 주제: **백테스트 기능 전체 점검** (읽기 전용 세션의 발견) + **같은 날 작업 세션의 후속 반영**.
> 점검 결론: 기능은 정상 구동, 단 **Sharpe 산출 구조 왜곡** (아래 20번) —
> **→ 같은 날 작업 세션에서 수정 완료** (노출 가중 rf, 미커밋. 아래 20번 참고).
> 직전 핸드오프(KIS tr_cont 페이지네이션, 8/2 오전): git `86d5b7e` 커밋 메시지·`tests/test_kis_adapter.py` 참고.

## ⚡ 병행 세션 현행화 (점검 세션이 못 본 같은 날 작업분)

- **SCR-21 배치 모니터: 완성·커밋됨** — `1ec7819` (라우터·화면·테스트 14건 포함, 미완성 아님)
- **백테스트 기간·대상·판정 기능: 커밋됨** — `ea0cab2` (0017 마이그레이션 포함)
- **이월 21번(로컬 alembic 스탬프): 해소** — `0014_regime_korea_weak_guard`로 stamp 교정 후
  head(`0017_bt_run_period_verdict`)까지 적용 완료
- **이월 20번(Sharpe 왜곡): 수정 완료, 미커밋** — 상세는 20번 항목
- 두 커밋은 push됨, **운영 배포는 아직** (서버 `86d5b7e` 그대로, 0016·0017 마이그레이션 대기)

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://maps.magable.kr`.
운영 DB PostgreSQL. **SSH 키는 PC마다 다름**: 회사 PC `D:\ssh_maps\`, 집 PC `D:\maps\`.
서버는 **`86d5b7e`** 배포 상태 그대로 (이번 세션 배포 없음). alembic `0015_backtest_run_log`(head).
테스트 **612 passed** (미커밋 SCR-21 테스트 포함, 기준선 598에서 증가).

## 이번 세션 작업 (커밋 없음)

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
| 🟡 | `gain_to_pain=inf`가 `walk_forward_fold_results`에 그대로 저장될 수 있음 |
| 🔵 | UI 진행률 5단계 장식(실제 1단계만), `progress_pct` 항상 100, "WFA 기준" 문구 낡음, `started_at` UTC naive(화면 9시간 어긋남) |
| 🔵 | `cost_sensitivity.py` `net_cagr=None` 고정 · `engine.run()` `universe` 인자 미사용 · `backtest/CLAUDE.md` 낡음(모듈 3개 누락·상수명 오기) · `/api/v1/wfa`·`robustness` 라우터 테스트 부재 |

## 미커밋 상태 (이월)

- **Sharpe 노출 가중 rf 수정** (8/2 오후 작업 세션): `backtest/engine.py`,
  `backtest/portfolio_replay.py`, `data/ohlcv_repo.py`(index.name), 회귀 테스트 —
  ship 대기. (SCR-21·백테스트 기능은 `1ec7819`·`ea0cab2`로 커밋 완료)
- `apps/mobile/google-services.json` — 커밋 여부 사용자 결정 대기.

## 운영 확인 필요 (다음 거래일 = 8/3 월, KIS 페이지네이션 `86d5b7e` 후속)

- `broker_sync` 잔고 종목 수가 실제 보유와 일치하는지 — 특히 보유 20종목 초과 시.
  기존 FILLED 오판 SELL이 있었다면 정합 복귀로 상태 변화 로그가 나올 수 있다.
- 연속조회로 호출 수 증가 → 모의투자 초당 한도(EGW00201) 로그 증가 여부 한 번 확인
  (기존 `_send_with_retry` 재시도로 자가복구되는 부류).

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

1. ~~🔴 Sharpe 재설계~~ → **완료(미커밋)** — ship + deploy(0016·0017 마이그레이션 포함) 필요.
   배포 후 8/3 17:10 검증 잡 결과로 승격 게이트 통과 여부 재확인 (이월 15·20번).
2. 블로그 10편 발행 — 원고 `docs/blog_series_backtest/`, 붙여넣기 검사 통과 상태.
3. ~~SCR-21 배치 모니터 마무리~~ → **완료·커밋됨** (`1ec7819`).
4. `google-services.json` 커밋 여부 사용자 결정.
5. 2015~ OHLCV 백필 (배포 후 운영 절차) — 연 단위 청크로
   `POST /api/v1/scheduler/backfill/ohlcv?start=2015-01-01&end=2015-12-31` → … →
   기존 `data_start`(운영 2016-01-04) 전일까지. 실패는 `job_run_log`에 남음.

### 이월 (번호 유지 · 20·21번 신규)

5. 워치리스트·보유 화면 브라우저 CSS 확인 (네비 1줄, 카드 2열)
6. 픽 만료 가드 로그 — ARMED 픽 0건이라 아직 안 뜸
7. ~~KIS 잔고·주문 페이지네이션~~ → `86d5b7e` 해소, **8/3 운영 확인만 남음** (위 절 참고)
8. 🟡 부분체결이 만료 처리된다 (`expire_pending_orders`)
9. 🟡 매매일지 페어링이 티커 단위 (`trade_review.py:119`)
10. 🔵 후보 퍼널 재설계 + AI 스코어링 — 계획서 `docs/plans/candidate-funnel-ai-scoring.md`, 착수 전
11. 🟡 후보 생성 누락일 2건 (7/01, 7/17) 잡 실패 로그 확인
12. 🟡 분석 워치리스트 누적 2건뿐 — 게이트 전량 탈락 중
13. `analysis_pick` id=1 CLOSED인데 exit_reason 빈 것
14. 📌 모의계좌 6/01 9,977만 → 7/30 8,513만 (-14.7%), 버그 수정으로 앞으로는 기록됨
15. ~2026-09-01 `mock_months ≥ 3` 충족 예정. 점수 28.6~48.0 < 60이라 승격 차단 —
    **단 8/2 점검 결과, 점수 병목의 상당 부분이 20번 Sharpe 왜곡 탓일 가능성. 20번 먼저.**
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
    **잔여 후속**: 과거 검증 결과(walk_forward_results 등)는 구 공식 산출값 — 다음 17:10
    검증 잡부터 새 공식 적용되므로 8/3 결과로 승격 게이트 통과 여부 재확인 필요 (이월 15번 연동).
21. ~~🔴 로컬 `maps.db` alembic 스탬프 깨짐~~ → **해소** — `0014_regime_korea_weak_guard`로
    stamp 교정, 현재 head `0017_bt_run_period_verdict` 적용 상태.

## 핵심 파일 맵 (이번 점검에서 본 곳, 변경 없음)

- `maps/backtest/engine.py` — `BacktestEngine.run`, `_compute_metrics`(Sharpe·rf 3%), 사이징 상수.
- `maps/api/backtest.py` — 콘솔 API (`GET /api/v1/backtest`, `POST /run`), `RUNNABLE_STRATEGIES` 7종,
  `float()`/`int()` 강제변환(8db50a9), `BacktestRunLog` INSERT.
- `maps/ops/scheduler.py` — `run_validation` → `_generate_validation_metrics` → WFA/Plateau/MC 저장.
- `maps/validation/walk_forward.py` — 2016봉 요구, 통과 3조건 (sharpe_mean>0 AND 음수폴드≤1 AND G2P≥0.6).
- 응답 스키마 필드는 `recent_runs` (`api/schemas.py:305`) — `runs` 아님.
- **운영 접속**(집 PC): `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`.
