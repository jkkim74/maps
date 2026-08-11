import { useCallback, useState } from 'react'
import { armPick, disarmPick, fetchPicks, stopPickEntries, type AnalysisPick } from '../api'
import { AuthError } from '../auth'

/**
 * 분석 워치리스트 픽 로딩 + 무장/무장해제 액션 훅.
 * 무장은 자동매수를 켜므로 확인창을 거치고, 액션 후 목록을 다시 로드한다.
 */
export function usePicks(requireLogin: () => void) {
  const [picks, setPicks] = useState<AnalysisPick[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setPicks(await fetchPicks())
    } catch (reason) {
      if (reason instanceof AuthError) {
        requireLogin()
      } else {
        setError(reason instanceof Error ? reason.message : '워치리스트를 불러오지 못했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }, [requireLogin])

  const runAction = useCallback(
    async (pick: AnalysisPick, action: 'arm' | 'disarm' | 'stopEntries') => {
      setBusyId(pick.id)
      setMessage('')
      try {
        if (action === 'arm') await armPick(pick.id)
        else if (action === 'disarm') await disarmPick(pick.id)
        else await stopPickEntries(pick.id)
        const label = action === 'arm' ? '무장됨' : action === 'disarm' ? '무장해제됨' : '분할 매수 중단됨'
        setMessage(`${pick.name} ${label}`)
        await load()
      } catch (reason) {
        if (reason instanceof AuthError) requireLogin()
        else setMessage(`실패: ${reason instanceof Error ? reason.message : '요청 오류'}`)
      } finally {
        setBusyId(null)
      }
    },
    [load, requireLogin],
  )

  const onArm = useCallback(
    (pick: AnalysisPick) => {
      if (window.confirm(`${pick.name}을(를) 무장하면 자동매수가 활성화됩니다. 계속할까요?`)) {
        void runAction(pick, 'arm')
      }
    },
    [runAction],
  )
  const onDisarm = useCallback((pick: AnalysisPick) => void runAction(pick, 'disarm'), [runAction])
  const onStopEntries = useCallback(
    (pick: AnalysisPick) => {
      if (window.confirm(`${pick.name}의 미체결 분할매수를 중단할까요? 보유분 청산 감시는 계속됩니다.`)) {
        void runAction(pick, 'stopEntries')
      }
    },
    [runAction],
  )

  return { picks, loading, error, busyId, message, load, onArm, onDisarm, onStopEntries }
}
