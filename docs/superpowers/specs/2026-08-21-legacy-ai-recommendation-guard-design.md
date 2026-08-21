# 기존 AI 권고 불명 전략매매 진입 방어 설계

## 1. 목표

`source='ai_trade_plan'` 인 기존 전략매매 픽이 `ai_recommendation=NULL` 인 상태로 남아
있으면 신규·잔여 진입을 차단한다. 원래 권고를 운영자가 한 번 복원하면 현재 정책대로
BUY는 그대로, WATCH/SELL은 경고와 감사 기록을 남기고 진입을 허용한다.

기존 보유분의 손절·익절은 권고 기록과 무관하게 계속 실행한다. 수동 입력 픽의
`ai_recommendation=NULL` 도 정상 상태로 유지한다.

## 2. 범위와 비범위

### 범위

- `source='ai_trade_plan' AND ai_recommendation IS NULL` 판정을 공용 순수 함수로 정의
- 단일·분할 전략매매의 신규 또는 잔여 BUY를 `AI_RECOMMENDATION_UNKNOWN`으로 차단
- 워치리스트 API·화면, 일일 다이제스트, 스케줄러 로그에 같은 사유 코드 노출
- 활성 AI 픽의 비어 있는 권고를 `BUY|WATCH|SELL` 중 하나로 한 번만 복원하는 관리자 API·화면
- 단일·분할 진입, 청산 유지, 수동 픽, 복원 API, 다이제스트에 대한 회귀 테스트

### 비범위

- 과거 행을 추측해서 자동 백필하지 않는다
- 이미 기록된 AI 권고를 수정하거나 덮어쓰지 않는다
- WATCH/SELL을 차단 정책으로 바꾸지 않는다
- 후보 자동주문, 점수 준비도, 손절·익절 정책을 변경하지 않는다
- 별도 승인 이력 테이블이나 신규 DB 컬럼을 만들지 않는다

## 3. 선택한 접근

### 채택: 기존 `source`와 `ai_recommendation`을 이용한 런타임 차단 + 1회 복원

`maps.ops.strategy_trade_plan`에 아래 공용 계약을 둔다.

```python
AI_RECOMMENDATION_UNKNOWN = "AI_RECOMMENDATION_UNKNOWN"

def has_unknown_ai_recommendation(
    source: str,
    ai_recommendation: str | None,
) -> bool:
    return source == "ai_trade_plan" and ai_recommendation is None
```

스케줄러는 이 판정을 모든 전략매매 BUY 직전에 적용한다. 분할매매는 기존 포지션의 청산과
회차 동기화를 먼저 수행하고, 다음 회차 주문을 만들기 직전에만 차단한다. 단일매매도 청산
분기에는 적용하지 않고 ARMED 신규 진입에만 적용한다.

권고 복원 API는 `POST /api/v1/analysis-picks/{pick_id}/ai-recommendation`이고 요청은
`{"recommendation":"BUY|WATCH|SELL"}`이다. 다음 조건을 모두 만족할 때만 저장한다.

- 픽이 존재한다
- `source == 'ai_trade_plan'`
- `state`가 `ARMED` 또는 `BOUGHT`
- 현재 `ai_recommendation IS NULL`

이미 값이 있으면 409로 거절해 감사 값을 불변으로 유지한다. 성공 응답은 기존
`AnalysisPickItem`을 재사용한다. 새 라우터 권한을 열지 않으므로 운영 인증 환경에서는
기존 fail-closed 정책대로 관리자만 POST할 수 있다.

### 기각: 차단 후 DB 직접 수정

코드는 가장 작지만 운영자가 매번 DB를 직접 수정해야 하고 입력 검증·감사 경계가 없다.

### 기각: 별도 재승인 컬럼·이력 테이블

원래 권고를 복원하는 현재 문제에는 기존 컬럼으로 충분하다. 신규 마이그레이션과 상태 모델은
추가 가치보다 복잡성이 크다.

## 4. 인터페이스와 표시

- `AnalysisPickItem.entry_block_reason: str | None`을 추가한다. 불명 AI 픽이면
  `AI_RECOMMENDATION_UNKNOWN`, 아니면 `None`이다.
- `DigestConditionalEntry.warnings: list[str]`를 추가해 같은 코드를 기록한다.
- 워치리스트 화면은 차단 사유와 “기존 AI 권고 복원 필요” 문구를 표시하고, 해당 행에만
  `BUY|WATCH|SELL` 선택 및 복원 버튼을 제공한다.
- 스케줄러는 차단할 때 픽 ID·티커·단일/분할 여부와 사유 코드를 WARNING으로 남긴다.
- 권고가 복원되면 기존 `AI_RECOMMENDATION_NOT_BUY` 경고 로직을 그대로 사용한다.

## 5. 오류 및 안전 경계

| 상황 | 동작 |
|---|---|
| AI 계획 출처 + 권고 없음 | 신규·잔여 BUY 차단, 기존 청산 유지 |
| 수동 출처 + 권고 없음 | 기존 동작 유지 |
| AI 권고 WATCH/SELL | 기존 정책대로 경고 후 진입 허용 |
| 존재하지 않는 픽 | 복원 API 404 |
| 수동·종결 픽 복원 요청 | 복원 API 409 |
| 이미 권고가 있는 픽 | 복원 API 409, 기존 값 보존 |
| 잘못된 권고 문자열 | Pydantic 422 |

## 6. 테스트와 완료 기준

- 단일 ARMED AI 픽의 권고가 없으면 BUY 주문 0건
- 분할 BOUGHT AI 픽의 다음 회차 권고가 없으면 BUY 주문 0건
- 같은 분할 픽이 손절·익절 조건이면 청산은 정상 제출
- 수동 픽의 권고가 없어도 기존 진입 동작 유지
- 복원 API가 유효한 권고를 한 번 저장하고 두 번째 변경을 거절
- 워치리스트 응답과 조건부 진입 다이제스트에 동일한 차단 코드 포함
- WATCH/SELL 복원 후 기존 비차단 경고가 유지됨
- 관련 pytest, 전체 `tests`, `node --check static/js/stock-analysis.js`, `git diff --check` 통과

## 7. 배포

스키마 변경과 데이터 백필이 없으므로 Alembic 적용은 없다. 코드 배포 후 서비스 재시작 전후로
활성 AI 전략매매 픽 중 권고 NULL 건수를 조회하고, 재시작 후 해당 픽의 차단 WARNING과
기존 포지션 청산 모니터가 계속 동작하는지 확인한다.
