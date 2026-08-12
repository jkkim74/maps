# MAPS index — 코드를 찾기 전에 여기부터

전수 `grep`/`glob` 은 이 색인에서 못 찾았을 때만 한다.
**읽기 순서: `index.md` → 해당 패키지 `CLAUDE.md` → 소스.**
문서와 코드가 다르면 **코드가 정본**이다. 그 자리에서 문서를 고친다
(`tests/test_docs_index.py` 가 트리 정합을 강제한다).

## 1. 무엇을 찾나 → 어디로

| 찾는 것 | 위치 |
|---|---|
| **손절가 계산** | `maps/strategy/live_rules.py` → `effective_stop_price()` (유일한 정본) |
| 포지션 수량 | `maps/common/sizing.py` → `risk_based_qty()` |
| 목표가·매수가 산출 | `maps/strategy/price_calculator.py`, AI 계획은 `maps/ai/trade_planner.py` |
| 호가 단위·거래일 | `maps/market/trading_rules.py` |
| **신규 매수·승격이 막힌 이유** | `maps/ops/score_readiness.py` (실측 커버리지 100% 게이트) |
| 픽 만료·신선도 | `maps/ops/pick_freshness.py` (`BOUGHT` 픽은 제외) |
| 장세(regime) 판정 | `maps/market/regime.py` + `regime_history.apply_hysteresis()` (최종값은 후자) |
| 시장폭·업종 | `maps/market/breadth.py`, `maps/market/sector_selector.py` |
| 뉴스 심리·수급 피드 | `maps/market/feeds.py` (실패를 중립 50으로 채우지 않는다) |
| **KRX 계정 잠금·pykrx 로그인** | `maps/data/krx_auth.py` → `ensure_krx_login_guard()` |
| OHLCV·펀더멘털·수급 수집 | `maps/data/collector.py`, `krx_adapter.py`, `fundamental_repo.py` |
| 후보 점수 공식 | `maps/strategy/scoring.py`, `score_features.py` |
| AI 스코어링 모드·한도 | `maps/ai/scoring_service.py` |
| 후보 주문 자격 SQL | `maps/ops/candidate_selection.py` |
| 주문 제출·브로커 | `maps/execution/order_manager.py`, `kis_adapter.py` |
| 중복 주문 방지 | `maps/ops/order_state.py` → `claimed_candidate_tickers()` |
| 전략매매 안전 최대금액 | `maps/ops/strategy_trade_plan.py` |
| 스케줄 잡·실행 시각 | `maps/ops/scheduler.py` + `maps/common/settings.py` |
| 승격 단계 판정 | `maps/promotion/gate.py`, 단계 스냅샷 `stage_snapshot.py` |
| Kill Switch | `maps/risk/manager.py`, 화면은 `maps/api/risk.py` |
| WFA·Plateau·MC | `maps/validation/` |
| 종목분석 이력·현재가 | `maps/stock_analysis/history.py` |
| 일일 다이제스트·블로그 | `maps/ops/daily_digest.py`, `maps/api/blog.py` |
| 텔레그램 버튼·푸시 | `maps/ops/notifications.py`, `maps/api/telegram.py` |
| DB 테이블 정의 | `maps/common/models.py` |
| 상수(MDD·그룹·승격 임계) | `maps/common/constants.py` |
| 환경변수 | `maps/common/settings.py` (`os.getenv` 직접 호출 금지) |
| **새 전략 추가** | `maps/strategy/CLAUDE.md` 의 체크리스트 6단계 |

## 2. 패키지 지도

| 패키지 | 한 줄 | 문서 |
|---|---|---|
| `maps/ai` | Bedrock 호출 — 점수·매매계획·안전마진. 실패 시 값을 만들지 않는다 | [maps/ai/CLAUDE.md](maps/ai/CLAUDE.md) |
| `maps/api` | 화면별 FastAPI 라우터 (`/api/v1/...`) | [maps/api/CLAUDE.md](maps/api/CLAUDE.md) |
| `maps/backtest` | 백테스트 엔진·비용 모델·포트폴리오 리플레이 | [maps/backtest/CLAUDE.md](maps/backtest/CLAUDE.md) |
| `maps/common` | 설정·DB·ORM 모델·예외·상수·사이징 | [maps/common/CLAUDE.md](maps/common/CLAUDE.md) |
| `maps/dashboard` | 전략 비교 대시보드 계산 | [maps/dashboard/CLAUDE.md](maps/dashboard/CLAUDE.md) |
| `maps/data` | KRX 수집·어댑터·레포지터리·로그인 회로차단기 | [maps/data/CLAUDE.md](maps/data/CLAUDE.md) |
| `maps/data_quality` | as-of-date 유니버스 생성 (미래 정보 금지) | [maps/data_quality/CLAUDE.md](maps/data_quality/CLAUDE.md) |
| `maps/execution` | 브로커 어댑터와 주문 관리 | [maps/execution/CLAUDE.md](maps/execution/CLAUDE.md) |
| `maps/indicator` | TrendStrength 0-100 점수와 S1~S5 버킷 | [maps/indicator/CLAUDE.md](maps/indicator/CLAUDE.md) |
| `maps/market` | 장세·시장폭·업종·실측 피드·KRX 규칙 | [maps/market/CLAUDE.md](maps/market/CLAUDE.md) |
| `maps/ops` | 스케줄러·알림·주문 상태·**게이트** | [maps/ops/CLAUDE.md](maps/ops/CLAUDE.md) |
| `maps/promotion` | 승격 게이트와 감사 로그 | [maps/promotion/CLAUDE.md](maps/promotion/CLAUDE.md) |
| `maps/risk` | Kill Switch·노출 한도 | [maps/risk/CLAUDE.md](maps/risk/CLAUDE.md) |
| `maps/stock_analysis` | 종목 종합 분석과 불변 이력 | [maps/stock_analysis/CLAUDE.md](maps/stock_analysis/CLAUDE.md) |
| `maps/stock_report` | 외부 stock-report 도구 연동 | [maps/stock_report/CLAUDE.md](maps/stock_report/CLAUDE.md) |
| `maps/strategy` | 전략 정의 + 손절·점수·가격 공용 규칙 | [maps/strategy/CLAUDE.md](maps/strategy/CLAUDE.md) |
| `maps/validation` | WFA·Plateau·Monte Carlo | [maps/validation/CLAUDE.md](maps/validation/CLAUDE.md) |

## 3. 코드 밖

| 경로 | 한 줄 | 문서 |
|---|---|---|
| `tests/` | pytest 스위트 — **`maps/tests/` 8개는 별도** | [tests/CLAUDE.md](tests/CLAUDE.md) |
| `scripts/` | 백필·진단·연구·cron 도구 | [scripts/CLAUDE.md](scripts/CLAUDE.md) |
| `alembic/` | 마이그레이션 (head `0023_score_readiness_feeds`) | [alembic/CLAUDE.md](alembic/CLAUDE.md) |
| `apps/mobile/` | Vite+Capacitor 모바일 앱 | [apps/mobile/CLAUDE.md](apps/mobile/CLAUDE.md) |
| `main.py` | FastAPI 앱 조립·라우터 등록·세션 게이트 (저장소 루트) | — |
| `templates/`, `static/` | Jinja2 대시보드 화면과 JS·CSS | — |
| `docs/` | 설계서·전략 가이드·운영 문서 | — |
| `config/` | 배포·서비스 설정 파일 | — |

## 4. 상황별 문서

| 알고 싶은 것 | 어디 |
|---|---|
| 최근 무슨 작업을 했나 | `HANDOFF.md` **최상단 절만** |
| 그 이전 이력 | `docs/handoff_archive/` |
| 배포·서버·인증·도메인 | 루트 `CLAUDE.md` "Production Server" |
| 승격 단계·MDD·가중치 기준 | 루트 `CLAUDE.md` + `maps/common/constants.py` |
| 점수 준비도 설정 | `docs/score-readiness-feeds.md` |
| 운영 설정값 | `docs/OPERATIONS_CONFIG.md` |
| 서버 런북 | `docs/AWS_LIGHTSAIL_RUNBOOK.md` |
| 전략 설명 원고 | `docs/strategy_guides/` |

## 5. 자주 밟는 지뢰

- 손절가를 `live_rules.effective_stop_price()` 밖에서 다시 계산하지 않는다 (사이징 포함)
- pykrx 를 부르기 전에 `ensure_krx_login_guard()`
- 결측 피드를 중립 50으로 채우지 않는다 — 게이트가 조용히 열린다
- `created_at` 은 UTC naive 다. 날짜 판정은 `ref_date`(KST) 로 한다
- 신규 전략은 `STRATEGY_GROUP_MAP` + `catalog.py` + `docs/strategy_guides/` 까지 등록
- 마이그레이션이 있으면 배포에 `alembic upgrade head` 를 포함한다
- **16:00~16:45 KST 배포 금지** — `/analyze` 파이프라인 실행 창
