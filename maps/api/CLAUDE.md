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
├── limit_up.py            # 상한가 V1 상태 조회·비상정지·설정 (관리자)
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
├── users.py               # 회원 계정 · 개인 설정 · 관리자 회원 관리
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

## 인증과 권한 — 강제 지점은 `auth.py` 한 곳

`auth.py` 의 `auth_gate_middleware` 가 **모든 요청**의 인증과 역할을 판정한다.
라우터에 권한 `Depends` 를 뿌리지 않는다. `MAPS_AUTH_ENABLED=true` 인 운영에서만 켜지며,
테스트는 `tests/conftest.py` 의 autouse fixture 로 항상 꺼진다(꺼지면 관리자로 동작).

| 이름 | 설명 |
|---|---|
| `authenticate(db, username, password)` | `app_user` 계정 검증. 비활성 계정은 실패 |
| `load_user(db, username)` | 활성 계정 조회 — 매 요청 역할을 DB에서 다시 읽는다 |
| `current_identity(request)` | `request.state.user` → `Identity(id, username, role)` |
| `is_allowed(role, path, method)` | **허용 목록** 판정 |
| `ensure_bootstrap_admin()` | 계정이 0개일 때만 `.env` 자격증명으로 관리자 시드 |

> 🔴 **`_USER_ALLOWED` 에 없는 경로는 전부 관리자 전용이다(fail-closed).** 새 라우터를
> 추가하면 기본적으로 닫혀 있고, 일반 사용자에게 열려면 명시적으로 등록해야 한다.
> 목록은 GET 만 여는 식으로 쓰며, 이는 **상태를 바꾸는 동작이 모두 POST/PUT/DELETE**
> 라는 가정에 기댄다 — 조회용 POST 엔드포인트를 만들면 그 가정이 깨진다.
>
> ⚠️ 역할·상태를 세션이나 토큰에 굽지 않는다. 매 요청 DB를 보므로 계정을 비활성화하면
> **이미 로그인한 세션과 발급된 모바일 토큰도 즉시** 막힌다.

## users.py — 계정과 개인 설정

| 메서드 | 경로 | 권한 |
|---|---|---|
| `GET` | `/me` | 본인 — 계정 + 해석된 설정 + 오늘 사용량 |
| `PUT` | `/me/preferences` | 본인 — `UserPreferences` 로 검증 후 저장 |
| `POST` | `/me/password` | 본인 — 현재 비밀번호 확인 필요 |
| `GET`/`POST` | `` | 관리자 — 목록·계정 생성 |
| `PUT` | `/{id}` | 관리자 — 역할·상태·요금제·한도 |
| `POST` | `/{id}/reset-password` | 관리자 — 임시 비밀번호 1회 반환 |

> ⚠️ 응답에 `password_hash` 를 절대 싣지 않는다(`UserSummary` 로 차단).
> 마지막 활성 관리자는 강등·비활성화할 수 없다 — 하면 아무도 운영 화면에 못 들어간다.

## 알아 둘 라우터

| 파일 | 비고 |
|---|---|
| `stock_analysis.py` | 분석은 **SSE 스트리밍**. 완료 시 이력을 정확히 한 번 저장하고, 저장만 실패하면 `history_error` 를 함께 내려보낸다. 일반 사용자는 **자기 이력만** 보이고 일일 한도(429)가 적용된다 |
| `analysis_picks.py` | 안전한도 → preview → `arm-plan`. **최종 arm 에서 잔고·게이트·중복을 다시 검증한다**. 목록은 소유자로 걸러진다(`owner_user_id IS NULL` = 운영자 픽) |
| `mobile.py` | 운영자 계좌·포지션을 반환하므로 **관리자 전용**이다. 일반 사용자 로그인은 403 |
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
