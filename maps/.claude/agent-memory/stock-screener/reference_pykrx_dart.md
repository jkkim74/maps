---
name: pykrx-dart-data-access
description: pykrx/DART 데이터 조회 방법 및 함정 — 스크리너에서 실제로 검증된 패턴
metadata:
  type: reference
---

## pykrx

- `stock.get_market_ohlcv_by_date(start, end, ticker)` 는 정상 작동 (단일 종목 OHLCV)
- `stock.get_market_ticker_list(date, market=...)` 는 현재 빈 리스트 반환 (KRX 공시 API 막힘)
- `stock.get_market_sector_classifications(date, market=...)` 도 같은 이유로 실패
- `stock.get_market_ticker_name(ticker)` 는 정상 작동 (종목명 조회)
- 컬럼명이 한글로 반환됨 (시가/고가/저가/종가/거래량) → 직접 rename 필요

## DART

- 전체 상장사 목록: `GET https://opendart.fss.or.kr/api/corpCode.xml` → ZIP(CORPCODE.xml)
  - 항목: corp_code, corp_name, stock_code (6자리), modify_date
  - corp_cls 필드 없음 (KOSPI/KOSDAQ 구분 불가 → 별도 company.json 조회 필요)
- 기업 정보: `GET /api/company.json?corp_code=...` → induty_code(업종코드), corp_cls(Y=KOSPI, K=KOSDAQ)
- 재무제표: `GET /api/fnlttSinglAcntAll.json?corp_code=...&bsns_year=2024&reprt_code=11011&fs_div=CFS`
  - reprt_code: 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
  - fs_div: CFS=연결, OFS=별도 (CFS 우선, 실패 시 OFS fallback)

## DART 재무 파싱 주의점 (함정)

`fnlttSinglAcntAll` 은 같은 계정과목명이 여러 행으로 반환됨 (하위 항목 포함).
예: 자본총계가 0인 서브항목들이 먼저 나타나고 실제 합계가 뒤에 옴.
→ **각 계정명에 대해 절댓값이 가장 큰 non-zero 행을 선택해야 함** (pick_max 패턴).

금융지주/보험사의 부채비율은 레버리지 특성상 수백 ~ 수만 배로 나타남 → 점수화 시 상한 적용 필요.

## 섹터 매핑

pykrx 종목 리스트가 막혀 있어 대안:
1. DART corpCode.xml 에서 전체 상장사 확보 → stock_code(6자리) 사용
2. 업종 매핑은 DART induty_code(한국표준산업분류) 사용
   - 26xx = 반도체/IT(전자부품)
   - 21 = 헬스케어/바이오(의약품)
   - 62,63 = 소프트웨어/IT서비스
   - 64,65,66 = 금융
   - 10,11,13,14,46,47 = 필수소비재
   - 24~31,41,49,51 = 산업재

## 유동성 필터

일평균 거래대금 기본 하한: 10억 KRW (lookback 20영업일)
계산: close * volume 의 20일 평균

## 전략 신호 구현 (검증 완료)

**pullback_v3/v2** (regime=mixed/strong만 적용):
  1. MA5 > MA_long(20)
  2. MA_long > MA_long.shift(20) (국면 필터)
  3. RSI(2) < 10
  4. low < low.shift(1)

**multi_asset_trend_v1** (모든 레짐):
  1. 골든크로스: MA(20) > MA(60) AND 전일 MA(20) <= MA(60)
  2. close > MA(20)
  3. MA(60) > MA(60).shift(20) (국면 필터)
