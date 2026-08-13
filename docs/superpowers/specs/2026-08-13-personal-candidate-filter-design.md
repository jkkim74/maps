# 개인 후보 필터 연결 · 미구현 알림 설정 제거 설계

## 1. 목표

`/settings` 에 저장은 되지만 아무 효과가 없는 개인 설정 필드를 없앤다. 켜 두면 동작한다고
믿게 만드는 스위치는 설정이 없는 것보다 나쁘다.

8/12 개인화 1차에서 `UserPreferences` 6개 필드를 만들었으나 실제로 동작하는 것은
`landing_screen` 하나뿐이다. 나머지 5개는 저장·조회만 되고 소비처가 `static/js/settings.js`
밖에 없다.

| 필드 | 현재 |
|---|---|
| `landing_screen` | 동작 — `api/auth.py:_landing_path`, `main.py` 리다이렉트 |
| `candidate_min_score` | **죽음** — 후보 필터링은 전역 `settings.maps_candidate_min_score` 를 쓴다 |
| `candidate_markets` | **죽음** |
| `notify_push` | **죽음** — 발송은 여전히 전역 대상 |
| `notify_telegram` | **죽음** |
| `telegram_chat_id` | **죽음** — 전역 chat id 만 쓰인다 |

후보 필터 2개는 조회 필터라서 싸게 연결된다. 알림 3개는 발송 경로를 사용자별로 갈라야 해서
이 작업의 범위를 넘는다. 그래서 **후보 2개는 연결하고 알림 3개는 삭제한다.**

## 2. 범위와 비범위

### 범위

- `UserPreferences` 에서 `notify_push`, `notify_telegram`, `telegram_chat_id` 삭제
- `/settings` 화면·스크립트에서 알림 입력 제거
- `GET /api/v1/candidates` 가 요청자의 `candidate_min_score`, `candidate_markets` 를 적용
- 후보 화면에 개인 필터 적용 배지와 해제 링크 표시

### 비범위

- 사용자별 알림 발송 분리 — HANDOFF 2차 후보 2번으로 남긴다
- 전역 `MAPS_CANDIDATE_MIN_SCORE` 의 의미 변경. **주문 게이트는 계속 전역값이 정본이다**
- 후보 생성·스코어링 파이프라인 변경
- 개인 필터를 `ops/order_preview.py`·`ops/scheduler.py` 에 적용하는 것

> 🔴 이 설계는 **조회 필터만** 다룬다. 개인 설정이 주문 대상을 바꾸면 사용자가 자기 화면에서
> 운영 계좌의 주문 범위를 좁히게 된다. 화면 필터와 주문 게이트는 끝까지 분리한다.

## 3. 선택한 접근

### 채택: 서버측 필터, 집계는 파이프라인 값 유지

`get_candidates()` 가 `Request` 를 받아 `current_identity` → `load_user` → `user_prefs.resolve()`
로 개인값을 읽고 SQL `WHERE` 에 두 조건을 더한다. **`.limit(200)` 보다 앞에서** 걸러야
상한에 잘린 뒤 필터링되는 문제가 생기지 않는다.

인증이 꺼진 환경(로컬·테스트)은 계정이 없으므로 필터를 적용하지 않는다.
`api/stock_analysis.py:_owner_scope` 와 같은 폴백 규칙이다.

`universe_count`·`final_count` 는 **파이프라인 통계지 화면 목록의 개수가 아니다.** 그대로 둔다.
대신 목록 위에 무엇이 걸려 있는지 배지로 보여 준다. 응답 스키마에 필드를 추가하지 않는다.

### 기각: 클라이언트측 필터

API 를 안 건드리는 대신 200행 상한이 필터보다 먼저 적용돼 결과가 달라진다.

### 기각: 집계도 필터 기준으로 재계산

`universe_count` 가 "유니버스 규모" 라는 본래 의미를 잃고, 운영자가 파이프라인 상태를
화면에서 못 보게 된다.

## 4. 데이터

**스키마 변경 없음. 마이그레이션 없음.**

`UserPreferences` 는 `extra="forbid"` 라서 삭제한 키가 저장돼 있으면 검증이 깨지고
`user_prefs.resolve()` 가 전체를 기본값으로 되돌린다(`landing_screen` 까지 함께 손실).
운영 실측 결과 `app_user` 2계정 모두 `preferences IS NULL` 이라 해당 데이터가 없다.
**정리 스크립트를 만들지 않는다.**

## 5. 오류 처리

| 상황 | 동작 |
|---|---|
| 인증 비활성(로컬·테스트) | 필터 미적용. 기존 동작 그대로 |
| 로그인했으나 계정 조회 실패 | 필터 미적용 — 조회 화면이므로 fail-safe 로 연다 |
| 저장된 설정이 손상됨 | `resolve()` 가 이미 기본값으로 되돌린다. 추가 처리 없음 |
| `candidate_markets` 가 빈 목록 | 전체 시장. 필터 조건을 붙이지 않는다 |

> 이 화면은 조회 전용이라 fail-safe(열림)가 맞다. 주문·무장 경로의 fail-closed 원칙과
> 혼동하지 않는다.

## 6. 테스트

`tests/test_candidates_api.py`

1. 개인 `candidate_min_score` 미만 후보가 목록에서 빠진다
2. `candidate_markets=["KOSPI"]` 면 코스닥 후보가 빠진다
3. 인증 비활성 환경에서는 전량 반환된다(기존 동작 보존)
4. 필터가 `.limit(200)` 앞에서 걸린다 — 201행 이상을 넣고 확인
5. `universe_count`·`final_count` 는 필터와 무관하게 파이프라인 값을 유지한다

`tests/test_users.py` — 삭제한 3개 키를 `PUT /me/preferences` 로 보내면 422 로 거절된다
(`extra="forbid"` 회귀 방지)

## 7. 배포

마이그레이션이 없으므로 `git pull` + `systemctl restart` 로 끝난다. DB 백업 불필요.
