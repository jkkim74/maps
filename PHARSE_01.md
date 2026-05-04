# Phase 1: 데이터 계층 + as-of-date 유니버스

Phase 0이 완료된 상태에서 진행.
pytest가 전부 통과하는 것을 먼저 확인하고 시작해줘.

## 작업 목표
DataCollector와 DataQualityFilter를 만든다.
DataQualityFilter는 단순 필터가 아닌 as-of-date 생성기로 구현한다.

## Step 1: data/krx_adapter.py
KRX Open API 래퍼 클래스. 실제 API 키는 환경변수 KRX_API_KEY.
아직 키가 없으면 MockKRXAdapter(테스트용 더미 데이터 반환)도 같이 만들어줘.
메서드: get_ohlcv(date), get_security_meta(date),
        get_halt_list(date), get_managed_list(date)

## Step 2: data/security_repo.py
security_metadata 테이블 CRUD.
Security 도메인 객체에 아래 메서드 구현:
- has_adjusted_price_as_of(ref_date) -> bool
- is_halted_on(ref_date) -> bool  # 정지 기간 이력 테이블 조회
- is_managed_on(ref_date) -> bool
- avg_turnover_20d_as_of(ref_date) -> float
- listing_days(ref_date) -> int

## Step 3: data/collector.py
DataCollector 클래스.
collect_daily(ref_date), collect_range(start, end) 구현.
수정주가 없으면 broker 폴백 (broker=None 이면 스킵 + 로그).

## Step 4: data_quality/universe_filter.py (핵심)
DataQualityFilter as-of-date 생성기.
generate(ref_date, candidates) -> UniverseResult

주의사항:
- backtest 모드: delisting_date > ref_date 이면 포함
- live 모드: delisting_date <= ref_date 이면 즉시 제외
- listing_date > ref_date 이면 "not_listed_yet"으로 제외
- ref_date 이후 정보는 절대 참조하지 않음

거부율 5% 초과 시 알림 로직 포함.
universe_quality_log에 결과 기록.

## Step 5: 테스트 작성 (테스트 먼저!)
tests/test_data_quality.py:
- test_generate_as_of_date: generate(ref_date) 호출 시 ref_date 이후 폐지 포함 안 함
- test_backtest_include_delisted: 백테스트 모드에서 폐지일 이전까지 포함
- test_no_lookahead: listing_date > ref_date 종목 제외
- test_recently_listed: 100일 미만 제외
- test_low_turnover_kospi: KOSPI 5억 미만 거부
- test_rejection_alert: 거부율 5% 초과 시 알림 호출 여부

## 완료 기준
- pytest tests/test_data_quality.py 전부 통과
- generate(ref_date) 호출 시 look-ahead 없이 종목 목록 반환
- collection_log 테이블에 수집 이력 기록