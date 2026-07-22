# HANDOFF

> 작성일: 2026-07-22 (KST) · 작성자: 세션 에이전트 (회사 PC, 키 `D:\ssh_maps\`)
> 주제: 모바일 앱 3기능 추가·배포 + 앱 시작 크래시(Firebase) 해결 + 워치리스트 미증가 원인 규명
> 이전 핸드오프(완료목록 분리, 7/14): git 이력 `2c020c2`·`f1e2db2` 참고.

## Goal

모바일 앱 개선 3건(예정 주문·워치 현재가·장세 배너)을 구현·배포하고, 앱 설치 후 발생한
시작 크래시를 해결한다. 부수적으로 "며칠째 워치 종목이 안 늘어난다"는 사용자 관찰의 원인을 규명한다.

## 운영 환경

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`, `https://magable.kr`.
브로커 **KIS 모의투자(paper)** 계좌 `50185813`. 운영 DB PostgreSQL(`sudo -u postgres psql -d maps`
또는 앱 venv로 `SessionLocal`). **SSH 키는 PC마다 다름**: 이 PC `D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem`.

## Current Progress (2026-07-22, 전부 완료·배포됨)

### 1. 모바일 예정 주문 + 워치 현재가 + `live_eligible` (커밋 `574e985`, 배포됨)
- 백엔드: `PreviewOrderItem.live_eligible`(schemas.py) 추가 — 예정주문 항목이 실주문 대상
  (live_candidate/live)인지 모의(mock_candidate)인지 구분. `order_preview.py`에서 `promotions`로 판정.
- 모바일 주문 탭: 상단 "다음 거래일 예정 주문" 섹션(기존 `/api/v1/orders/preview` 소비) — 핵심만 표시
  + [실주문]/[모의] 뱃지. `api.ts`/`useOrderPreview.ts`/`OrdersScreen.tsx`/`format.ts`/`styles.css`.
- 모바일 워치 탭: 현재가를 **체결 전에도** 표시(백엔드는 이미 미체결 픽에 current_price 채움 — 순수 프론트
  렌더 버그였음). `WatchlistScreen.tsx`. `HoldingDetail.tsx`는 원래도 정상.

### 2. 모바일 홈 장세 배너 (커밋 `c5359fc`, 배포됨)
- 백엔드: `/api/v1/mobile/summary`에 `regime` 블록 추가(`mobile.py` `MobileRegime`/`_mobile_regime`).
  **`latest_applied_regime(db, today)`로 `market_regime_log` 인덱스 1회 조회**(실시간 pykrx 재계산 없음).
  운영 검증: `강세 · 주봉 통과 · 변동성 높음 · 상승 6/8`(2026-07-22) 반환 확인.
- 모바일: HomeScreen 최상단 장세 배너(강세/혼조/약세). 웹 색상 규칙 미러(`regimeTone`): 주봉fail→빨강,
  strong→녹, weak→앰버, else→파랑. `.status-banner.warn/.info` CSS 추가.
- 테스트: `test_mobile_api.py`(regime 매핑) 추가. **전체 415 passed**.

### 3. 앱 시작 크래시 해결 (Firebase google-services.json 누락)
- 증상: 설치 후 실행 즉시 "MAPS에 오류 발생 — 앱을 종료했습니다"(네이티브 크래시, 로그인 화면 전).
- 원인: `@capacitor/push-notifications`가 `firebase-messaging` 포함 → `FirebaseInitProvider`가 시작 시
  `google_app_id`를 못 찾아 크래시. **`apps/mobile/android/app/google-services.json` 누락**이 원인.
- 해결: 사용자가 Firebase 프로젝트 `maps-bc07c`의 kr.maps.mobile용 google-services.json 제공 → 해당
  경로 배치 후 재빌드(`processDebugGoogleServices` 정상) → 크래시 소멸 확인.

### 4. APK 빌드 (디버그)
- 산출물: `apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk` (7.4MB),
  복사본 `C:\Users\jack\Downloads\maps-debug-20260722.apk`. 최신 커밋 `c5359fc` 전체 포함, 운영 API 연결.
- **모바일 UI는 systemd 배포로 안 나감** — APK 재빌드/사이드로드 필요. 백엔드 필드는 배포로 반영됨.

### 5. 워치리스트 미증가 원인 규명 (조치 불필요 — 정상 동작)
- **핵심: analyze는 이미 매 거래일 자동 실행 중.** `/etc/cron.d/maps-analyze`
  (`0 16 * * 1-5 ubuntu bash /opt/maps/scripts/run_analyze_cron.sh`) → `claude -p "/analyze"`
  (서버에 claude CLI v2.1.197 설치·구독 OAuth 인증). 로그 `/opt/maps/logs/analyze_cron_*.log`.
- **미증가 이유 = 게이트 전량탈락.** `analysis_run` 7/8~7/22 매일 `status=completed, picks=0`.
  파이프라인 정상 완료하나 안전마진·R:R·기술 게이트를 다 통과하는 셋업이 현 방어장에 안 나옴.
  예 7/22: 현대해상(001450) 안전마진 25.6% 통과했으나 **trade-planner R:R 0.34로 최종 탈락**.
- **R:R 게이트 = ≥ 2.0** (`.claude/agents/trade-planner.md:28,40,65`, 운영은 `/opt/maps/.claude/...`).
  손익분기 승률 33%인 검증-우선 표준값. **사용자 결정: 2.0 그대로 유지**(변경 없음).

## What Worked
- 병렬 Explore 에이전트로 백엔드/모바일/웹을 동시 조사 → 예정주문·장세 데이터 소스 빠르게 특정.
- 장세 데이터는 `/api/v1/market`(실시간 재계산) 대신 **`market_regime_log` 로그 조회**가 위젯에 적합.
- 크래시 진단: logcat 없이도 `google-services.json` 부재 + 빌드 매니페스트에 FirebaseInitProvider 존재로 확정.
- 운영 로그(`analysis_run` + cron 로그 tail)로 "자동화 이미 있음 + 게이트 탈락" 원인을 데이터로 확정.

## What Didn't Work / 주의
- **초기 오진**: "analyze 수동실행이라 미실행"으로 판단 → **틀림**. 이미 cron 자동 실행 중이었음.
  교훈: `crontab -l`은 `/etc/cron.d/`를 안 보여줌 → `/etc/cron.d/` 직접 확인 필수.
- **APK 빌드는 JDK 21 필요**(JAVA_HOME 기본 17). `JAVA_HOME=...Adoptium\jdk-21...` 지정 후 gradlew.bat.
- **`google-services.json`·`android/`는 gitignore** — 커밋 안 됨. 새 PC/체크아웃마다 재배치해야 크래시 방지.
- `.env`(KRX_ID/PW 등 시크릿) 커밋 금지 — `git add -u`만 사용(tracked만).
- 운영 DB .env 직접 grep은 분류기 차단됨 → 앱 venv `SessionLocal`로 조회하면 시크릿 노출 없이 가능.

## Next Steps
0. **새 APK 폰 설치·확인** — 홈 "장세·강세" 배너, 주문 탭 예정주문, 워치 체결 전 현재가 육안 확인.
1. **(선택) 게이트 튜닝은 보류** — R:R 2.0 유지 결정됨. 픽을 늘리려면 스케줄이 아니라 게이트(R:R/안전마진/섹터)
   조정이나, 검증-우선 철학상 신중. 현 0건은 정상 신호.
2. **모바일 완료 탭(익절/손절)** 미반영(7/14 handoff 이월) — `WatchlistScreen.tsx`에 `?state=CLOSED` 탭.
3. **서명 릴리스 APK**(스토어/정식 배포용) 필요 시 `npm run build:apk:release`(RELEASE.md, keystore 필요).
4. 이월 항목: 매도 만료율 조사, KIS 90020000 장외 경고, `/opt/stock_report` 버전관리, 네트워크 테스트 mock화.

## 핵심 파일 맵
- 모바일: `apps/mobile/src/{api.ts,format.ts,App.tsx}`, `screens/{HomeScreen,OrdersScreen,WatchlistScreen}.tsx`,
  `hooks/useOrderPreview.ts`, `styles.css`. Firebase: `apps/mobile/android/app/google-services.json`(gitignore).
- 백엔드: `maps/api/mobile.py`(regime 블록), `maps/ops/order_preview.py`+`maps/api/schemas.py`(live_eligible),
  `maps/market/regime_history.py`(`latest_applied_regime`).
- analyze 자동화(서버): `/etc/cron.d/maps-analyze`, `/opt/maps/scripts/run_analyze_cron.sh`,
  `/opt/maps/.claude/{commands/analyze.md,agents/*.md}`(R:R 등 게이트 규칙), `scripts/load_analysis_picks.py`.
- APK 빌드: `apps/mobile/RELEASE.md`, JDK21 필요. 디버그=`gradlew.bat assembleDebug`.
- 운영 접속: `ssh -i D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem ubuntu@3.37.117.246`, 앱 루트 `/opt/maps`.
