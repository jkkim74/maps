// 모바일 앱 인증 — 서버 세션 쿠키(SameSite=Lax)는 다른 오리진의 앱에서 전송되지 않으므로
// itsdangerous 서명 토큰을 받아 `Authorization: Bearer` 로 사용한다.
//
// 토큰 저장은 현재 localStorage(웹/웹뷰 모두 동작). 네이티브 단계에서 @capacitor/preferences
// 같은 보안 저장소로 교체할 수 있도록 storage 접근을 이 모듈로 격리한다.

import { API_BASE } from './config'

const TOKEN_KEY = 'maps.auth.token'

/** 401(인증 필요)을 일반 오류와 구분하기 위한 전용 에러. */
export class AuthError extends Error {}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* 저장 불가 환경은 무시 — 세션 동안 메모리 토큰만 사용 */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

/** 공유 비밀번호로 로그인하고 토큰을 저장한다. 실패 시 AuthError. */
export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/mobile/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (res.status === 401) {
    throw new AuthError('아이디 또는 비밀번호가 올바르지 않습니다.')
  }
  if (!res.ok) {
    throw new Error(`로그인 실패 (${res.status})`)
  }
  const data = (await res.json()) as { token: string; username: string }
  setToken(data.token)
}
