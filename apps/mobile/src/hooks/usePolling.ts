import { useEffect } from 'react'

/** 자동 갱신 주기(30초). */
export const POLL_INTERVAL = 30000

/**
 * 주기적 폴링 훅. `active`일 때만 동작하며, 백그라운드(document.hidden)에서는 tick을 건너뛰고
 * 화면 복귀(visibilitychange) 시 즉시 한 번 실행한다. `tick` 또는 `active`가 바뀌면 타이머를
 * 재설정하므로, 호출측은 tick을 useCallback으로 안정화해 의도한 시점에만 재설정되게 한다.
 */
export function usePolling(tick: () => void, active: boolean, interval: number = POLL_INTERVAL): void {
  useEffect(() => {
    if (!active) return
    const run = () => {
      if (document.hidden) return
      tick()
    }
    const timer = window.setInterval(run, interval)
    const onVisible = () => { if (!document.hidden) run() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [tick, active, interval])
}
