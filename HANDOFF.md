# HANDOFF

> 작성일: 2026-07-04 (토, KST) · 작성자: 이전 세션 에이전트
> 주제: 운영 오류(KIS 접속 실패) 원인 규명 + 리스크 보유종목 미표시 수정 3종 배포
> 이전 핸드오프(텔레그램/모바일 앱): git 이력 `d5f2fa8` 이전의 HANDOFF.md 참고.

## 운영 환경 (재확인됨)

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, 도메인 `https://magable.kr`.
**SSH 키 실제 경로는 `D:\maps\LightsailDefaultKey-ap-northeast-2.pem`** (이전 핸드오프의
`D:\ssh_maps\`는 현재 존재하지 않음 — CLAUDE.md가 맞다). 운영 DB는 PostgreSQL
(`sudo -u postgres psql -d maps`). 운영 `.env`: `MAPS_ENV=production`, `MAPS_AUTH_ENABLED=true`,
브로커는 **KIS 모의투자(VTS)** 계좌 `50185813`.

## 이번 세션에서 규명한 사실

1. **"Broker account balance unavailable" 알림** = KIS 모의투자 서버
   (`openapivts.koreainvestment.com:29443`) 접속 실패. 금 7/3 19:03까지는 응답했고
   (그때는 EGW00201 레이트리밋), **토 7/4 11:35부터 Connection refused** → 주말 서버 중단.
   시스템 문제 아님. 평일 장중 자동 회복.
2. **세방전지(004490) 보유종목 미표시** = 위와 같은 원인. 리스크·모니터 보유종목은
   KIS 잔고 API 실시간 조회였는데, 실패 시 예외를 삼키고 빈 목록을 반환했음
   (`risk.py` 구 `_broker_holdings`). 004490은 7/3 15시부터 전략매매 엔진이
   BOUGHT로 정상 추적 중(analysis_pick).
3. **대시보드 "최근 알림 (24H)"** 라벨과 달리 kill_switch_log 최신 5건을 시간 필터
   없이 표시 → 오래된 donchian_v2 알림이 계속 노출되고 있었음.
4. **EGW00201(초당 호출한도)** 이 평일에도 간헐 발생(7/1·7/2·7/3 각 1~2회):
   리스크 페이지 1회 로드가 inquire-balance 2회 + 대시보드 1회 → 모의투자 한도(2/s) 초과.

## 적용·배포된 수정 (커밋 `df44d04`, 운영 반영 완료)

1. **브로커 실패 폴백** — `maps/api/risk.py`: `_broker_holdings`가 5-튜플
   `(holdings, max_exposure, count, broker_status, broker_error)` 반환.
   실패 시 `_fallback_holdings()`(analysis_pick state=BOUGHT, 진입가는 entry 주문
   체결가 우선, 현재가는 최신 OHLCV 종가, exposure=0.0)로 폴백.
   `RiskResponse`에 `broker_status`(ok|fallback|unavailable)/`broker_error` 추가.
   `static/js/app.js` loadRisk가 경고 배너 표시(+`esc()` HTML 이스케이프 헬퍼 추가).
2. **KIS 잔고 5초 캐시** — `maps/execution/kis_adapter.py`: 모듈 레벨
   `_BALANCE_CACHE`(키는 토큰 캐시와 동일, TTL 5s, `time.monotonic`).
   `place_order`/`cancel_order` 후 `_invalidate_balance_cache()`.
   어댑터가 `get_broker()`마다 새로 생성되므로 **인스턴스가 아닌 모듈 레벨**이어야 함.
3. **알림 24H 필터** — `maps/api/dashboard.py` `_dashboard_alerts`: `created_at >=
   now(UTC)-24h` 필터, 타임스탬프 `%m-%d %H:%M`.

테스트: 신규 5건 포함 **371 passed** (아래 '주의'의 네트워크 의존 2파일 제외).
`tests/test_kis_adapter.py`의 `settings` 픽스처가 `_BALANCE_CACHE.clear()`도 수행.

## What Worked / 주의

- **운영 로그가 근거**: `sudo journalctl -u maps` grep으로 오류 시작 시점·원인 코드
  (Connection refused vs EGW00201)를 구분해 확정. 로컬 `logs/maps.log`는 pytest가
  오염시키므로 운영 진단에 쓰지 말 것.
- **`tests/test_analysis_picks_api.py`·`tests/test_mobile_auth.py`는 실 KIS API에
  의존** → KIS 모의서버가 내려간 주말/야간엔 ConnectTimeout으로 12건 실패(코드 무관).
  후속: mock 처리 권장. `!deploy`의 "테스트 실패 시 중단" 판단 시 이 2파일은
  환경 요인임을 감안할 것.
- `apps/mobile/google-services.json`이 untracked로 존재(Firebase 키 포함) —
  **커밋 금지**. `git add -u`만 사용.
- 자동 승인 모드에서 운영 서버 SSH/psql은 정책상 차단될 수 있음 — journalctl 로그
  조회는 허용됐고, DB 직접 조회는 사용자 승인 필요.

## Next Steps

1. **월요일 장중 확인**: 리스크·모니터 보유종목이 실시간(broker_status=ok)으로
   세방전지를 표시하는지, EGW00201 재발이 사라졌는지 운영 로그로 확인.
2. **네트워크 의존 테스트 mock 처리** — test_analysis_picks_api / test_mobile_auth가
   실 KIS 호출 없이 돌도록 (conftest에서 broker mock 강제 등).
3. **펀더멘털 백필 재개 예정일 2026-06-22이 이미 지남** — 상태 확인 필요(메모리 노트 참고).

## 핵심 파일 맵 (이번 변경)

- `maps/api/risk.py` — `_broker_holdings`(5-튜플), `_fallback_holdings`, `_latest_close`
- `maps/api/schemas.py` — `RiskResponse.broker_status/broker_error`
- `maps/api/dashboard.py` — `_dashboard_alerts` 24h 컷오프
- `maps/execution/kis_adapter.py` — `_BALANCE_CACHE`/`_invalidate_balance_cache`
- `static/js/app.js` — `esc()`, loadRisk 브로커 배너
- 테스트: `tests/test_risk_api.py`, `tests/test_dashboard_api.py`, `tests/test_kis_adapter.py`
