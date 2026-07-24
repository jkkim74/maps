# HANDOFF

> 작성일: 2026-07-24 (KST) · 작성자: 세션 에이전트 (집 PC, 키 `D:\maps\`)
> 주제: 승격 데드락 해소 — mock_candidate 전략이 모의 계좌에서 주문을 내도록 열고,
>       `mock_months` 트랙레코드를 실제로 축적·게이트에 전달
> 이전 핸드오프(모바일 3기능·Firebase 크래시, 7/22): git 이력 `574e985`·`c5359fc` 참고.

## Goal

"오늘 주문예정이던 SK이터닉스가 왜 주문 안 됐나" 조사 → 근본 원인이 **승격 단계 데드락**임을
규명하고 해소한다.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813` (`kis_real_trading=False`).
운영 DB PostgreSQL(`sudo -u postgres psql -d maps`). **SSH 키는 PC마다 다름**: 이 PC(집) `D:\maps\`, 회사 PC `D:\ssh_maps\`.

## Current Progress (2026-07-24, 전부 완료·배포됨 — 커밋 `226a468`)

### 1. 원인 규명 — 두 개의 게이트가 서로를 막고 있었음

- 7/24 08:55 `order_cycle` 로그: `submitted_buy_orders=0, **skipped_buy_orders=0**`.
  skipped=0이 결정적 — 스킵된 게 아니라 **후보 리스트가 비어 루프가 0회** 돌았다.
- 원인: 예정목록과 실주문의 **승격단계 게이트가 서로 달랐음**.
  | | 대상 단계 |
  |---|---|
  | 모바일 예정주문 `order_preview.py` | `mock_candidate` + `live_candidate` + `live` |
  | 실제 08:55 주문 `scheduler._order_candidates` | `live_candidate` + `live` **만** |
  운영 DB의 6개 전략이 **전부 `mock_candidate`** → preview엔 뜨지만 주문은 0건.
- 더 근본: `live_candidate` 승격 조건 `mock_months ≥ 3`인데 **`mock_months`를 채우는 코드가
  코드베이스에 아예 없었다**(항상 `metrics.get("mock_months", 0.0)` → 0.0). 게다가 mock_candidate는
  주문 자체가 안 나가므로 실적을 쌓을 수도 없었음 — **주문하려면 승격 필요, 승격하려면 주문 필요**.

### 2. 조치 (커밋 `226a468`, 배포·검증 완료)

- `settings.py`: **`is_paper_account`** 프로퍼티 신설 — `mock` 브로커이거나 `kis` + `kis_real_trading=False`.
  kiwoom은 모의 판별 플래그가 없어 보수적으로 False.
- `scheduler._order_candidates`: **모의 계좌에서만** `eligible_stages`에 `mock_candidate` 추가.
  실계좌 전환 시 자동으로 다시 닫힌다.
- `scheduler._mock_track_months(db, ref_date)`: `order_log`에서 전략별 **최초 체결(fill_qty>0) BUY**
  이후 경과 개월 계산 → `_evaluate_promotions`가 `metrics["mock_months"]`로 게이트에 전달.
  미체결·취소는 운용 실적이 아니므로 제외.
- `order_preview.py`: `live_eligible`을 주문 경로와 **동일 규칙으로 미러링**. 안 하면 실제로 주문이
  나가는 항목에 `[모의]` 뱃지가 붙어 정반대 오해를 준다.
- `order_manager._order_log_mode()`: `order_log.mode`가 `maps_live_trading_enabled`만 보고
  페이퍼 체결까지 `live`로 찍던 라벨링 버그 수정 → **실제 돈이 오간 주문만 `live`**.
- 테스트 4건 추가(모의/실계좌 단계 분기, `_mock_track_months` 체결만 카운트, preview 뱃지 실계좌,
  mode 라벨 4케이스 파라미터). **전체 423 passed**.

### 3. 운영 검증 (배포 직후)

```
broker_mode=kis, kis_real_trading=False, live_enabled=True, is_paper_account=True  ✅
_order_candidates → SK이터닉스(475150, donchian_v2) 포함 다수 반환 (배포 전엔 [])
_mock_track_months → {}  (체결된 BUY가 아직 없음 — 월요일부터 카운트 시작)
```

### 4. `order_log.mode` 백필 (운영 DB)

- 42건 전부 `live`(오라벨) → `mock`. 기간 2026-05-25~07-13, 전 기간 페이퍼 계좌였음.
- **백업 테이블 `order_log_backup_20260724` 보존** (원본 42행). 되돌리려면:
  ```sql
  UPDATE order_log o SET mode = b.mode
    FROM order_log_backup_20260724 b WHERE o.id = b.id;
  ```
- 며칠 뒤 문제없으면 `DROP TABLE order_log_backup_20260724;` (42행이라 방치해도 무방).

### 5. 워치리스트 실시간 시세 (커밋 `99e7d3d`, 배포·검증 완료)

- 증상: 워치리스트 현재가가 실제와 안 맞음. 원인은 **미보유(WATCH) 종목이 실시간 시세 소스가 없어
  `historical_ohlcv` 최신 일봉 종가로 폴백**하던 것 — 장중엔 어제 종가가 뜬다. KIS 잔고 조회는
  보유 종목 시세(`prpr`)만 준다.
- 조치: `BrokerAdapter.get_current_prices(tickers)` 선택 메서드 추가(기본 no-op).
  `KISAdapter`가 **inquire-price(FHKST01010100, 모의/실 공통 TR)** 종목당 1회 호출로 구현.
  `analysis_picks._current_prices` 폴백 순서: 보유=잔고 라이브 → 미보유=KIS 실시간 조회 →
  조회 실패=일봉 종가 → 없으면 None. 조회 실패는 로깅 후 흡수해 목록이 죽지 않는다.
- **곁다리 발견/수정**: 이 PC에 KIS `.env`(prod paper)가 있어 **테스트가 실제 KIS API를 치고 있었다.**
  `conftest.py`에 `MAPS_BROKER_MODE=mock` 강제 추가 → 어느 테스트든 실 브로커 미접촉. **425 passed.**
- 운영 검증: `_current_prices(db, ["002350"]) → {'002350': 6930.0}` (저장된 7/23 종가 7030 대신 라이브).
  **모바일 워치리스트는 백엔드 실시간 소비 → APK 재빌드 불필요, systemd 배포로 반영됨.**
- 주의(YAGNI로 보류): 워치 종목당 KIS 1회 호출. 현재 1건이라 무해하나 수십 건이면 모의투자
  초당 호출한도(EGW00201) 근접 가능 — 그때 배치/캐시 도입. 지금 필요 없음.

## What Worked

- **`skipped=0` vs `skipped>0` 구분이 진단의 핵심이었다.** "스킵"이면 필터 조건 문제, "0회 루프"면
  후보 리스트 자체가 빈 것 — 후자로 좁히자 `_order_candidates`의 단계 필터로 바로 도달.
- 두 경로(`order_preview` / `_order_candidates`)를 나란히 놓고 비교 → 게이트 불일치 즉시 발견.
- 조치 후 운영 서버에서 `_order_candidates`를 직접 호출해 SK이터닉스 포함을 눈으로 확인.
  systemd 재기동만 믿지 않고 실제 함수 반환값으로 검증한 것이 유효했다.
- 감사 테이블 UPDATE 전 `CREATE TABLE ... AS SELECT` 백업을 같은 트랜잭션에 묶음.

## What Didn't Work / 주의

- **이 PC(집)의 `.venv`가 깨져 있었다 → 재생성 완료.** 기존 `pyvenv.cfg`가 `C:\Python312`(존재하지 않음)
  + `D:\workspace2\maps` 기준이라 `.venv\Scripts\python.exe`가 실행 불가였다. `C:\ProgramData\anaconda3\python.exe`
  (3.12.4)로 재생성 + `pip install -r requirements.txt` → **423 passed 확인**. 이제 CLAUDE.md 안내대로 쓰면 된다.
  다른 PC에서 같은 증상이 나오면 동일하게 `.venv` 삭제 후 재생성할 것.
- **PowerShell에서 ssh + psql 중첩 따옴표는 거의 항상 깨진다.** Bash 툴(Git Bash)에서
  `'...'"'"'...'"'"'...'` 또는 heredoc(`<< "SQL"`)을 쓸 것.
- **`analyze` 픽 0건과 이번 주문 0건은 완전히 다른 파이프라인이다.** 혼동 금지:
  - `analyze`(cron 16:00) → `analysis_pick` 테이블 → 워치리스트. 게이트는 R:R ≥ 2.0. **7/8 이후 0건.**
  - 스케줄러(16:50 후보생성 → 08:55 주문) → `candidate_snapshot` → `order_log`. 이번에 고친 쪽.
- `journalctl`에 `broker_sync`가 60초마다 찍혀 grep이 묻힌다 → `| grep -v broker_sync` 필수.

## Next Steps

1. **7/27(월) 08:55 확인 (최우선)** — `order_cycle` 로그에 `submitted_buy_orders > 0` 나오는지.
   지금까지 계속 0이었으므로 이게 승격 데드락 수정(`226a468`)의 실질 검증이다.
   장세 `mixed` 기준 상한은 **하루 2건**(`max_orders = max(1, round(3 × entry_limit_ratio))`).
   확인 명령: `sudo journalctl -u maps --no-pager | grep "order_cycle: success" | tail -1`
   - 0이면 추가 원인(장세 차단·entry_signal 미발생 등) 조사.
   - **자동화 불가 메모**: 클라우드 예약 에이전트(`/schedule`)로 이 체크를 못 건다 — prod는 SSH
     키(로컬 전용)로만 접근되고 대시보드는 인증 게이트다. 서버 cron이나 Slack 알림은 가능(미구현).
2. **~2026-10월말: `mock_months ≥ 3` 충족 시점** 재확인. 단 **점수 34.7 < 임계값 75는 그대로**라
   승격은 여전히 안 된다 — Live Small 차단만 풀린다. 점수 개선은 별도 과제.
3. **새 APK 폰 설치·확인**(7/22 이월) — 홈 장세 배너, 주문 탭 예정주문, 워치 체결 전 현재가.
   모바일 UI는 systemd 배포로 안 나감, APK 재빌드 필요(JDK 21).
4. **모바일 완료 탭(익절/손절)** 미반영(7/14부터 이월) — `WatchlistScreen.tsx`에 `?state=CLOSED` 탭.
5. **서명 릴리스 APK** 필요 시 `npm run build:apk:release`(RELEASE.md, keystore 필요).
6. 이월 항목: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트 mock화.
7. `apps/mobile/google-services.json`이 워크트리 루트에 untracked로 있음 — 크래시 방지에 필요한
   위치는 `apps/mobile/android/app/google-services.json`. 이 PC에서 APK 빌드하려면 옮겨야 함.

## 핵심 파일 맵

- 승격 데드락(이번 변경): `maps/common/settings.py`(`is_paper_account`),
  `maps/ops/scheduler.py`(`_order_candidates` 단계 게이트, `_mock_track_months`, `_evaluate_promotions`),
  `maps/ops/order_preview.py`(`order_stages`/`live_eligible`), `maps/execution/order_manager.py`(`_order_log_mode`).
- 승격 규칙: `maps/promotion/gate.py`(`_check_live_small_readiness`, `_MIN_MOCK_MONTHS_FOR_LIVE_SMALL=3`),
  `maps/common/constants.py`(`TRADEABILITY_THRESHOLDS`, `STRATEGY_GROUP_MAP`).
- 테스트: `tests/test_candidate_snapshot_scheduler.py`, `tests/test_order_preview.py`,
  `tests/test_order_manager.py`.
- 모바일: `apps/mobile/src/{api.ts,format.ts,App.tsx}`, `screens/{HomeScreen,OrdersScreen,WatchlistScreen}.tsx`,
  `hooks/useOrderPreview.ts`, `styles.css`. Firebase: `apps/mobile/android/app/google-services.json`(gitignore).
- analyze 자동화(서버): `/etc/cron.d/maps-analyze`, `/opt/maps/scripts/run_analyze_cron.sh`,
  `/opt/maps/.claude/{commands/analyze.md,agents/*.md}`(R:R 등 게이트 규칙), `scripts/load_analysis_picks.py`.
- 운영 접속: `ssh -i D:\maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`, 앱 루트 `/opt/maps`.
