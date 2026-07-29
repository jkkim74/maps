// API 베이스 오리진 결정.
//
// - 개발(`npm run dev`): 빈 문자열 → Vite 프록시가 `/api`를 127.0.0.1:8000으로 전달.
// - 프로덕션 빌드(`vite build`, Capacitor 네이티브): 프록시가 없으므로 절대 오리진이 필요.
//   기본값은 운영 서버(maps.magable.kr)이며, 빌드 시 VITE_API_BASE_URL 로 덮어쓸 수 있다.
//
// 이 값은 APK 안에 그대로 구워진다. 도메인이 바뀌면 재배포만으로는 안 되고
// 반드시 재빌드·재설치해야 한다 — 2026-07-29 magable.kr 이전 때 기존 APK 가
// 남의 서버를 호출하며 먹통이 됐다.
const PROD_DEFAULT = 'https://maps.magable.kr'

export const API_BASE: string =
  import.meta.env.VITE_API_BASE_URL || (import.meta.env.PROD ? PROD_DEFAULT : '')
