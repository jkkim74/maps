# scripts/

단발성 운영·연구 도구 모음. 앱 런타임은 이 디렉터리를 import 하지 않는다.

## 운영 백필·정정 (DB를 쓴다 — 실행 전에 대상 확인)

| 스크립트 | 용도 |
|---|---|
| `backfill_score_feeds.py` | 수급·피드 백필. 점수 커버리지가 비었을 때 (`--calendar-days N`) |
| `backfill_fundamentals_naver.py` | Naver 소스로 펀더멘털 백필 |
| `backfill_entry_atr.py` | 과거 주문 행의 진입 ATR 보정 |
| `normalize_pick_tick_prices.py` | 픽 가격을 KRX 호가 단위로 정규화 |
| `load_analysis_picks.py` | 분석 픽 적재 |

## 진단·검증

| 스크립트 | 용도 |
|---|---|
| `diag_kis_balance.py` | KIS 잔고·미체결 직접 조회 (동기화 불일치 추적) |
| `export_strategy_stages.py` | `promotion_history` 최신 통과 단계를 JSON 으로 — `/analyze` 프롬프트 주입용 |
| `verify_blog_numbers.py` | 블로그 원고 숫자를 다이제스트와 대조 |
| `check_naver_format.py` | 네이버 평문 규칙 + 용어 가독성 검사 (warning-only) |
| `redact_stream_secrets.py` | Claude stream-json 에서 비밀값 제거 — **`tee` 앞단에 둔다** |

## 연구 평가 (운영 데이터를 바꾸지 않는다)

| 스크립트 | 용도 |
|---|---|
| `evaluate_pullback_v3_3.py` | 청산 조합 × 강세 3구간 감사 평가 |
| `evaluate_ai_scoring_models.py` | AI 스코어링 모델 비교 |
| `regime_conditional_performance.py` | 장세 조건부 성과 |
| `run_kostolany_backtest.py` | 코스톨라니 백테스트 실행 |
| `portfolio_replay_run.py` | 포트폴리오 리플레이 실행 |
| `build_stock_analysis_ui_ppt.py` | 화면설계서 PPT 생성 (`pillow`, `python-pptx`) |

## 운영 자동화·인프라

| 스크립트 | 용도 |
|---|---|
| `run_analyze_cron.sh` | 매 거래일 16:00 `/analyze` (45분 상한, `flock`) |
| `run_blog_cron.sh` | 블로그 생성 cron |
| `analyze_stream_to_log.py` | analyze 스트림을 진행 로그로 |
| `setup_telegram_webhook.py` | 텔레그램 웹훅 등록. `--info` 로 `ip_address` 확인 |
| `backup_postgres.ps1` / `restore_postgres.ps1` | PostgreSQL custom-format 백업·복구 |

## 주의

> 🔴 **16:00~16:45 KST 는 analyze 실행 창이다.** 이 시간에 배포하거나 작업 트리를 바꾸면
> 진행 중인 회차가 깨진다. 확인: `flock -n /tmp/maps_analyze.lock true && echo 미실행`
>
> ⚠️ 백필·정정 스크립트는 **운영 DB 를 직접 쓴다.** 실행 전 대상 날짜·건수를 먼저 출력해
> 확인하고, 대규모 변경 앞에는 `backup_postgres.ps1` 로 백업한다.
>
> ⚠️ 예외 메시지에 연결 문자열이 섞여 로그에 남은 적이 있다(2026-08-11). 자격증명이 찍힐
> 수 있는 출력은 `redact_stream_secrets.py` 를 거치게 한다.
