# stock_analysis/

사용자가 종목 하나를 직접 분석하는 화면(SCR — 종목분석)의 백엔드. pykrx 시세·기술적 지표와
DART 재무제표를 모아 AI 원고를 만들고, 그 결과를 **불변 이력**으로 저장한다.

## Directory structure

```
stock_analysis/
├── __init__.py   # 빈 패키지 마커
├── analyzer.py   # 시세·지표·재무 수집 + Bedrock 스트리밍 분석
└── history.py    # 분석 이력 저장 · 현재가 오버레이 갱신
```

## analyzer.py

| 함수 | 설명 |
|---|---|
| `resolve_ticker(query)` | 종목명 또는 6자리 코드 → `(코드, 종목명)` |
| `calc_technicals()` | RSI·MACD·이동평균선 + 6개월 차트 데이터 |
| `fundamentals()` | PER·PBR·EPS·시가총액 |
| `dart_corp_code()` | `corpCode.xml(zip)` 에서 종목코드 → DART 고유번호 |
| `dart_financials()` | 최근 3개년 주요 재무계정 (연결 우선) |
| `analyze()` | 위를 합친 종합 분석 dict |
| `stream_llm_analysis()` | Bedrock Claude 로 7단계 분석 텍스트를 **SSE 스트리밍** |

## history.py — 저장 규칙

| 함수 | 설명 |
|---|---|
| `save_analysis_history()` | 완료된 분석을 **중복 제거 없이** 새 행으로 저장 |
| `save_analysis_history_with_new_session()` | 작업 스레드용 전용 세션 버전 |
| `refresh_analysis_price()` | `latest_*` 와 `price_refreshed_at` 만 갱신 |
| `CurrentPriceUnavailable` | 브로커·저장 일봉 모두 실패 — 저장값을 보존한 채 503 |

> ⚠️ **분석 원본은 생성 후 바꾸지 않는다.** `snapshot`, AI 원고, 구조화 `trade_plan`,
> 분석 당시 가격은 불변이다. 같은 종목을 재분석하면 **새 행**이 쌓이고, 현재가 갱신은
> 기존 행의 `latest_*` 열만 건드린다. 이 경계가 깨지면 과거 판단 근거가 사라진다.

> ⚠️ SSE 경로와 단일 응답 경로 모두 완료 시 이력을 **정확히 한 번** 저장한다.
> SSE 저장만 실패하면 분석 결과는 계속 표시하고 `history_error` 를 함께 내려보낸다.

> ⚠️ `created_at` 은 **UTC naive** 다. API 는 명시적 UTC 로 보정해서 내보내야 브라우저
> KST 표시가 9시간 어긋나지 않는다.

## 인접 경계

- 저장 모델: `common/models.py` 의 `StockAnalysisHistory`
- 라우터: `api/stock_analysis.py` (`/api/v1/stock-analysis`)
- AI 매매계획: `ai/trade_planner.py` — 실패 시 가격을 만들지 않고 수동 입력으로 닫는다
- 최종 승인된 실행 상태는 `AnalysisPick` 으로 **분리 보관**한다 (여기서 관리하지 않는다)

## 의존성

```
pykrx    → 시세·지표
DART OpenAPI → 재무제표 (corpCode.xml)
boto3    → Bedrock 스트리밍
```
