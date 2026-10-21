# 후지모토 부족 자료 보완

승인된 [실행 계획](../superpowers/plans/2026-10-21-fujimoto-data-completion.md)에 따라
자료 누락과 확인된 PER 미적용을 구분한다. 매출·이익·배당 기준은 유지한다.
제품 코드 `6d653d9`, 스키마·의존성 변경 없음.

## 구현

- 새 선별은 버전 2로 숫자·확인된 계산 불가·원인 미확인을 구분한다. 원인 미확인이 있는 업종은 계속 차단한다.
- `valuation_source`에 원문과 실제 확인 시각을 보존한다. `valuation_review`는 같은 제공기관의 공식 설명, 정확한 종목·날짜·원천 ID·해시와 검토 사유에 연결된다. 양수 PER을 계산 불가로 바꾸지 못한다.
- 과거 스냅샷은 기존 버전 1로 재현한다. 업종 중앙값의 모집단은 기존처럼 자기 종목을 포함한다. 별도의 최소 타사 수 규칙은 추가하지 않았다.
- `AnnualCollector.collect(..., tickers=[...])`는 최신 실측 적격 종목으로 대상을 제한하고 탈락 대상·시도한 대상·오류 종류를 반환한다. 기존 기본 순환 수집과 요청·시간 제한은 유지한다.
- CLI `scripts/fujimoto_research.py --collect-annual --database URL --tickers CODE1,CODE2 --batch 20 --output FILE`로 실행할 수 있다. 운영 DB URL은 로그·셸 인자에 노출하지 말고 설정을 읽는 Python 호출을 사용한다.

변경 파일은 `maps/fujimoto/{evidence,replay,sources}.py`, 연구 CLI,
두 회귀 테스트 파일과 관련 `CLAUDE.md`다. 매매 설정·승인·기존 보유는 변경하지 않는다.

## 운영 원천 확인

작업 전 선별 118137: 2,762종목 중 통과 0. 실측 적격 1,208종목 중 업종 PER 부족 1,204,
연간 이력 부족 1,156(사유 중복). 연간 원천 301건/91종목, 비교 가능성 검토 2건이었다.

공식 [KRX PER 화면](https://data.krx.co.kr/contents/MDC/STAT/standard/MDCSTAT035.jsp)은
최근 사업보고서·기업이벤트 조정 및 정규장 가격 기준을 명시한다. 실제 10월 21일 응답은
2,714종목 중 1,167종목의 PER이 `-`다. 이 표기만으로 각 회사의 계산 불가 사유를 확정할 수 없어
미적용 검토를 임의로 생성하지 않는다. 실제 원문은 운영 비공개 백업에 보관했다.

공식 [전 종목 시세 설명](https://data.krx.co.kr/contents/MDC/STAT/standard/MDCSTAT015.jsp)에 따르면
당일 종가에는 지연된 애프터마켓 가격이 표출될 수 있다. 반면
[개별 종목 시세 설명](https://data.krx.co.kr/contents/MDC/STAT/standard/MDCSTAT017.jsp)은
OHLC가 정규장 가격이고 거래량·거래대금은 전체 거래 합계임을 명시한다.
이 차이를 확인하고 개별 종목 비수정 일봉 전체를 정본으로 사용했다.

우선 후보 20종목의 10월 20일까지 원문을 비교해 **883건**을 정정했다.
가격 범위·유한수 검사 거부는 0건이었다. `price_source_repair` 감사 근거 20건에
변경 전후 값, 원문, 출처·해시를 남겼으며 불변 후보 근거는 수정하지 않았다.
오늘 거래량은 아직 변할 수 있어 이 작업의 가격 정정 범위에서 제외했다.

## 검증·배포 진행

- 관련 4개 파일 회귀 103 passed; 추가 경계 검사 16 passed.
- 과제 리뷰 및 전체 변경 독립 리뷰 모두 PASS, 조치할 지적 없음.
- 전체 격리 회귀 `python scripts/run_isolated_tests.py tests maps/tests -q --tb=short`: **2180 passed, 기존 경고 24개, 431.83초**. 컴파일·diff 검사 통과.
- 운영 반영·추가 연간 수집·재심사 결과는 완료 후 아래 기록을 갱신한다.

## 보관과 남은 조건

운영 원문·감사·스크립트는 `/opt/maps/backups/fujimoto-data-completion-20261021/`에만 보관한다.
정상 백업은 `before-data-completion-complete.dump`(561,249,940 bytes, pg_restore 목록 검사 통과)다.
최초 `before-data-completion.dump`는 기존 백업 테이블의 읽기 권한 문제로 실패한 파일이므로 복원에 사용하지 않는다.

유효한 PER 미적용 사유, 필요한 공시·연도별 비교 근거, 최신 확정 가격이 갖춰진 종목만 통과시킨다.
당일 새 연간 자료는 다음 거래일인 10월 22일부터 사용 가능하다. 20거래일 시험은 별도 승인 대상이며
이번 작업 중 전역 주문 OFF, KIS 실거래 false, observe, 신규 진입·매도 동의 false를 유지한다.
