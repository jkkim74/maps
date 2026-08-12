# ai/

AWS Bedrock(Claude) 호출을 감싸는 패키지. 후보 점수 재조정, 종목 매매계획, 역발상 검증,
안전마진 평가를 담당한다.

**이 패키지의 공통 계약: 실패하면 값을 만들어 내지 않는다.** AI가 죽거나 스키마를 어기면
규칙 점수로 되돌리거나(`RULE_FALLBACK`) 수동 입력으로 닫는다. 중립값(50)이나 임의 가격을
채워 넣는 경로는 존재하지 않는다.

## Directory structure

```
ai/
├── __init__.py             # 빈 패키지 마커
├── contrarian_analyzer.py  # 코스톨라니식 역발상 논리 검증
├── evaluation.py           # 반복 관측 통계 (provider 호출 없음)
├── scoring.py              # 점수 payload 검증 + 모드별 합성 공식
├── scoring_service.py      # 전역 예산·캐시 오케스트레이션
├── technical_scorer.py     # 파생 지표 → Bedrock 1회 호출 어댑터
├── trade_planner.py        # 매매계획 구조화 출력 (fail-closed)
└── valuation_margin.py     # 안전마진 0-100 점수화
```

## scoring_service.py — 하루 한 번의 제한된 AI 패스

`AIStockScoringService.apply()` 가 후보 생성이 끝난 뒤 **규칙 점수만 있는** 행에 AI를 얹는다.

| 개념 | 내용 |
|---|---|
| 모드 | `MAPS_AI_SCORING_MODE` = `off` / `rerank` / `replace` (기본 `off`) |
| 호출 한도 | `MAPS_AI_DAILY_CALL_LIMIT` — **전체 전략 합산 고유 ticker 수** |
| 예약·캐시 | `ai_scoring_invocation` 테이블. 같은 날 같은 입력이면 재호출하지 않는다 |
| 모델 | `MAPS_AI_SCORING_MODEL_ID` |

행에 남는 감사 필드 — `candidate_snapshot.score_source` / `ai_status`:

| score_source | ai_status | 언제 |
|---|---|---|
| `AI` | `SUCCESS` | 정상 호출·검증 통과 |
| `RULE_FALLBACK` | 실패 코드 | 호출·스키마 실패 → 규칙 점수 사용 |
| `RULE` | `SKIPPED_LIMIT` | 일일 한도 초과 |
| `RULE` | `SKIPPED_UNCONFIGURED` | 자격증명 없음 (호출·예산 미사용) |
| `RULE` | `None` | 모드 `off` |

> ⚠️ `rerank` 는 **순위만** 바꾼다. 후보 자격과 최소 점수 판정은 계속 규칙 점수로 한다.
> `replace` 만 추천 점수를 대체하며, 한도 밖 종목은 관찰 기록만 남기고 추천·주문에서 빠진다.
> 모드를 바꿔도 **기존 후보는 재평가되지 않는다** — 다음 후보 생성부터 반영된다.

## scoring.py — 점수 계약

| 이름 | 설명 |
|---|---|
| `AIStockScore` | Pydantic 불변 구조화 출력. 항목별 점수를 받아 서버가 범위를 검증한 뒤 합산한다 |
| `AIStrategyFit` | 전략 적합도 항목 |
| `recommendation_score()` | 모드별 최종 추천 점수. 규칙 자격 판정을 바꾸지 않는다 |

`rerank` 합성식은 `rule_score * (1 - ai_weight) + ai_score * ai_weight` 이며 가중치는
설정값이다 — 숫자는 `settings.py` 와 이 함수를 본다.

## technical_scorer.py — Bedrock 호출 경계

| 이름 | 설명 |
|---|---|
| `AIStockFeatures.from_frame()` | 원시 봉 대신 **파생 지표만** 만든다 |
| `.canonical_json()` | 캐시 키에 쓰는 입력 해시의 원본 |
| `AITechnicalScorer.score()` | Bedrock **1회** 호출. 자동 재시도·batch·prompt caching 없음 |
| `.is_configured()` | 자격증명 없으면 호출도 예산도 쓰지 않는다 |

> ⚠️ **Bedrock 구조화 출력에 못 보내는 JSON Schema 키워드가 있다.** `minimum`, `maximum`,
> `minLength`, `maxLength`, `maxItems`, `prefixItems` 를 그대로 보내면 provider 오류로
> 즉시 실패한다(2026-08-07 실제 사고). 도메인 검증은 Pydantic 쪽에 남기고 전송 스키마에서만
> 제거·정규화한다.

## trade_planner.py — 가격은 BUY 에서만 나온다

`AITradePlan.validate_price_contract()` 가 가격 순서와 경계를 검증하고, **BUY 가 아니거나
검증에 실패하면 주문값을 비운다.** 호출부는 그때 수동 입력으로 전환해야 하며, 원고나 화면이
가격을 임의로 만들어 내면 안 된다. `StockTradeFacts` 가 모델에 보내는 사실의 상한이다.

## 나머지

| 모듈 | 역할 |
|---|---|
| `contrarian_analyzer.py` | `AIContrarianAnalyzer.analyze()` → `ContrarianAnalysisResult`. `position_size_multiplier` 로 사이징에 영향 |
| `valuation_margin.py` | `ValuationMarginScorer.score()` — 보수적 내재가치 대비 안전마진 0-100. 데이터는 `data/fundamental_repo.py` 의 provider 로 주입 |
| `evaluation.py` | 같은 종목을 여러 모델·여러 번 돌린 결과의 일관성·토큰·지연 집계. **네트워크 호출 없음** |

## 의존성

```
boto3 (bedrock-runtime) → 모든 AI 호출
pydantic               → 구조화 출력 검증
maps.common.settings   → 모드·한도·모델 ID
maps.common.models     → CandidateSnapshot, AIScoringInvocation
```
