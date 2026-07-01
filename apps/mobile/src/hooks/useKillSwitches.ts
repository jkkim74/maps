import { useCallback, useState } from 'react'
import { approveLiquidation, fetchKillSwitches, releaseKillSwitch, type KillSwitches } from '../api'
import { AuthError, getUsername } from '../auth'

/**
 * Kill-Switch 현황 로딩 + 청산 승인 / 해제 액션 훅.
 * 청산 승인은 실매도(고위험)이므로 경고 확인창을 반드시 거친다.
 */
export function useKillSwitches(requireLogin: () => void) {
  const [data, setData] = useState<KillSwitches>({ approvals: [], releases: [] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchKillSwitches())
    } catch (reason) {
      if (reason instanceof AuthError) requireLogin()
      else setError(reason instanceof Error ? reason.message : 'Kill-Switch 현황을 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }, [requireLogin])

  const onApprove = useCallback(
    (strategyId: string) => {
      const ok = window.confirm(
        `⚠️ 실거래 경고\n\n[${strategyId}] 보유 포지션을 시장가로 청산 승인합니다.\n` +
        `이 작업은 되돌릴 수 없습니다. 정말 진행할까요?`,
      )
      if (!ok) return
      setBusyId(strategyId)
      setMessage('')
      approveLiquidation(strategyId, getUsername())
        .then(() => { setMessage(`${strategyId} 청산 승인됨`); return load() })
        .catch((e) => {
          if (e instanceof AuthError) requireLogin()
          else setMessage(`실패: ${e instanceof Error ? e.message : '요청 오류'}`)
        })
        .finally(() => setBusyId(null))
    },
    [load, requireLogin],
  )

  const onRelease = useCallback(
    (strategyId: string) => {
      const ok = window.confirm(
        `[${strategyId}] Kill-Switch를 해제하면 신규 진입이 재개됩니다.\n청산이 완료됐는지 확인했나요?`,
      )
      if (!ok) return
      setBusyId(strategyId)
      setMessage('')
      releaseKillSwitch(strategyId, getUsername())
        .then(() => { setMessage(`${strategyId} Kill-Switch 해제됨`); return load() })
        .catch((e) => {
          if (e instanceof AuthError) requireLogin()
          else setMessage(`실패: ${e instanceof Error ? e.message : '요청 오류'}`)
        })
        .finally(() => setBusyId(null))
    },
    [load, requireLogin],
  )

  return { data, loading, error, busyId, message, load, onApprove, onRelease }
}
