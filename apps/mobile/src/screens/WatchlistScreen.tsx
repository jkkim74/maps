import { AlertTriangle } from 'lucide-react'
import type { AnalysisPick } from '../api'
import { Empty } from '../components/Empty'
import { STATE_LABEL, won } from '../format'

/** 워치리스트 탭 — 분석 픽 목록 + 무장/무장해제 액션. */
export function WatchlistScreen({
  picks, loading, error, busyId, message, onArm, onDisarm, onReload, onSelect,
}: {
  picks: AnalysisPick[]
  loading: boolean
  error: string
  busyId: number | null
  message: string
  onArm: (pick: AnalysisPick) => void
  onDisarm: (pick: AnalysisPick) => void
  onReload: () => void
  onSelect: (pick: AnalysisPick) => void
}) {
  return (
    <section>
      <h2>분석 워치리스트</h2>
      {message ? <div className="action-msg">{message}</div> : null}
      {error ? <div className="error"><AlertTriangle size={20} />{error}<button type="button" onClick={onReload}>다시 시도</button></div> : null}
      {loading && !picks.length ? <Empty text="불러오는 중…" /> : null}
      {!loading && !picks.length && !error ? <Empty text="워치리스트가 비어 있습니다." /> : null}
      <div className="rows">
        {picks.map((p) => {
          const busy = busyId === p.id
          const armed = p.state === 'ARMED'
          const bought = p.state === 'BOUGHT'
          return (
            <article className="pick" key={p.id}>
              <div className="pick-head tappable" onClick={() => onSelect(p)}>
                <div><strong>{p.name}</strong><span>{p.ticker}</span></div>
                <span className={`badge ${p.state.toLowerCase()}`}>{STATE_LABEL[p.state] ?? p.state}</span>
              </div>
              <div className="pick-prices">
                매수 {won(p.buy_price)} · 목표 {won(p.target_price)} · 손절 {won(p.stop_price)}
                {p.rr_ratio != null ? ` · R:R ${p.rr_ratio}` : ''}
              </div>
              {p.fill_price != null ? (
                <div className="pick-fill">
                  체결 {won(p.fill_price)}
                  {p.current_price != null ? ` · 현재 ${won(p.current_price)}` : ''}
                  {p.fill_price && p.current_price != null
                    ? ` (${p.current_price >= p.fill_price ? '+' : ''}${(((p.current_price - p.fill_price) / p.fill_price) * 100).toFixed(1)}%)`
                    : ''}
                </div>
              ) : null}
              <div className="pick-actions">
                <button type="button" disabled={busy || armed || bought} onClick={() => onArm(p)}>무장</button>
                <button type="button" className="ghost" disabled={busy || p.state === 'WATCH' || bought} onClick={() => onDisarm(p)}>무장해제</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
