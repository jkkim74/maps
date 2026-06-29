// API 베이스 오리진 결정.
//
// - 개발(`npm run dev`): 빈 문자열 → Vite 프록시가 `/api`를 127.0.0.1:8000으로 전달.
// - 프로덕션 빌드(`vite build`, Capacitor 네이티브): 프록시가 없으므로 절대 오리진이 필요.
//   기본값은 운영 서버(magable.kr)이며, 빌드 시 VITE_API_BASE_URL 로 덮어쓸 수 있다.
const PROD_DEFAULT = 'https://magable.kr'

export const API_BASE: string =
  import.meta.env.VITE_API_BASE_URL || (import.meta.env.PROD ? PROD_DEFAULT : '')
