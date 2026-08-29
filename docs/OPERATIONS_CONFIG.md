# MAPS 운영 설정 입력 가이드

실제 운영에 필요한 외부 연계 정보는 모두 `.env`에 입력합니다. 코드에서는 `maps/common/settings.py`가 이 값을 한 번에 읽고, `/ops-config` 화면과 `/api/v1/ops/config` API가 누락 여부를 마스킹해서 보여줍니다.

## 1. 입력 위치

| 목적 | 파일/화면 | 설명 |
|---|---|---|
| 실제 계좌/API 키 입력 | `.env` | 로컬 비밀 설정 파일입니다. Git에 커밋하지 않습니다. |
| 입력 양식 확인 | `.env.example` | 필요한 변수명과 예시가 모두 들어 있습니다. |
| 설정 상태 확인 | `/ops-config` | 값이 입력됐는지, 선택한 브로커 기준 필수값이 빠졌는지 확인합니다. |
| 프로그램 코드 설정 진입점 | `maps/common/settings.py` | 새 외부 연계값을 추가할 때 이 파일에 필드를 추가합니다. |

## 2. 최소 운영 입력 순서

1. `.env.example`을 복사해서 `.env`를 만듭니다.
2. `MAPS_BROKER_MODE`를 정합니다. 실주문 전까지는 `mock`을 유지합니다.
3. KIS를 쓸 경우 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`를 입력합니다.
4. 실전 API를 쓸 경우에도 처음에는 `MAPS_LIVE_TRADING_ENABLED=false`, `KIS_REAL_TRADING=false`로 둡니다.
5. `/ops-config`에서 누락된 필수값과 경고를 확인합니다.
6. 모의/Mock 검증이 끝난 뒤에만 `MAPS_LIVE_TRADING_ENABLED=true`와 브로커 실전 옵션을 켭니다.

## 3. 주요 환경변수

| 변수 | 예시 | 필수 조건 | 설명 |
|---|---:|---|---|
| `MAPS_DB_URL` | `sqlite:///./maps.db` | 항상 | 운영 DB 연결 문자열입니다. |
| `MAPS_BROKER_MODE` | `mock` | 항상 | `mock`, `kis`, `kiwoom` 중 하나입니다. |
| `MAPS_LIVE_TRADING_ENABLED` | `false` | 항상 | 실주문 안전 스위치입니다. |
| `MAPS_DATA_PROVIDER` | `pykrx` | 항상 | 시장 데이터 공급자입니다. |
| `KIS_APP_KEY` | 빈칸 | KIS 사용 시 | KIS Open API 앱 키입니다. |
| `KIS_APP_SECRET` | 빈칸 | KIS 사용 시 | KIS Open API 시크릿입니다. |
| `KIS_ACCOUNT_NO` | `12345678-01` | KIS 사용 시 | 계좌번호 8자리와 상품코드 2자리입니다. |
| `KIS_REAL_TRADING` | `false` | 선택 | KIS 실전 서버 사용 여부입니다. |
| `KIWOOM_ACCOUNT_NO` | 빈칸 | Kiwoom 사용 시 | 키움 계좌번호입니다. |
| `KIWOOM_PASSWORD` | 빈칸 | Kiwoom 사용 시 | 키움 계좌 비밀번호입니다. |
| `DART_API_KEY` | 빈칸 | 선택 | 관리종목/상장폐지/공시 보완 데이터용입니다. |
| `DAILY_LOSS_LIMIT` | `0.015` | 항상 | 일 손실 한도입니다. |
| `MAX_SINGLE_EXPOSURE` | `0.10` | 항상 | 단일 종목 최대 노출입니다. |
| `ACCOUNT_RISK_PER_TRADE` | `0.005` | 항상 | 주문당 위험 예산입니다. |
| `SLACK_WEBHOOK_URL` | 빈칸 | 선택 | 알림 연동용 웹훅입니다. |

## 4. 변경이 필요할 때

계좌번호, API 키, 웹훅, 위험 한도는 `.env`만 수정하면 됩니다. 서버 프로세스는 설정을 시작 시점에 읽으므로, 값을 바꾼 뒤에는 서버를 재시작하세요.

새로운 외부 서비스가 추가되면 다음 순서로 반영합니다.

1. `maps/common/settings.py`에 새 필드를 추가합니다.
2. `.env.example`에 입력 예시를 추가합니다.
3. `get_config_status()`에 표시 섹션을 추가합니다.
4. 필요한 어댑터나 API에서 `get_settings()`를 통해 값을 읽습니다.

## 5. 안전 원칙

- `.env`는 `.gitignore` 대상입니다. 실제 키를 문서, 테스트, 코드에 직접 쓰지 않습니다.
- `MAPS_BROKER_MODE=mock`이면 실제 주문 어댑터를 만들지 않습니다.
- 실전 전환은 `MAPS_BROKER_MODE=kis` 같은 브로커 선택과 `MAPS_LIVE_TRADING_ENABLED=true`를 모두 의식적으로 바꾸는 방식으로 처리합니다.
- `/ops-config`는 값의 존재 여부와 마스킹된 일부만 보여주며, 전체 키/시크릿은 노출하지 않습니다.

## 6. 후보 AI 스코어링

후보 AI 스코어링은 기본적으로 꺼져 있으며 `off → rerank → replace` 순서로만 단계적으로
활성화합니다. 모드 전환은 `.env`의 명시적 변경과 프로세스 재시작이 필요합니다.

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `MAPS_AI_SCORING_MODE` | `off` | `off`, `rerank`, `replace` 중 하나입니다. |
| `MAPS_AI_DAILY_CALL_LIMIT` | `5` | 전체 전략을 합친 하루 고유 ticker 호출 상한입니다. 시작·성공·실패 호출을 모두 셉니다. |
| `MAPS_AI_RERANK_WEIGHT` | `0.20` | rerank 추천점수에 반영할 AI 비중입니다. 후보 최소점수는 계속 규칙점수를 사용합니다. |
| `MAPS_AI_SCORING_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | 후보 점수 전용 Bedrock 모델입니다. |
| `MAPS_AI_REQUEST_TIMEOUT_SECONDS` | `60` | 개별 Bedrock 요청 제한시간입니다. 자동 재시도는 하지 않습니다. |

비용을 우선할 경우 모델을 `us.anthropic.claude-haiku-4-5-20251001-v1:0`으로 바꿀 수
있습니다. 동일 거래일·ticker·입력·모델·프롬프트 결과는 DB에서 재사용하며, 실패 결과도
같은 날 재호출하지 않습니다. 프롬프트 캐싱, 배치 추론, 자동 네트워크 재시도는 사용하지
않습니다. 후보 생성 로그의 `ai_calls`, `ai_cache_hits`, `ai_input_tokens`,
`ai_output_tokens`를 비용·실패율 관측의 정본으로 사용합니다.

`replace`는 한도 안에 든 종목만 주문 후보가 됩니다. AI 오류는 규칙점수로 복구하지만,
AI가 시장 국면, 진입 신호, 유동성, 신선도 또는 주문 안전 조건을 우회하지는 않습니다.
매수가·손절가·목표가는 모든 모드에서 기존 규칙 기반 계획을 유지합니다.

## 7. 보유 장세 오버레이

보유 장세 오버레이 v1은 읽기 전용 shadow 기능입니다. 기존 손절·익절·전략 청산을 변경하지
않고 자동후보 보유분을 `HOLD`, `WATCH`, `EXIT` 후보로 분류해 감사 테이블과 일일
다이제스트에만 기록합니다.

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `MAPS_HOLDING_REGIME_OVERLAY_MODE` | `shadow` | `off` 또는 `shadow`만 허용합니다. `enforce`와 실제 매도 기능은 없습니다. |
| `MAPS_HOLDING_REGIME_MAX_AGE_DAYS` | `3` | 현재 장세의 최대 나이이자 두 장마감 관측 사이의 최대 달력 간격입니다. |

장세 근거는 `source=candidate_generation`인 장마감 관측만 사용합니다. `order_cycle` 행,
미체결·expired BUY, 수동·외부 주문, 진입 컨텍스트가 깨진 주문은 강제 판단에 쓰지 않습니다.
설정을 `off`로 바꾼 뒤 프로세스를 재시작하면 새 감사 기록만 중단되며 기존 청산은 계속됩니다.

## 상한가 당일매매 V1 (limit-up intraday)

장중 실시간 상한가 단기매매 엔진입니다. 일봉 파이프라인과 **별개 경로**이며 KIS 실시간
웹소켓을 씁니다. 계약 상세는 [maps/limit_up/CLAUDE.md](../maps/limit_up/CLAUDE.md).

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `MAPS_LIMIT_UP_ENABLED` | `false` | 엔진 기동 스위치. `false`면 배선돼 있어도 스캔·웹소켓이 뜨지 않습니다. |
| `MAPS_LIMIT_UP_MODE` | `recommend_only` | `off` / `recommend_only` / `automatic`. **`automatic` 만 실제 주문을 냅니다.** |
| `MAPS_LIMIT_UP_MIN_TURNOVER_KRW` | `50000000000` | 유동성 하한. 500억 미만은 설정 검증에서 거부됩니다. |
| `MAPS_LIMIT_UP_AFTER_HOURS_DROP_PCT` | `0.02` | 시간외 붕괴 판정 기준(`0 < x ≤ 0.02`). |
| `MAPS_LIMIT_UP_HEALTHCHECKS_PING_URL` | (빈값) | 데드맨 핑 URL. 비우면 no-op. |

**기동 조건은 두 개가 모두 참이어야 합니다** — `MAPS_LIMIT_UP_ENABLED=true` 이고
`MAPS_BROKER_MODE=kis`. 후자가 아니면 기동을 거부하고 ERROR 로그를 남깁니다. 실시간 시세가
없는 브로커로 띄우면 트리거가 영영 걸리지 않는 좀비 엔진이 되기 때문입니다.

손실 한도(일일 −30만원, 비상 절대상한 −100만원, 익일 하한가 30%)는 **설정이 아니라
코드 상수**입니다. 설정으로 열면 위험 한도가 조용히 느슨해집니다.

모드는 `PUT /api/v1/limit-up/settings` 로 재시작 없이 바꿀 수 있지만 **영속되지 않습니다** —
재시작하면 `.env` 값으로 돌아갑니다. 실수로 켠 `automatic` 이 재부팅으로 부활하지 않게 하는
의도된 동작이며, 영구 전환은 `.env` 를 고쳐야 합니다.
`POST /api/v1/limit-up/emergency-off` 는 즉시 신규 진입을 막고, 이때도 청산 경로는 열려 있습니다.

> 🟡 **`automatic` 전환 전 미완 3건**: ① 시간외 `ORD_DVSN=21` 실검증 ② 시간외 미체결 주문의
> 회차 이월 확인 ③ 상한가 V1 전용 승격 기준(기존 WFA·MC·Plateau를 그대로 태울 수 없음).
