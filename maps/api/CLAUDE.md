# api/

FastAPI REST 라우터 패키지. 화면별로 파일을 분리한다.

## Directory structure

```
api/
├── __init__.py            # 빈 패키지 마커
├── deps.py                # 공통 의존성 (get_db, DbDep)
├── schemas.py             # Pydantic 응답 모델 정의
├── auth.py                # 로그인/로그아웃 — 세션 쿠키 (prefix 없음)
├── analysis_picks.py      # SCR-19 분석 워치리스트 · 전략매매 무장
├── backtest.py            # SCR-07 백테스트 콘솔
├── batch_monitor.py       # SCR-21 배치 모니터
├── blog.py                # 생성된 일일 블로그 원고 조회
├── candidates.py          # SCR-04 후보 종목 풀
├── cost_sensitivity.py    # SCR-12 거래 비용 민감도
├── daily_digest.py        # 일일 다이제스트 (블로그 입력·검증 창구)
├── dashboard.py           # SCR-01 대시보드
├── data_quality.py        # SCR-14 유니버스 품질 로그
├── live_monitor.py        # SCR-13 계좌·포지션·주문 상태
├── market.py              # SCR-03 장세/팩터 분석
├── mobile.py              # 모바일 앱 축약 응답
├── ops_config.py          # 운영 설정 조회·변경
├── orders.py              # SCR-05 주문/체결
├── research.py            # SCR-10 전략 연구
├── risk.py                # SCR-06 Kill Switch
├── robustness.py          # SCR-08 Trend Robustness
├── scheduler.py           # 스케줄러 상태·수동 잡 실행
├── stock_analysis.py      # 종목 종합 분석 (SSE) · 분석 이력
├── stock_report.py        # Stock Report 실행·조회
├── strategies.py          # SCR-02 전략 관리·승격
├── telegram.py            # 텔레그램 봇 웹훅 (인라인 버튼 콜백)
├── trade_review.py        # SCR-17 거래 리뷰
├── trend_strength.py      # SCR-09 TrendStrength 모니터
└── wfa.py                 # SCR-11 Walk-Forward 리포트
```

## deps.py — 공통 의존성

```python
get_db() → Generator[Session, None, None]  # DB 세션 제너레이터
DbDep = Depends(get_db)                    # 라우터 함수 파라미터로 직접 사용
```

## schemas.py — Pydantic 응답 모델

모든 API 응답 스키마를 한 파일에서 관리한다. 라우터 파일에서 import해 사용.

## 라우터 등록

저장소 루트의 `main.py`(이 패키지 밖이다)에서 각 라우터를 `app.include_router()` 로 등록한다.
접두사와 태그는 각 라우터 파일의 `APIRouter(prefix=...)` 에 있다.

## prefix 지도

대부분 `/api/v1/<화면>` 이고 예외만 따로 기억하면 된다.

| 파일 | prefix |
|---|---|
| `auth.py` | **없음** — `/login`, `/logout` 이 루트에 붙는다 |
| `telegram.py` | `/api/telegram` (`/api/v1` 아님) |
| `ops_config.py` | `/api/v1/ops/config` |
| `scheduler.py` | `/api/v1/ops/scheduler` |
| 나머지 | `/api/v1/` + 파일명의 케밥케이스 (`stock_analysis.py` → `/api/v1/stock-analysis`) |

## 인증

`auth.py` 가 단일 공용 비밀번호 로그인을 처리하고, 세션 쿠키 게이트는 루트 `main.py` 에 있다.
`MAPS_AUTH_ENABLED=true` 인 운영에서만 켜지며, 테스트는 `tests/conftest.py` 의 autouse
fixture 로 항상 꺼진다.

## 알아 둘 라우터

| 파일 | 비고 |
|---|---|
| `stock_analysis.py` | 분석은 **SSE 스트리밍**. 완료 시 이력을 정확히 한 번 저장하고, 저장만 실패하면 `history_error` 를 함께 내려보낸다 |
| `analysis_picks.py` | 안전한도 → preview → `arm-plan`. **최종 arm 에서 잔고·게이트·중복을 다시 검증한다** |
| `telegram.py` | 웹훅 URL 은 텔레그램 서버에 저장된다. 도메인 변경 시 `scripts/setup_telegram_webhook.py` 재실행 필요 |
| `risk.py` | Kill Switch 발동·해제·청산 승인 |
| `scheduler.py` | 잡 수동 실행 — 운영에서 파이프라인을 돌리는 창구 |

> ⚠️ DB 의 UTC naive 시각을 그대로 내보내면 브라우저 KST 표시가 9시간 어긋난다.
> 응답 직렬화 시 명시적 UTC 로 보정한다.

## 코딩 규칙

- 모든 라우터 함수는 `db: Session = DbDep` 파라미터를 받는다.
- 비즈니스 로직은 라우터 밖의 도메인 패키지에 위치. 라우터는 호출만.
- Pydantic 응답 모델은 반드시 `schemas.py`에 정의.
- 설정은 `get_settings()`로 접근 (`os.getenv` 직접 사용 금지).

## 의존성

```
fastapi               → APIRouter, Depends, HTTPException
maps.common.db        → SessionLocal
maps.common.settings  → get_settings()
maps.common.models    → 각종 ORM 모델
maps.ops.scheduler    → get_operational_scheduler()
maps.risk.manager     → RiskManager
(각 도메인 패키지)
```
