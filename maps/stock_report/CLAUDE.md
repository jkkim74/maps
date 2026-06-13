# stock_report/

외부 stock-report 도구 연동 패키지. 4종 리포트를 생성하고 DB에 저장한다.

## Directory structure

```
stock_report/
├── __init__.py  # 빈 패키지 마커
└── runner.py    # run_report, run_all_reports, run_all_reports_if_idle
```

## runner.py

### 리포트 유형

| 키 | 이름 |
|---|---|
| `premium` | 프리미엄 주식 리포트 |
| `updown` | Gap Up & Down 리스크 리포트 |
| `summary` | Market Summary 리포트 |
| `supply` | Market Supply 리포트 |

### 동작 원리

1. `MAPS_STOCK_REPORT_PATH` (기본 `/opt/stock_report`) 경로를 `sys.path`에 추가
2. 외부 `report_generator` 모듈의 생성 함수 4개를 임포트
3. 각 함수 호출 → `report_data.html_content`를 `stock_report_runs` 테이블에 저장
4. 발송(Telegram/Slack/GitHub)은 일절 하지 않음 — 저장만 수행

### 주요 함수

| 함수 | 설명 |
|---|---|
| `run_report(db, report_type)` | 지정 리포트 1개 생성. 생성된 `run_id` 반환 |
| `run_all_reports(db)` | 4종 순서대로 생성. `list[int]` (run_id 목록) 반환 |
| `run_all_reports_if_idle(db)` | 실행 중(`status="running"`)인 작업이 없을 때만 `run_all_reports()` 호출 |

### DB 기록 구조 (`stock_report_runs`)

| 컬럼 | 값 |
|---|---|
| `status` | `"running"` → `"completed"` 또는 `"failed"` |
| `html_content` | 생성된 HTML 문자열 |
| `trade_date` | 리포트 기준 거래일 |
| `meta_json` | 리포트 메타데이터 JSON |
| `error_message` | 오류 발생 시 메시지 |

### 스케줄

`ops/scheduler.py`에서 매일 `MAPS_STOCK_REPORT_TIME`(기본 15:00)에 자동 실행. 주말 포함.

### 리눅스 환경 폰트

서버(Linux)에서는 `NanumGothic` 폰트를 자동 적용 (Windows `Malgun Gothic` 대체).

## 의존성

```
maps.common.models   → StockReportRun
maps.common.settings → get_settings() (maps_stock_report_path)
외부: report_generator (MAPS_STOCK_REPORT_PATH 아래 위치)
```
