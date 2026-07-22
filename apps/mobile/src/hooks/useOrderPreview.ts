import { useCallback, useState } from 'react'
import { fetchOrderPreview, type OrderPreview } from '../api'
import { AuthError } from '../auth'

/** 다음 거래일 예정 주문 미리보기 로딩 훅. 주문 탭 진입/폴링 시 로드한다. */
export function useOrderPreview(requireLogin: () => void) {
  const [preview, setPreview] = useState<OrderPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setPreview(await fetchOrderPreview())
    } catch (reason) {
      if (reason instanceof AuthError) {
        requireLogin()
      } else {
        setError(reason instanceof Error ? reason.message : '예정 주문을 불러오지 못했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }, [requireLogin])

  return { preview, loading, error, load }
}
