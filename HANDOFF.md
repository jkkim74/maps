# HANDOFF

> 작성일: 2026-06-29 (KST) · 작성자: 이전 세션 에이전트
> 주제: ① 텔레그램 /analyze 알림 + 무장/무장해제 ② 모바일 앱 개선 로드맵(Phase 0~5)

## Goal

두 가지 묶음 작업을 완료했다.
1. **텔레그램 봇**: 매 거래일 16:00 cron의 `/analyze` 편입 결과를 텔레그램으로 푸시하고,
   각 종목 메시지의 **[무장][무장해제] 인라인 버튼**으로 arm/disarm을 원격 트리거.
2. **모바일 앱(`apps/mobile`) 실사용화**: "읽기 전용 MVP"를 인증·조작 가능한 실사용 앱으로.
   로드맵 Phase 0(인증)→1(네이티브/APK)→2(자동갱신)→3(워치/Kill-Switch)→5(테스트), Phase 4(푸시)는
   텔레그램으로 충족.

## Current Progress (전부 master 푸시 + 운영 반영 완료)

배포 서버: AWS Lightsail `3.37.117.246`, `/opt/maps`, systemd `maps`. **SSH 키 실제 경로
`D:\ssh_maps\LightsailDefaultKey-ap-northeast-2.pem`**(CLAUDE.md의 `D:\maps\`는 틀림). 운영 DB는
**PostgreSQL**(`sudo -u postgres psql -d maps`). 운영 `.env`는 `MAPS_ENV=production`, `MAPS_AUTH_ENABLED=true`.

커밋(시간순):
1. `94470e0` 텔레그램 알림+웹훅 — `TelegramNotifier`(`maps/ops/notifications.py`), 웹훅
   `POST /api/telegram/webhook`(`maps/api/telegram.py`, secret+chat 검증 후 기존 arm/disarm 재사용),
   `load_analysis_picks.py` 적재 후 푸시, `scripts/setup_telegram_webhook.py`.
2. `d8efaa1` 텔레그램 비운영 발송 가드 — `MAPS_ENV!=production`이면 실발송 차단(`maps_telegram_allow_nonprod`로 우회).
   **운영은 production이라 정상 발송.**
3. `3100b01` Phase 0 — 모바일 Bearer 토큰 인증(`maps/api/auth.py` `make/verify_mobile_token`, 게이트가
   세션 OR Bearer 허용), 공개 `POST /api/v1/mobile/login`. 앱 `auth.ts`/로그인 화면.
4. `1dc06c5` Phase 1 — 앱 프로덕션 API 베이스(`src/config.ts`: dev=프록시, prod=`https://magable.kr`).
5. `d50d1b2` Phase 3-a — 워치리스트 탭(무장/무장해제, 기존 `analysis-picks` 엔드포인트 재사용).
6. `0090bfb` Phase 3-b/2/5 — Kill-Switch 청산승인/해제(`live-monitor` 엔드포인트), 30초 자동폴링+요약
   캐시+미활용필드, **vitest 앱 단위 테스트 10건**.
7. `c39f99f` 버그픽스 — 워치리스트 **보유종목 체결가 표시**(앱 타입/화면이 `fill_price` 누락했던 것).

테스트: 백엔드 **352 passed**, 앱 **vitest 10 passed**. 폰에서 로그인·무장/무장해제·체결가 표시 실동작 확인됨.

### 텔레그램 운영 설정 (이미 적용됨)
- 봇 `@maps9352_bot`, **chat_id `8149176134`**(처음에 봇이름으로 잘못 들어가 chat not found → 교정).
- 웹훅 `https://magable.kr/api/telegram/webhook`(allowed_updates=callback_query), secret은 `/opt/maps/.env`.
- 변경 시: 토큰/chat/secret은 운영 `.env`에만, 웹훅 재등록은 서버에서 `python scripts/setup_telegram_webhook.py`.

## What Worked

- **기존 서버 로직 재사용**: arm/disarm(`analysis_picks.py`), kill-switch(`live_monitor.py` approve-liquidation/release),
  summary(`mobile.py`)를 그대로 호출 → 앱·텔레그램은 호출만, 서버 변경 최소.
- **세션 쿠키 대신 Bearer 토큰**: `same_site=lax`라 다른 오리진 앱은 쿠키 전송 불가 → `itsdangerous`(세션
  서명키 재사용) 토큰으로 우회. 게이트가 둘 다 허용.
- **APK 빌드**: JDK 21 설치(winget `EclipseAdoptium.Temurin.21.JDK`, 경로
  `C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot`). 빌드는 **bash에서 `export JAVA_HOME=...` +
  unix `./gradlew assembleDebug --no-daemon`**이 안정적.
- **운영 DB 직접 조회로 근거 확정** 후 수정(체결가 버그: order_log fill_price=48783 확인 → 앱 표시만 누락이었음).
- **정직한 범위 판단**: FCM/차트는 외부 의존(Firebase/서버 시계열)이라 가짜 코드 대신 보류·문서화.

## What Didn't Work / 주의

- **`cmd //c "set JAVA_HOME=... && gradlew.bat"`는 불안정**(release 21 에러 재발). → bash export + unix gradlew 사용.
- **`.env.*`는 `.gitignore`로 무시**(루트·`apps/mobile/.env.production` 포함, `apps/mobile/.env.example`만 예외).
  프로덕션 API 베이스는 `.env.production` 대신 **커밋 가능한 `src/config.ts`**로 처리.
- **`apps/mobile/android/`는 gitignore**(생성물). 커밋 안 함 — `npx cap add android`로 재생성.
- 텔레그램 chat_id는 **봇 이름이 아니라 숫자 ID**. 웹훅 활성 중엔 `getUpdates`가 비어 메시지 못 읽음 →
  `deleteWebhook` 후 사용자가 봇에 메시지 보내야 chat_id 획득 가능.
- 윈도우 Git Bash `curl -d`에 **한글 직접 입력은 cp949로 깨짐**(서버는 정상). 테스트 시 ASCII/파일 사용.

## Next Steps (미착수 후속 후보)

1. ~~**APK 산출물 관리/배포**~~ — **release 서명 빌드 설정 완료**(`apps/mobile/RELEASE.md`).
   `keystore.properties.example` 템플릿 + `scripts/apply-release-signing.mjs`(생성된
   `android/app/build.gradle`에 서명 config 주입, 멱등) + `scripts/gradle-release.mjs`(JDK21
   `assembleRelease`) + `npm run build:apk:release` 원커맨드. **남은 것: 사용자가 실제 keystore
   (`maps-release.jks`)와 store/key 비밀번호를 `apps/mobile/keystore.properties`에 채우기**(gitignore됨).
   산출물: `apps/mobile/android/app/build/outputs/apk/release/app-release.apk`.
2. **Phase 4 FCM 푸시** — 텔레그램으로 충족 중. 네이티브 푸시 원하면 Firebase 프로젝트+`google-services.json`+
   서버 FCM 키, 디바이스 토큰 등록 API, `notifications.py`에 `FcmNotifier` 필요.
3. **추이 차트** — 모바일 summary에 시계열 없음 → `portfolio_snapshot` 기반 시계열 엔드포인트(서버) 선행 필요.
4. **드릴다운 상세화면** — 주문/종목 클릭 시 상세. 현재는 목록만.
5. **앱 코드 품질** — `App.tsx` 단일 파일에 컴포넌트·상태 집중 → 화면/훅 분리 여지.

## 핵심 파일 맵

- 텔레그램: `maps/ops/notifications.py`(`TelegramNotifier`), `maps/api/telegram.py`(웹훅),
  `scripts/setup_telegram_webhook.py`, `scripts/load_analysis_picks.py`(편입 후 푸시).
- 모바일 인증: `maps/api/auth.py`(토큰·게이트), `maps/api/mobile.py`(`/login`, `/summary`).
- 앱: `apps/mobile/src/` — `App.tsx`(탭5: 홈/주문/리스크/워치/알림), `api.ts`(authedFetch·picks·kill-switch·캐시),
  `auth.ts`(토큰/username), `config.ts`(API 베이스), `*.test.tsx`(vitest), `vite.config.ts`(test 블록).
- 앱 빌드: `npm run build`(웹) → `npx cap sync android` → JDK21로 `./gradlew assembleDebug`.
- 조작 엔드포인트(재사용): `analysis_picks.py`(arm/disarm), `live_monitor.py`(approve-liquidation/release).
