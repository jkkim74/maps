import type { Holding } from '../api'
import { absPct, money, pct, won } from '../format'

// 보유 종목 카드. 티커만 찍던 기존 행을 대체한다 — 종목명·수량·평가금액·손익·
// 손절가까지 한 장에 담아 다른 화면으로 옮겨 다니지 않아도 되게 한다.
//
// 브로커 조회가 실패해 DB 근사(fallback)로 내려온 항목은 quantity=0 /
// market_value=null 이라 수량·평가금액·노출 비중 줄을 숨긴다. 0원이나 0%로
// 찍으면 실제 값으로 읽힌다.

/** 보유 1종목 카드. */
export function HoldingCard({ holding }: { holding: Holding }) {
  const { quantity, market_value: marketValue, pnl_pct: pnlPct } = holding
  const pnlClass = pnlPct == null ? '' : pnlPct >= 0 ? 'text-up' : 'text-down'
  return (
    <article className="pick holding">
      <div className="pick-head">
        <div>
          <strong>{holding.name || holding.ticker}</strong>
          <span>{holding.ticker} · {holding.strategy_id}</span>
        </div>
        <b className={pnlClass}>{pnlPct == null ? '—' : pct(pnlPct)}</b>
      </div>
      {quantity > 0 ? (
        <div className="pick-prices">
          {quantity.toLocaleString('ko-KR')}주
          {marketValue != null ? ` · 평가 ${money(marketValue)}` : ''}
        </div>
      ) : null}
      <div className="holding-meta">
        <div><span>진입가</span><b>{won(holding.entry_price)}</b></div>
        <div><span>현재가</span><b>{won(holding.current_price)}</b></div>
        <div><span>손절가</span><b>{won(holding.stop_price)}</b></div>
        {quantity > 0 ? (
          <div><span>노출 비중</span><b>{absPct(holding.exposure_pct)}</b></div>
        ) : null}
      </div>
    </article>
  )
}
