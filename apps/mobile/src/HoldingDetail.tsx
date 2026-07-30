import { ArrowLeft } from 'lucide-react'
import type { AnalysisPick } from './api'
import { STATE_LABEL, staleLabel } from './format'

// 워치리스트(보유/픽) 드릴다운 상세. 목록에서 선택한 AnalysisPick을 받아
// 매수/목표/손절가·체결가·현재가·손익·무장상태를 표시하고, 무장/무장해제 조작을
// 그대로 노출한다. 별도 서버 호출 없이 목록 데이터만 사용한다.

const won = (value: number | null | undefined) =>
  value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}원`

/** 체결가 대비 현재가 손익률(%). 둘 중 하나라도 없으면 null. */
function pnlPct(pick: AnalysisPick): number | null {
  if (pick.fill_price == null || !pick.fill_price || pick.current_price == null) return null
  return ((pick.current_price - pick.fill_price) / pick.fill_price) * 100
}

/** 선택한 워치리스트 픽의 상세 화면. */
export function HoldingDetail({
  pick, busy, onBack, onArm, onDisarm,
}: {
  pick: AnalysisPick
  busy: boolean
  onBack: () => void
  onArm: (pick: AnalysisPick) => void
  onDisarm: (pick: AnalysisPick) => void
}) {
  const armed = pick.state === 'ARMED'
  const bought = pick.state === 'BOUGHT'
  const pnl = pnlPct(pick)
  const stale = staleLabel(pick)
  return (
    <section className="detail">
      <button type="button" className="detail-back" onClick={onBack}>
        <ArrowLeft size={18} />목록으로
      </button>
      <div className="detail-head">
        <div>
          <strong>{pick.name}</strong>
          <span>{pick.ticker}</span>
        </div>
        <span className={`badge ${pick.state.toLowerCase()}`}>{STATE_LABEL[pick.state] ?? pick.state}</span>
      </div>
      {stale ? (
        <div className="status-banner warn">
          기준일 {pick.ref_date ?? '—'} · {stale} — 재분석 후 가격을 갱신해야 무장할 수 있습니다.
        </div>
      ) : null}
      <div className="detail-list">
        <div><span>매수가</span><b>{won(pick.buy_price)}</b></div>
        <div><span>목표가</span><b>{won(pick.target_price)}</b></div>
        <div><span>손절가</span><b>{won(pick.stop_price)}</b></div>
        {pick.rr_ratio != null ? <div><span>R:R</span><b>{pick.rr_ratio}</b></div> : null}
        <div><span>체결가</span><b>{won(pick.fill_price)}</b></div>
        <div><span>현재가</span><b>{won(pick.current_price)}</b></div>
        <div>
          <span>평가 손익</span>
          <b className={pnl == null ? '' : pnl >= 0 ? 'detail-up' : 'detail-down'}>
            {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%`}
          </b>
        </div>
      </div>
      <div className="pick-actions detail-actions">
        <button type="button" disabled={busy || armed || bought || !!pick.data_stale} onClick={() => onArm(pick)}>무장</button>
        <button type="button" className="ghost" disabled={busy || pick.state === 'WATCH' || bought} onClick={() => onDisarm(pick)}>무장해제</button>
      </div>
    </section>
  )
}
