# AI Scoring 설계

> 작성일: 2026-08-07
> 상태: 사용자 승인 완료, 구현 계획 작성 전
> 범위: 후보 퍼널 Phase 2 AI Scoring 재설계
> 우선순위: 추천 품질과 비용의 균형, 호출·토큰 상한의 강제, 규칙 기반 안전장치 보존

## 1. 배경과 목표

후보 퍼널 Phase 1은 전략별 진입 신호를 후보 생성 시점에 계산하고, 종목 컨텍스트를
전략 간 공유하도록 이미 개선되었다. Phase 2는 진입 가능성이 있는 소수 종목에만 AI
점수를 부여해 추천 순위를 보조하거나, 명시적 옵션에서 기존 추천점수를 대체한다.

이 기능의 목표는 다음과 같다.

- 전체 유니버스가 아니라 실제 매수 가능 후보에만 AI를 호출한다.
- 일일 호출 수와 입력·출력 토큰을 예측 가능한 상한 안에 둔다.
- AI가 시장 국면, 진입 신호, 주문 안전장치를 우회하지 못하게 한다.
- 규칙 점수와 AI 점수의 의미·출처를 화면과 API에서 구분한다.
- AI 호출 실패 또는 재실행 시 비용이 반복 발생하지 않게 한다.
- `rerank`로 관측한 뒤 검증된 경우에만 `replace`를 운영에서 선택할 수 있게 한다.

실제 투자 성과의 A/B 검증과 AI 기반 매수가·손절가·목표가 생성은 이번 범위에서
제외한다. 가격 계획은 기존 규칙 기반 계산을 유지한다.

## 2. 모드와 점수 의미

`MAPS_AI_SCORING_MODE`는 다음 세 값을 갖는다.

| 모드 | 후보 자격 | 최종 추천 순위 | AI 실패 |
|---|---|---|---|
| `off` | 기존 규칙 점수와 안전 조건 | `rule_score` | 해당 없음 |
| `rerank` | 기존 규칙 점수와 안전 조건 | 규칙·AI 가중 합산 | `rule_score` |
| `replace` | 기존 신호와 안전 조건 | `ai_score` | `rule_score` |

### 2.1 `off`

AI 클라이언트를 만들지 않고 기존 동작을 보존한다. 기본 모드다.

### 2.2 `rerank`

기존 규칙이 매수 가능 후보로 선정한 종목만 AI 평가 대상으로 삼는다. 추천점수는
다음 식으로 계산한다.

```text
recommendation_score =
    rule_score * (1 - ai_weight) + ai_score * ai_weight
```

기본 `ai_weight`는 `0.20`이다. 후보 자격, 최소점수, 주문 안전 조건은 반드시
`rule_score`로 판단한다. 따라서 낮은 AI 점수는 후보를 탈락시키지 않고 순서만 바꾼다.

### 2.3 `replace`

시장 국면, 전략 진입 신호, 유동성, 제외 사유와 주문 안전 조건은 그대로 적용한다.
이를 통과한 종목을 `rule_score` 순으로 일일 호출 한도까지 1차 압축한 후 AI를 호출한다.
이 1차 압축 집합만 최종 추천 대상이 된다. 정상 응답을 받은 종목은
`recommendation_score = ai_score`로 저장하고 AI 점수로 최종 순위를 정한다. 압축 집합
안에서 호출이 실패한 종목은 규칙 점수로 복구한다. 한도 밖 종목은 관측용 스냅샷에는
`RULE`과 `SKIPPED_LIMIT`으로 남길 수 있지만 `replace` 모드의 최종 추천·주문 대상에서는
제외한다. 이 구분으로 규칙 점수 종목이 AI 추천 순위에 다시 섞이지 않게 한다.

### 2.4 점수와 출처

- `rule_score`: 기존 점수 계산기의 결과
- `ai_score`: AI 세부 항목을 서버가 합산한 0~100 점수
- `recommendation_score`: 화면 정렬과 모드별 최종 추천에 쓰는 점수
- `final_score`: 기존 API 호환을 위해 유지하며 `recommendation_score`와 같은 값
- `score_source`:
  - `RULE`: AI 비활성 또는 일일 한도로 AI를 호출하지 않은 규칙 점수
  - `AI`: 정상 AI 결과가 반영된 점수
  - `RULE_FALLBACK`: AI 호출·검증 실패로 복구한 규칙 점수

`RULE`과 `RULE_FALLBACK`은 화면 배지와 API 필드에서 구분한다. 호출 한도 때문에
평가하지 않은 경우에는 `ai_status=SKIPPED_LIMIT`을 함께 기록한다.

## 3. AI 점수 산식

모델이 임의의 총점을 직접 만들지 않는다. 모델은 구조화 출력으로 제한된 범위의 세부
점수만 반환하고, 애플리케이션이 합계를 계산한다. 모든 세부 점수는 높을수록 매수
적합성이 좋은 방향이다. 따라서 `변동성·위험` 점수는 위험이 낮고 관리 가능한 경우 높다.

| 항목 | 범위 | 판단 기준 |
|---|---:|---|
| 추세 구조 | 0~25 | 이동평균 배열, 중기 추세, 52주 가격 위치 |
| 모멘텀 | 0~20 | RSI, MACD, 5일·20일 수익률 조합 |
| 거래량 확인 | 0~15 | 상승·돌파·반등의 거래량 확인 |
| 변동성·위험 | 0~15 | ATR, 급등 과열, 불리한 손익비 위치 |
| 진입 타이밍 | 0~15 | 지지선 접근, 이격도, 추격매수 위험 |
| 전략 적합도 | 0~10 | 눌림목·돌파·추세추종 등 전략별 적합성 |

공통 항목은 종목당 한 번만 반환한다. 같은 종목이 여러 전략 후보라면
`strategy_fit`을 전략 ID별 맵으로 반환한다.

```json
{
  "trend": 21,
  "momentum": 15,
  "volume": 11,
  "risk": 12,
  "timing": 10,
  "strategy_fit": {"pullback_v3": 8},
  "confidence": 0.82,
  "reason_codes": ["UPTREND", "HEALTHY_PULLBACK", "VOLUME_WEAK"]
}
```

전략별 `ai_score`는 공통 5개 항목과 해당 전략 적합도의 합이다. 모든 정수 범위를
서버가 검증하고 범위 위반, 누락, 알 수 없는 전략 ID 또는 스키마 위반은 전체 응답
실패로 처리한다. 모델이 반환한 총점은 신뢰하지 않으며 총점 필드 자체를 요청하지 않는다.

## 4. 후보 선정과 데이터 흐름

후보 생성은 전략별 저장을 즉시 수행하는 구조에서, 모든 전략의 후보 초안을 먼저 모으는
구조로 바뀐다.

```text
종목 컨텍스트 1회 생성
  -> 전략별 신호·rule_score·안전 조건 계산
  -> CandidateDraft 전량 수집
  -> 모드별 AI 대상 결정
  -> 티커별 전략 목록으로 그룹화
  -> 티커당 AI 호출 1회
  -> 전략별 ai_score와 recommendation_score 계산
  -> 후보·AI 결과·사용량을 저장
```

### 4.1 호출 대상

- `off`: 0개
- `rerank`: 기존 규칙으로 매수 가능하다고 판정된 종목을 `rule_score` 순으로 선택
- `replace`: 신호와 안전 조건을 통과한 종목을 `rule_score` 순으로 1차 압축하고,
  압축 집합만 최종 추천 대상으로 사용
- 두 AI 모드 모두 전체 전략을 합친 고유 티커 기준 일일 최대 5개
- 같은 티커가 여러 전략에 포함되면 한 요청에 해당 전략 목록을 함께 전달

AI가 탈락 종목을 신규 후보로 올리는 경로는 만들지 않는다. 다음 날 주문 직전의 신호
재검사와 스냅샷 신선도 가드도 유지한다.

### 4.2 결과 재사용

AI 결과는 다음 키로 영속 저장한다.

```text
ref_date + ticker + input_hash + model_id + prompt_version
```

동일 거래일 후보 생성 재실행, 서버 재시작 또는 전략 간 중복이 있어도 저장 결과를
재사용한다. 성공 결과뿐 아니라 실패한 호출도 해당 거래일의 사용량으로 기록해 반복
호출 폭증을 막는다. 입력 지표, 모델 또는 프롬프트 버전이 바뀌면 해시가 달라져 새
평가로 취급하되 일일 호출 한도는 그대로 적용한다.

## 5. 토큰과 비용 제어

### 5.1 일일 호출 예산

`MAPS_AI_DAILY_CALL_LIMIT` 기본값은 `5`이며 유효 범위는 0~100이다. 한도는 전략별이
아니라 전체 후보 생성 작업의 고유 티커 기준이다. 실제 네트워크 요청을 시작한 시점에
성공 여부와 무관하게 한 건을 소비한다.

### 5.2 입력 축소

최근 30거래일 OHLCV 표 전체를 보내지 않는다. 로컬에서 다음 특징을 계산해 짧은 JSON으로
전달한다.

- RSI14, MACD와 시그널·히스토그램
- 거래량 비율과 ATR 비율
- MA20·MA60 이격도와 이동평균 배열
- 5일·20일 수익률
- 52주 가격 위치
- 돌파·눌림·과열 플래그
- 현재가, 기준일, 활성 전략 ID 목록

목표 입력은 종목당 300~500토큰이다. 프롬프트 크기 회귀 테스트로 직렬화된 시스템 지시와
사용자 입력의 문자 상한을 고정한다.

### 5.3 출력 축소

출력은 점수 항목, `confidence`, 최대 3개의 `reason_codes`로 제한한다. 자연어 장문 분석,
AI 매수가·손절가·목표가를 요청하지 않는다. 화면 설명은 사유 코드의 서버측 한국어
매핑으로 만든다. 역발상 분석이 활성화된 경우에도 별도 모델 호출을 추가하지 않고 같은
응답 스키마에 짧은 판정 필드를 선택적으로 포함한다.

### 5.4 프롬프트 캐싱과 재시도

호출량과 정적 프롬프트 길이가 Claude 캐시 최소 길이에 미달하므로 Bedrock 프롬프트
캐싱을 사용하지 않는다. 캐시 조건을 맞추려고 지시문을 늘리지 않는다. 네트워크 또는
모델 오류에 대한 자동 재시도도 하지 않는다. 실패는 즉시 `RULE_FALLBACK`으로 처리한다.

## 6. 모델과 Bedrock API

기본 모델은 Claude Sonnet 4.6이다.

```text
MAPS_AI_SCORING_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

하루 최대 5회 규모에서는 Haiku 4.5 대비 절대 비용 차이가 작고, 상충하는 기술 지표와
전략 적합도를 평가할 때 Sonnet 4.6의 지시 준수와 복합 추론 능력이 더 중요하다고
판단했다. 비용 또는 지연시간을 우선하는 환경은 다음 모델로 바꿀 수 있다.

```text
us.anthropic.claude-haiku-4-5-20251001-v1:0
```

Bedrock Mantle의 Anthropic Messages 경로는 구조화 출력을 지원하지 않으므로 사용하지
않는다. 기존 `boto3`의 `bedrock-runtime` 클라이언트와 Converse 또는 InvokeModel API를
사용하고 JSON Schema 구조화 출력을 강제한다. Sonnet 4.6은 낮은 effort와 작은 출력
상한으로 호출한다. Bedrock의 Sonnet 4.6은 adaptive thinking을
비활성화하는 요청을 허용하지 않으므로 `thinking={"type": "adaptive"}`와
`output_config.effort="low"`를 사용한다. `temperature`, `top_p`, `top_k` 같은 샘플링
파라미터는 보내지 않는다. AI Scoring 모델 설정은 기존 공용
`AWS_BEDROCK_MODEL_ID`와 분리한다.

관련 공식 자료:

- https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html
- https://platform.claude.com/docs/en/about-claude/models/choosing-a-model

## 7. 설정과 하위 호환

추가 설정:

- `maps_ai_scoring_mode: Literal["off", "rerank", "replace"] = "off"`
- `maps_ai_daily_call_limit: int = 5`
- `maps_ai_rerank_weight: float = 0.20`
- `maps_ai_scoring_model_id: str = "us.anthropic.claude-sonnet-4-6"`
- `maps_ai_request_timeout_seconds: float`

기존 `maps_ai_technical_scoring_enabled=true`이고 새 모드가 명시되지 않은 환경은
`rerank`로 해석한다. 기존 `maps_ai_technical_score_weight`와
`maps_ai_candidate_top_n`은 각각 새 가중치와 일일 한도의 하위 호환 입력으로 읽되,
새 설정이 함께 있으면 새 설정이 우선한다. 레거시 설정을 사용한 경우 한 번의 경고를
남긴다.

## 8. 저장 구조와 API

`candidate_snapshot`에 다음 필드를 추가하거나 기존 필드를 재사용한다.

- `rule_score: float`
- 기존 `ai_technical_score`를 `ai_score` 응답 필드로 노출
- `recommendation_score: float`
- `score_source: str`
- `ai_scoring_mode: str`
- `ai_status: str | None`
- `ai_confidence: float | None`
- `ai_reason_codes: JSON | None`
- `ai_model_id: str | None`

`final_score`는 `recommendation_score`와 같은 값을 저장한다. 기존
`ai_buy_price`, `ai_stop_price`, `ai_target_price` 컬럼은 스키마 호환을 위해 유지하지만
신규 AI 응답으로 채우지 않고 기존 규칙 가격 계획을 저장한다.

별도 AI 결과·사용량 테이블은 최소 다음 정보를 보존한다.

- 캐시 키 구성 필드와 입력 해시
- 요청 상태: `SUCCESS`, `FAILED`, `SKIPPED_LIMIT`
- 공통 세부 점수와 전략별 적합도
- 입력·출력 토큰 수
- 오류 분류 코드와 생성 시각

API는 기존 필드를 유지하면서 새 점수와 출처 필드를 추가한다. 민감한 AWS 오류 원문이나
자격증명은 응답에 포함하지 않는다.

## 9. 화면과 운영 가시성

후보 화면의 기본 점수 열은 `recommendation_score`를 표시하고 출처 배지를 붙인다.
세부 정보에는 `rule_score`, `ai_score`, 가중치, 신뢰도와 사유 코드를 표시한다.

운영 설정 화면에는 모드, 일일 호출 한도, rerank 가중치와 모델 ID를 노출한다. 자격증명은
노출하지 않는다. 후보 생성 완료 로그에는 다음 합계를 남긴다.

- AI 대상 고유 티커 수
- 실제 호출, 저장 결과 재사용, 성공, 실패, 한도 초과 건수
- 입력·출력 토큰 합계
- 적용 모델과 모드

달러 비용은 리전·요금제 변경으로 부정확해질 수 있으므로 코드에 단가를 고정하지 않고
토큰 수를 정본으로 기록한다.

## 10. 오류 처리

다음 상황은 예외를 후보 생성 밖으로 전파하지 않고 `RULE_FALLBACK`으로 처리한다.

- AWS 자격증명 또는 모델 접근 권한 오류
- 타임아웃, 제한, 서비스 오류
- 빈 응답, 구조화 출력 스키마 위반
- 세부 점수·신뢰도 범위 위반
- 요청한 전략 적합도 누락 또는 알 수 없는 전략 ID

모드가 `off`이거나 자격증명이 없어서 호출을 시도하지 않은 경우는 실패가 아니라
`RULE`이다. 오류 로그에는 티커, 오류 분류와 모델만 포함하고 비밀값과 원문 응답 전체는
남기지 않는다.

## 11. 테스트와 검증

### 11.1 단위·통합 테스트

- `off`, `rerank`, `replace`별 후보 자격과 추천점수 공식
- `rerank`에서 AI 점수가 후보 탈락이나 최소점수 판정을 바꾸지 않음
- `replace`에서 정상 AI 점수가 추천점수를 대체함
- AI 실패 시 `RULE_FALLBACK`과 규칙 점수 복구
- 전체 전략 합산 5회 제한과 티커 중복 제거
- 동일 입력 재실행 시 네트워크 호출 없이 결과 재사용
- 실패 호출도 일일 한도에 포함
- 세부 점수 범위와 서버측 합계 계산
- 전략별 적합도 누락·초과·알 수 없는 ID 거부
- 구조화 출력 요청에 샘플링·확장 사고 파라미터가 의도대로 설정됨
- 프롬프트가 원시 30봉 표를 포함하지 않고 크기 상한을 지킴
- API와 화면의 `RULE`, `AI`, `RULE_FALLBACK` 표시
- 기존 주문 신호 재검사, 신선도 가드와 전체 테스트 회귀

### 11.2 모델 평가

실제 주식 추천 성과는 공개 모델 벤치마크로 입증할 수 없다. 운영 활성화 전 동일한 과거
후보 표본을 Sonnet 4.6과 Haiku 4.5에 입력해 다음을 비교하는 평가 도구를 제공한다.

- 스키마 성공률과 범위 오류율
- 동일 입력 반복 시 점수 변동폭
- 지표를 한 방향으로 바꾼 대조쌍의 점수 단조성
- 전략 적합도와 사유 코드의 일관성
- 입력·출력 토큰과 지연시간

기본 모델은 Sonnet 4.6으로 두되 평가 결과가 동등하면 운영자가 Haiku 4.5로 변경할 수
있다.

### 11.3 단계적 활성화

1. 배포 직후 `off`로 스키마와 기존 후보 생성 회귀 확인
2. `rerank`, 일일 한도 5로 점수·토큰·실패율 관측
3. 주문에는 규칙 점수를 계속 사용하며 충분한 기간 동안 추천 순위 기록
4. 사용자 판단으로만 `replace` 활성화

## 12. 범위 밖

- AI가 비후보 종목을 신규 추천하는 기능
- AI 점수를 주문 안전 조건보다 우선하는 기능
- AI 생성 매수가·손절가·목표가
- 프롬프트 캐싱을 위한 인위적 프롬프트 확장
- Bedrock 배치 추론과 S3 작업 관리
- 과거 후보 행 삭제 또는 보존 정책 변경
- 자동 매매 성과를 보장하거나 모델 성능만으로 `replace`를 자동 활성화하는 기능
