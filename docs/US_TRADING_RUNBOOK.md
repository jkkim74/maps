# 미국주식 모의매매 운영 안내

미국 전용 모의계좌의 관리자 주문·취소와 체결 대사를 제공한다. 실제 전략 실행과 실거래는 제공하지 않는다. 설계 기준은 [프로그램 목록](us-trading-program-list.md), [화면설계서](ui-design/us-trading-screen-design.md)다.

## 설치와 데이터베이스

Python 3.11 이상 가상환경에서 의존성을 설치한 뒤, 대상 데이터베이스를 확인하고 마이그레이션한다.

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
```

미국 원장은 국내 KST·float 회계와 분리한 `us_*` 테이블에 Decimal/Numeric으로 저장한다. 공통 주문·실행 컨텍스트, 예외와 계좌 잠금은 재사용한다. 국내 기본 브로커 선택과 자격증명은 변경하지 않는다.

## 기본 설정

아래는 **로컬 모의 예시**다. 금액과 종목은 운영 권장값이 아니라 재현용 입력이다. 비밀값은 저장소에 넣지 않는다.

```dotenv
MAPS_BROKER_MODE=mock
MAPS_LIVE_TRADING_ENABLED=false
MAPS_US_ENABLED=false
MAPS_US_BROKER_MODE=mock
MAPS_US_ACCOUNT_NO=us-local-demo
MAPS_US_ALLOWED_SYMBOLS=AAPL:NASD
MAPS_US_MAX_ORDER_USD=1000
MAPS_US_MAX_EXPOSURE_USD=3000
MAPS_US_MAX_DAILY_LOSS_USD=100
MAPS_US_MOCK_CASH_USD=10000
MAPS_US_SYNC_SECONDS=60
MAPS_US_QUOTE_MAX_AGE_SECONDS=30
MAPS_US_SNAPSHOT_MAX_AGE_SECONDS=90
```

설정을 검토한 후 `MAPS_US_ENABLED=true`로 미국 모의 실행과 주기 동기화를 켠다. 전역 `MAPS_DRY_RUN=true`이면 주문·취소를 차단한다. 국내 실거래 스위치를 미국 주문을 위해 켜지 않는다.

서버를 실행하고 관리자로 `/us-trading`에 접속한다.

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

처음에는 동기화와 신선한 시세가 필요하다. 화면의 차단 사유가 없어야 신규 주문이 가능하다. 금액·시각 미확인을 0이나 현재 시각으로 대체하지 않는다.

## 로컬 모의 시세·체결 재현

로컬 모의 브로커는 지정가로 자동 체결된다고 가정하지 않는다. 시세와 체결을 명시적으로 입력하고, 독립된 모의 원장에 저장한다. 아래 명령은 `mock` 전용이며 KIS 환경에서는 거부한다.

```powershell
python -m scripts.us_paper_simulator quote AAPL NASD 100.00
```

미국 정규장에 화면에서 동기화 후 지정가 주문을 제출한다. 시세가 만료되면 새 모의 시세를 입력한다. 제출된 주문 상세의 **브로커 주문번호**로 체결을 주입한다.

```powershell
python -m scripts.us_paper_simulator fill <broker-order-id> 1 99.50 --commission 0.10 --tax 0
```

동기화 후 부분체결·보유 귀속·USD 현금 변화를 확인한다. 이 입력은 실측 시세나 KIS 체결이 아니며 성능 근거로 사용할 수 없다. 기동 중 앱과 별도 CLI가 같은 계좌 writer 잠금을 동시에 가질 수 없으므로, 잠금 오류가 나면 앱을 종료한 뒤 입력하고 다시 시작한다. 테스트에서는 고정 시각과 격리 DB를 사용해 장외에도 시나리오를 재현한다.

## KIS 미국 전용 모의 연결

`MAPS_US_BROKER_MODE=kis_paper`로 지정하고 미국 전용 모의 계좌의 `MAPS_US_APP_KEY`, `MAPS_US_APP_SECRET`, `MAPS_US_ACCOUNT_NO`, `MAPS_US_ACCOUNT_PRODUCT_CODE`를 별도로 설정한다. 국내 계좌나 키로 대체하지 않는다. 호스트는 KIS 모의 서버로 제한하며 실서버 주문 TR은 거부한다.

- REST 가격 응답만으로 거래 시각을 추정하지 않는다. HDFSCNT0의 거래 시각과 가격을 확인한 경우만 주문 시세로 사용한다.
- 모의환경에서 확인할 수 없는 수수료·세금은 미확정으로 남긴다. 체결 비용이 미확정이면 후속 신규 매수가 차단될 수 있다.
- 코드 테스트와 계좌 연동 검증은 다르다. 실제 계좌의 API 권한, WebSocket 구독, 주문·취소·체결 결과는 전용 자격증명을 사용한 별도 검증이 필요하다.
- 공식 계약 근거: [해외주식 예제](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock), [해외 실시간 체결 예제](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/delayed_ccnl).

## 중단과 복구

KIS 거래일이 지난 주문의 취소는 원주문 일자를 API 요청에 전달할 수 없어 차단한다. 같은 주문번호가 다른 거래일에 재사용되는 상황에서 잘못된 주문을 취소하지 않기 위한 제한이다.

- `신규 매수 중단`은 신규 매수만 막는다. 기존 주문 취소나 보유분 청산을 수행하지 않는다.
- `중단 해제`는 수동 중단만 해제한다. 데이터·한도·계좌 대사 차단은 유지한다.
- 결과 불명 주문은 다시 제출하지 않는다. 예약을 보존한 채 정확한 브로커 식별자로 확인한다.
- 취소 요청은 취소 완료가 아니다. 대사에서 최종 상태를 확인할 때까지 예약을 유지한다.
- 미귀속 보유분을 자동 편입하거나 매도하지 않는다.
- 이 구현은 단일 호스트 writer 잠금을 사용한다. 여러 호스트에서 동일 계좌를 실행하지 않는다.
- 미확정 주문이 있는 데이터베이스의 원장 테이블을 삭제하거나 마이그레이션을 되돌리지 않는다.

## 전략 추가 경계

전략은 공통 `Order`와 `ExecutionContext`를 생성하고 미국 실행 서비스에 전달한다. 브로커를 직접 호출하지 않는다. 전략 출처는 기본 차단되며 등록된 서버 정책, 인스턴스, 버전, 파라미터와 판단 시각 근거가 필요하다. 테스트용 전략 생산자로 이 계약을 검증하지만 운영 전략 활성화 기능은 이번 버전에 포함하지 않는다.

## 검증 명령

```powershell
python -m pytest tests/test_us_execution_core.py tests/test_kis_us_adapter.py tests/test_us_trading_rules.py tests/test_us_trading_api.py tests/test_us_runtime.py tests/test_us_simulator.py tests/test_us_trading_ui.py --tb=short
python -m pytest tests/test_execution_safety.py tests/test_order_manager.py tests/test_order_manager_sync.py tests/test_settings.py tests/test_auth.py tests/test_migrations.py tests/test_docs_index.py --tb=short
```

실제 수행 결과와 미검증 항목은 [ExecPlan](superpowers/plans/2026-10-16-us-paper-trading.md)에 기록한다.
