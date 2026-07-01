import { AlertTriangle } from 'lucide-react'
import type { KillSwitches } from '../api'
import { Empty } from './Empty'

/** Kill-Switch 청산 승인 / 해제 대기 패널 (리스크 탭). */
export function KillSwitchPanel({
  data, loading, error, busyId, message, onApprove, onRelease,
}: {
  data: KillSwitches
  loading: boolean
  error: string
  busyId: string | null
  message: string
  onApprove: (strategyId: string) => void
  onRelease: (strategyId: string) => void
}) {
  const empty = !data.approvals.length && !data.releases.length
  return (
    <section>
      <h2>Kill-Switch</h2>
      {message ? <div className="action-msg">{message}</div> : null}
      {error ? <div className="error"><AlertTriangle size={20} />{error}</div> : null}
      {loading && empty ? <Empty text="불러오는 중…" /> : null}
      {!loading && empty && !error ? <Empty text="대기 중인 Kill-Switch 작업이 없습니다." /> : null}
      <div className="rows">
        {data.approvals.filter((k) => k.strategy_id).map((k) => (
          <article className="ks danger" key={`a-${k.strategy_id}`}>
            <div className="ks-head">
              <div><strong>{k.strategy_id}</strong><span>{k.reason || '발동됨'}</span></div>
              <span className="badge armed">청산 승인 대기</span>
            </div>
            <button
              type="button" className="danger-btn"
              disabled={busyId === k.strategy_id}
              onClick={() => onApprove(k.strategy_id as string)}
            >보유 포지션 청산 승인</button>
          </article>
        ))}
        {data.releases.filter((k) => k.strategy_id).map((k) => (
          <article className="ks" key={`r-${k.strategy_id}`}>
            <div className="ks-head">
              <div><strong>{k.strategy_id}</strong><span>청산 완료 — 해제 대기</span></div>
              <span className="badge bought">해제 대기</span>
            </div>
            <button
              type="button"
              disabled={busyId === k.strategy_id}
              onClick={() => onRelease(k.strategy_id as string)}
            >Kill-Switch 해제(신규 진입 재개)</button>
          </article>
        ))}
      </div>
    </section>
  )
}
