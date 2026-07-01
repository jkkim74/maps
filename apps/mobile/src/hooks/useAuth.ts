import { useCallback, useState } from 'react'

/**
 * 로그인 필요 여부 게이트. 데이터 훅이 401(AuthError)을 만나면 `requireLogin()`을,
 * summary 갱신이 성공하면 `clearRequireLogin()`을 호출한다.
 */
export function useAuth() {
  const [needLogin, setNeedLogin] = useState(false)
  const requireLogin = useCallback(() => setNeedLogin(true), [])
  const clearRequireLogin = useCallback(() => setNeedLogin(false), [])
  return { needLogin, requireLogin, clearRequireLogin }
}
