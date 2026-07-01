import { useCallback, useState } from 'react'
import { fetchSummary, loadCachedSummary, type MobileSummary } from '../api'
import { AuthError } from '../auth'

/**
 * 모바일 요약(summary) 로딩 훅. 콜드스타트에는 로컬 캐시를 즉시 노출하고,
 * `refresh()`로 서버 최신값을 가져온다. 401이면 `requireLogin()`, 성공이면 `clearRequireLogin()`.
 */
export function useSummary(requireLogin: () => void, clearRequireLogin: () => void) {
  const [data, setData] = useState<MobileSummary | undefined>(() => loadCachedSummary())
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchSummary())
      clearRequireLogin()
    } catch (reason) {
      if (reason instanceof AuthError) {
        requireLogin()
      } else {
        setError(reason instanceof Error ? reason.message : '데이터를 불러오지 못했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }, [requireLogin, clearRequireLogin])

  /** 로그아웃 시 캐시 표시값을 비운다. */
  const clear = useCallback(() => setData(undefined), [])

  return { data, error, loading, refresh, clear }
}
