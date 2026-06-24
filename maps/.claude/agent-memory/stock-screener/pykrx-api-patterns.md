---
name: pykrx-api-patterns
description: pykrx 함수별 신뢰도, 동작 패턴, 반환 컬럼명 및 알려진 실패 사례
metadata:
  type: reference
---

## 안정적으로 동작하는 API

- `get_market_ohlcv_by_date(start, end, ticker)` — 단일 종목 일별 OHLCV. 가장 안정적.
  - 반환 컬럼(한글): `시가`, `고가`, `저가`, `종가`, `거래량`, `등락률`
  - 인덱스: datetime (날짜)
  - 예: `krx.get_market_ohlcv_by_date("20260101", "20260623", "005930")`

## 불안정하거나 실패하는 API (2026-06 기준)

- `get_market_sector_classifications(date, market)` — **KRX 서버 JSON 파싱 오류로 반복 실패**
  - 오류: `Expecting value: line 1 column 1 (char 0)` (빈 응답)
  - 대안: 업종별 대표 종목 수동 목록 사용 + 활성 여부를 OHLCV로 간접 확인
- `get_market_ticker_list(date, market)` — 마찬가지로 서버 오류 발생
- `get_market_fundamental_by_date(start, end, ticker)` — 실패 (동일 오류)
- `get_market_cap_by_date(start, end, ticker)` — 실패 (동일 오류)
- `get_market_ohlcv(date, market)` — 시장 전체 단일일 OHLCV, 실패 빈도 높음

## 관리종목/거래정지 감지

- pykrx 공식 API 없음. 현재 구현: `get_halt_list` = 거래량 0 heuristic + `MAPS_HALTED_TICKERS` 환경변수
- 거래량 0인 종목을 최신일 기준으로 필터링하는 것이 실용적 대안

## 유동성 임계값

- 스크리닝 하한: 일평균 거래대금 **50억원** (5,000,000,000 KRW) 이상
  - KOSPI 대형주 분류: 1,000억원 이상
  - 중형주: 100~1,000억원
  - 소형주/제외 대상: 50억원 미만
- 유동성 점수 log-scale 정규화:
  - 기준하한: 50억원 (log10 = 9.7)
  - 기준상한: 100조원 (log10 = 14.0)
  - score = (log10(avg_tv) - 9.7) / (14.0 - 9.7) × 100

## 컬럼명 통일 매핑

```python
col_map = {
    "시가": "open", "고가": "high", "저가": "low",
    "종가": "close", "거래량": "volume", "등락률": "chg_pct"
}
```

## 대표 업종별 종목 (WICS 기준, 2026-06 검증)

### 은행/금융
- 105560 KB금융, 055550 신한지주, 086790 하나금융지주, 316140 우리금융지주
- 024110 기업은행, 032830 삼성생명, 088350 한화생명, 039490 키움증권

### 반도체
- 005930 삼성전자, 000660 SK하이닉스, 000990 DB하이텍
- 240810 원익IPS, 058470 리노공업, 042700 한미반도체
- 357780 솔브레인, 067310 하나마이크론

### 필수소비재
- 271560 오리온, 097950 CJ제일제당, 004370 농심
- 005300 롯데칠성, 033780 KT&G, 051900 LG생활건강, 145990 삼양식품
