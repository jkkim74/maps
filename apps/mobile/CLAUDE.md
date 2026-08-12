# apps/mobile/

Vite + React + Capacitor 모바일 클라이언트. 서버 API 를 그대로 쓰는 **읽기·모니터링 중심**
앱이다. 매매 계획 생성은 웹 대시보드에서만 한다.

## 명령

```powershell
cd apps/mobile
npm install
npm run dev        # Vite 개발 서버 (/api → 127.0.0.1:8000 프록시)
npm test           # vitest run
npm run cap:sync   # build + cap sync (네이티브 프로젝트 반영)
npm run build:apk:release   # build → sync → 서명 → gradle release
```

## 구조

```
src/
├── App.tsx / main.tsx      # 라우팅·부트스트랩
├── api.ts                  # 서버 호출 (모든 fetch 의 단일 경로)
├── auth.ts                 # 세션 로그인
├── push.ts                 # FCM 네이티브 푸시 등록
├── config.ts               # API_BASE — PROD_DEFAULT 가 여기 있다
├── format.ts               # 숫자·날짜 표시
├── screens/                # Home / Watchlist / Orders / Alerts / Risk / Login
├── components/             # Kpi, HoldingCard, TrendChart, AlertList, KillSwitchPanel, Empty
└── hooks/                  # useAuth, useSummary, usePicks, useOrderPreview,
                            # useKillSwitches, useHistory, usePolling
```

테스트는 소스 옆에 둔다 (`api.test.ts`, `auth.test.ts`, `App.test.tsx` …), vitest + jsdom.

## 🔴 도메인은 APK 안에 구워진다

`src/config.ts` 의 `PROD_DEFAULT` 가 프로덕션 빌드의 기본 오리진이다
(`VITE_API_BASE_URL` 로 덮어쓸 수 있다). **서버를 재배포해도 이미 설치된 APK 는 구 도메인을
계속 호출한다** — 도메인이 바뀌면 반드시 재빌드·재설치해야 한다(2026-07-29 `magable.kr`
이전 때 기존 APK 가 남의 서버를 호출하며 먹통이 됐다).

## 그 밖

- `google-services.json` 은 FCM 설정이다. **커밋하지 않는다**
- `keystore.properties.example` 만 저장소에 있고 실제 서명 키는 로컬에만 둔다
- 릴리스 절차는 `apps/mobile/RELEASE.md`
- 서버 쪽 대응 라우터는 `maps/api/mobile.py` (`/api/v1/mobile`)
