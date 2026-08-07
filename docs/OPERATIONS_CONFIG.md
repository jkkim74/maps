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
