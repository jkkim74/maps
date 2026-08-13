# 운영 설정 편집·변경 이력 설계 (OPS-02 · OPS-03)

## 1. 목표

운영 설정을 화면에서 안전하게 바꾼다. 지금 `/ops-config` 는 59개 항목을 보여 주지만
바꿀 수 있는 것은 `MAPS_AI_SCORING_MODE` **하나뿐**이고, 나머지는 SSH 로 서버에 들어가
`.env` 를 직접 고쳐야 한다. 손으로 고치면 오타가 다음 기동을 깨뜨리고, 누가 언제 무엇을
바꿨는지 아무 기록도 남지 않는다.

화면설계서 `docs/ui-design/maps-auth-screen-design.html` 의 OPS-02·OPS-03 절을 구현한다.

## 2. 범위와 비범위

### 범위

- `get_config_status()` 항목의 값 변경 — **64개** (기존 59 + 아래 5종 신규 노출)
- 파이프라인 스케줄 시각 5종을 `get_config_status()` 에 노출:
  `MAPS_DATA_COLLECTION_TIME`, `MAPS_CANDIDATE_TIME`, `MAPS_VALIDATION_TIME`,
  `MAPS_ORDER_TIME`, `MAPS_EOD_TIME`. 설정 자체는 `settings.py:55-59` 에 이미 있으나
  조회 목록에서 빠져 있었다. **운영자가 가장 자주 조정하는 값이라 이걸 빼면 SSH 를 계속
  써야 하고, 이 작업의 목적이 절반만 달성된다.**
- 타입별 입력 위젯과 검증 (불리언·열거형·숫자·시각·문자열)
- 위험 항목의 확인 문구 요구
- 비밀 항목의 마스킹 유지와 감사 로그 값 미기록
- 변경 이력 저장·조회 (OPS-03)
- 재시작이 필요한 항목의 배지 표시

### 비범위

- 새 설정 키 생성. **"추가" 는 정의돼 있으나 비어 있는 항목에 값을 넣는 것만 뜻한다**
- 스케줄 잡 자동 재등록 — 배지로 알리고 재시작은 운영자가 한다
- 설정 변경 롤백·되돌리기 버튼
- 사용자별 설정 (개인 설정은 별도 스펙)
- `.env` 파일 자체의 형식 변경(주석·따옴표 처리)

## 3. 선택한 접근

### 채택: 편집 메타데이터를 pydantic 에서 파생한다

`MapsSettings.model_fields[attr]` 에 이미 있는 것을 그대로 쓴다.

| 필요한 것 | 출처 | 확인 |
|---|---|---|
| 타입 | `field.annotation` | `bool`/`int`/`float`/`str` |
| 선택지 | `typing.get_args(annotation)` | `Literal['off','rerank','replace']` → 3개 |
| 범위 | `field.metadata` | `[Ge(ge=0), Le(le=60)]` |
| 비밀 여부 | `_field(..., secret=True)` | **이미 선언돼 있고 지금은 버려진다** — 15개 |

손으로 유지하는 것은 **두 개의 짧은 집합**뿐이다. 59행짜리 메타데이터 표를 만들지 않는다.
표를 만들면 설정이 늘 때마다 두 곳을 고쳐야 하고 조용히 어긋난다 — 루트 `CLAUDE.md` 가
전략 카탈로그에서 이미 경고하는 함정과 같다.

### 기각: 명시 메타데이터 표 59행

명시적이고 pydantic 내부 구조에 안 묶이지만, 새 설정을 추가할 때 갱신을 빠뜨리면
화면에서 편집이 조용히 사라진다. 이 저장소는 이미 "값은 코드에서 읽어 온다" 를 규약으로 삼는다.

### 채택: `.env` 쓰기 + 캐시 객체 갱신, 재시작 필요 항목만 배지

`get_settings()` 는 `lru_cache` 싱글턴이고 `self._settings = get_settings()` 는 **같은 객체를
참조**하므로, 캐시 객체의 속성을 바꾸면 호출 시점에 읽는 모든 곳에 즉시 반영된다.
기존 `set_ai_scoring_mode` 가 이미 이 방식으로 동작한다.

예외는 **값이 구워진 곳**이다.

| 항목 | 이유 |
|---|---|
| 시각 6종 — `MAPS_DATA_COLLECTION_TIME` · `MAPS_CANDIDATE_TIME` · `MAPS_VALIDATION_TIME` · `MAPS_ORDER_TIME` · `MAPS_EOD_TIME` · `MAPS_STOCK_REPORT_TIME` | `CronTrigger` 에 구워진다 (`ops/scheduler.py:_register_jobs`, 3436~3479) |
| `MAPS_SCHEDULER_TIMEZONE` | 스케줄러 생성 시점 |
| `MAPS_DB_URL` | 엔진이 이미 만들어져 있다 |
| `MAPS_LOG_DIR` | 기동 시 핸들러 등록 |

**총 9개.** `MAPS_LOG_MAX_BYTES`·`MAPS_LOG_BACKUP_COUNT` 는 `get_config_status()` 에 없어
편집 대상이 아니므로 이 목록에도 넣지 않는다(실측 확인).

### 기각: 스케줄 시각 변경 시 자동 재등록

재시작이 필요 없어지지만 실행 중인 잡·`/etc/cron.d/maps-analyze` 와의 경합을 새로 다뤄야
한다. 배지 한 줄로 얻는 것과 비교해 값이 안 맞는다.

## 4. API

```
GET  /api/v1/ops/config              기존 + 필드별 편집 메타데이터
PUT  /api/v1/ops/config/{env_var}    값 변경
GET  /api/v1/ops/config/history      변경 이력
```

`OpsConfigField` 에 추가하는 필드: `type`, `choices`, `min`, `max`, `secret`,
`dangerous`, `requires_restart`.

### 허용목록은 `get_config_status()` 자체다

편집 대상은 그 응답에 실린 `env_var` 집합이다. 거기 없는 이름은 400 으로 거절한다.
**별도 허용목록을 만들지 않으므로 두 곳이 어긋날 수 없다.**

### `POST /ai-scoring-mode` 는 삭제한다

일반 엔드포인트가 같은 일을 하므로 남겨 두면 `.env` 에 쓰는 경로가 둘이 되고 그중 하나만
감사 로그를 남기는 구멍이 생긴다. 호출부는 `static/js/` 한 곳이다.

### 쓰기 순서

`.env` 먼저 쓰고, 성공하면 캐시 객체를 갱신하고, 그다음 감사 로그를 남긴다.
파일 쓰기가 실패하면 메모리도 안 바뀌어 상태가 갈라지지 않는다.
`_set_env_value()` 가 이미 원자적이다(temp 파일 + `os.replace`).

## 5. 데이터

신규 테이블 `ops_config_log`, migration **`0025_ops_config_log`**.

| 컬럼 | 비고 |
|---|---|
| `id` | PK |
| `env_var` | 변경된 설정 이름 |
| `old_value` | 변경 전 값 |
| `new_value` | 변경 후 값 |
| `changed_by` | `Identity.username` |
| `created_at` | UTC naive (기존 테이블과 동일 규약) |

> 🔴 **비밀 항목 15개는 값을 저장하지 않는다.** `old_value`·`new_value` 에 `***` 만 남긴다.
> 감사 로그는 "누가 언제 무엇을 건드렸다" 를 남기는 것이지 자격증명 사본을 만드는 곳이 아니다.

## 6. 검증과 안전장치

### 타입 검증은 pydantic 에 위임한다

필드별 `TypeAdapter(annotation)` 로 변환·검증한다. 범위 위반·잘못된 열거값은 pydantic 이
거절하므로 검증 코드를 새로 쓰지 않는다.

**예외: 시각 6종은 순수 `str` 이라 pydantic 이 `HH:MM` 을 검사하지 않는다.**
정규식 한 줄을 명시 규칙으로 둔다. 마침 재시작 필요 집합과 같은 항목들이다.

### 위험 항목은 확인값을 요구한다

`MAPS_LIVE_TRADING_ENABLED`, `KIS_REAL_TRADING`, `MAPS_BROKER_MODE`,
`MAPS_SCHEDULER_ENABLED`, `MAPS_STRATEGY_TRADE_ENABLED`, `MAPS_DB_URL` — **6개**.

화면설계서는 5개로 적었지만 `MAPS_DB_URL` 을 더한다. 잘못 넣으면 다음 기동에서 앱이
아예 안 뜨고, 화면으로는 복구할 수 없어 SSH 로만 되돌릴 수 있다. 위험 등급이 실주문
스위치보다 낮지 않다.

요청 body 의 `confirm` 이 **`env_var` 이름과 정확히 같아야** 통과한다.
화면설계서 목업은 `LIVE` 같은 항목별 문구를 쓰지만, 이름으로 통일하면 항목별 문구표가
필요 없고 안전성은 같다. **설계서와 다른 점이므로 구현 시 설계서도 함께 고친다.**

### 비밀 항목

`GET` 응답은 지금처럼 `mask_config_value(secret=True)` 로 마스킹한다. 새 값을 `PUT` 으로
넣을 수는 있지만 응답에 되돌려주지 않는다 — **재열람 불가**.

### 권한

추가 작업 없음. `_USER_ALLOWED` 에 `/api/v1/ops` 가 없어 이미 관리자 전용(fail-closed)이다.

## 7. 오류 처리

| 상황 | 응답 |
|---|---|
| 허용목록에 없는 `env_var` | 400 |
| 타입·범위·열거값 위반 | 400 + pydantic 메시지 |
| 시각 형식 위반 | 400 |
| 위험 항목인데 `confirm` 없음/불일치 | 400 |
| `.env` 쓰기 실패 | 500. 캐시 미변경, 감사 로그 미기록 |
| 감사 로그 쓰기 실패 | 값 변경은 이미 반영됨. 500 대신 **200 + `audit_error`** 로 알린다 |

> 감사 로그 실패로 500 을 내면 "실패했다" 고 읽고 다시 눌러 같은 값을 두 번 쓰게 된다.
> `api/stock_analysis.py` 가 이력 저장 실패를 `history_error` 로 전달하는 기존 패턴을 따른다.

## 8. 테스트

`tests/test_ops_config_edit.py`

1. 허용목록 밖 `env_var` → 400
2. 열거형에 정의되지 않은 값 → 400
3. 숫자 범위 위반 → 400 (`maps_analysis_pick_max_age_trading_days` 에 61)
4. 시각 형식 위반 → 400
5. 위험 항목에 `confirm` 없음 → 400, 이름 일치 → 200
6. 비밀 항목 변경 시 `ops_config_log` 에 `***` 만 남고 실제 값이 없다
7. 성공 시 `.env` 파일과 `get_settings()` 캐시가 함께 바뀐다
8. `.env` 쓰기 실패 시 캐시가 안 바뀐다
9. `GET /history` 가 최신순으로 반환한다
10. 편집 메타데이터가 pydantic 에서 바르게 파생된다 (`Literal` 3종·`Ge/Le`·`bool`)
11. 스케줄 시각 5종이 `get_config_status()` 에 노출된다 (신규 항목 회귀 방지)

`tests/test_migrations.py` — 빈 SQLite 에서 `0001 → 0025` 전체 적용

`tests/test_auth_screen_design_doc.py` — 항목 수가 59 → 64 로 바뀌므로 이 테스트와
화면설계서 `docs/ui-design/maps-auth-screen-design.html` 의 OPS 절을 함께 갱신한다.
확인 문구 방식(`LIVE` → `env_var` 이름)과 위험 항목 5 → 6 도 같은 커밋에서 고친다.

## 9. 배포

마이그레이션 `0025_ops_config_log` 가 있으므로 **운영 PostgreSQL custom-format 백업 후
`alembic upgrade head` 를 반드시 포함**한다. 16:00~16:45 KST 배포 금지.
