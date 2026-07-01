import { useCallback, useState } from 'react'
import { fetchPortfolioHistory, loadCachedHistory, type PortfolioHistory } from '../api'
import { AuthError } from '../auth'

/**
 * 홈 탭 자산 추이(portfolio-history) 로딩 훅. 콜드스타트에는 로컬 캐시를 즉시 노출한다.
 * 추이 차트는 보조 정보이므로 401(AuthError) 외의 오류는 무시하고 마지막 캐시를 유지한다.
 */
export function useHistory(requireLogin: () => void) {
  const [history, setHistory] = useState<PortfolioHistory | undefined>(() => loadCachedHistory())

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await fetchPortfolioHistory())
    } catch (reason) {
      if (reason instanceof AuthError) requireLogin()
      /* 그 외 오류는 무시하고 마지막 캐시를 유지 */
    }
  }, [requireLogin])

  return { history, loadHistory }
}
