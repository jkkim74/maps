# api/

FastAPI REST 라우터 패키지. 화면별로 파일을 분리한다.

## Directory structure

```
api/
├── __init__.py          # 빈 패키지 마커
├── deps.py              # 공통 의존성 (get_db, DbDep)
├── schemas.py           # Pydantic 응답 모델 정의
└── [라우터 파일 18개]
    ├── backtest.py      # 백테스트 실행 및 결과 조회
    ├── candidates.py    # 후보 종목 스냅샷 조회
    ├── cost_sensitivity.py  # 거래 비용 민감도 분석
    ├── dashboard.py     # 전략 비교 대시보드
    ├── data_quality.py  # 유니버스 품질 로그 조회
    ├── live_monitor.py  # 라이브 모니터링 (계좌·포지션·주문 상태)
    ├── market.py        # 시황 분석 결과
    ├── mobile.py        # 모바일 앱 전용 엔드포인트
    ├── ops_config.py    # 운영 설정 조회·변경
    ├── orders.py        # 주문 감사 로그 조회
    ├── research.py      # 전략 연구 화면 (백테스트 파라미터 탐색)
    ├── risk.py          # Kill Switch 관리
    ├── robustness.py    # Plateau / WFA / MC 검증 결과 조회
    ├── scheduler.py     # 스케줄러 상태 조회 및 수동 잡 트리거
    ├── stock_report.py  # Stock Report 조회 및 수동 생성
    ├── strategies.py    # 전략 목록 및 승격 단계 관리
    ├── trend_strength.py # 추세 강도 점수 조회
    └── wfa.py           # Walk-Forward Analysis 결과 조회
```

## deps.py — 공통 의존성

```python
get_db() → Generator[Session, None, None]  # DB 세션 제너레이터
DbDep = Depends(get_db)                    # 라우터 함수 파라미터로 직접 사용
```

## schemas.py — Pydantic 응답 모델

모든 API 응답 스키마를 한 파일에서 관리한다. 라우터 파일에서 import해 사용.

## 라우터 등록

`main.py`에서 각 라우터를 `app.include_router()`로 등록. 접두사(`/api/...`) 및 태그는 라우터 파일 내에서 정의.

## 주요 라우터 역할

| 파일 | 주요 엔드포인트 |
|---|---|
| `scheduler.py` | `POST /scheduler/run/{job_name}` — 잡 수동 실행; `GET /scheduler/status` |
| `risk.py` | `POST /risk/kill-switch/trigger`, `/release`, `/approve-liquidation` |
| `strategies.py` | `GET /strategies` 전략 목록; 승격 단계 변경 |
| `orders.py` | `GET /orders` 주문 로그 조회 |
| `live_monitor.py` | `GET /live/account`, `/positions`, `/open-orders` |
| `candidates.py` | `GET /candidates/{date}` 후보 스냅샷 |
| `market.py` | `GET /market/regime` 시황 분석 결과 |
| `stock_report.py` | `GET /stock-report/runs`, `POST /stock-report/generate` |
| `backtest.py` | `POST /backtest/run` 백테스트 실행 |
| `wfa.py` | `GET /wfa/results/{strategy_id}` |
| `robustness.py` | Plateau / MC 결과 조회 |
| `mobile.py` | 모바일 앱 전용 축약 응답 |
| `ops_config.py` | 운영 설정(슬리피지, 갭한도 등) 조회·변경 |

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
