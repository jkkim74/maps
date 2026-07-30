import { ChevronRight, Minus, ShieldCheck, ShieldX, TrendingDown, TrendingUp } from 'lucide-react'
import type { MobileSummary, PortfolioHistory } from '../api'
import { Kpi } from '../components/Kpi'
import { AlertList } from '../components/AlertList'
import { TrendChart } from '../components/TrendChart'
import { REGIME_LABEL, VOL_LABEL, absPct, holdingsTotals, money, pct, regimeTone, weeklyLabel } from '../format'

/** 홈에 표시할 종목별 미니 줄의 최대 개수. 넘으면 "외 N건"을 반드시 찍는다. */
const HOME_HOLDING_PREVIEW = 5

/**
 * 홈 보유 요약 — 보유가 어디 있는지 찾지 못하는 문제를 없애기 위한 진입점.
 * 상세(수량·평가금액·손절가)는 리스크 탭의 보유 현황이 담당한다.
 */
function HoldingSummary({ data, onGoRisk }: { data: MobileSummary; onGoRisk: () => void }) {
  const { holdings } = data.risk
  const totals = holdingsTotals(holdings)
  const shown = holdings.slice(0, HOME_HOLDING_PREVIEW)
  const hidden = holdings.length - shown.length
  return (
    <section>
      {/* 리스크 탭의 '보유 현황'과 구분되는 제목을 쓴다 — 같은 문구면 어느 화면인지 헷갈린다. */}
      <h2>보유 요약</h2>
      <div className="holding-summary tappable" onClick={onGoRisk}>
        <div className="holding-summary-head">
          <strong>보유 {totals.count}종목</strong>
          {totals.pnlPct != null ? (
            <b className={totals.pnlPct >= 0 ? 'text-up' : 'text-down'}>평가 {pct(totals.pnlPct)}</b>
          ) : null}
        </div>
        {totals.marketValue != null ? (
          <span className="holding-summary-value">평가 {money(totals.marketValue)}</span>
        ) : null}
        {holdings.length > 0 ? (
          <p className="holding-summary-list">
            {shown
              .map((h) => `${h.name || h.ticker} ${h.pnl_pct == null ? '—' : pct(h.pnl_pct)}`)
              .join(' · ')}
            {/* 자를 때는 반드시 남은 건수를 노출한다 — 조용히 truncate 하면
                "보유가 다 안 나온다"로 읽힌다. */}
            {hidden > 0 ? ` · 외 ${hidden}건` : ''}
          </p>
        ) : (
          <p className="holding-summary-list">보유 종목이 없습니다.</p>
        )}
        <button type="button" className="holding-summary-link" onClick={onGoRisk}>
          보유 전체 보기<ChevronRight size={16} />
        </button>
      </div>
    </section>
  )
}

/** 장세 배너 — 현재 시장 국면(강세/혼조/약세)을 홈 최상단에 표시. */
function RegimeBanner({ regime }: { regime: MobileSummary['regime'] }) {
  const tone = regimeTone(regime.regime, regime.weekly_trend)
  const Icon = regime.regime === 'strong' ? TrendingUp : regime.regime === 'weak' ? TrendingDown : Minus
  const label = REGIME_LABEL[regime.regime] ?? regime.regime
  const sub: string[] = [`주봉 ${weeklyLabel(regime.weekly_trend)}`, `변동성 ${VOL_LABEL[regime.vol_regime] ?? regime.vol_regime}`]
  if (regime.up_count != null && regime.total_assets != null) sub.push(`상승 ${regime.up_count}/${regime.total_assets}`)
  if (regime.floor_applied) sub.push('하한적용')
  return (
    <section className={`status-banner ${tone}`}>
      <Icon size={22} />
      <div>
        <strong>장세 · {label}</strong>
        <span>{sub.join(' · ')}</span>
      </div>
    </section>
  )
}

/** 홈 탭 — 장세 배너 + 운영 상태 배너 + 핵심 KPI + 자산 추이 + 보유 요약 + 오늘의 운영 + 최근 알림. */
export function HomeScreen({
  data, history, onGoRisk,
}: {
  data: MobileSummary
  history?: PortfolioHistory
  onGoRisk: () => void
}) {
  const blocked = !data.orders.auto_order_active
  return (
    <>
      {data.regime ? <RegimeBanner regime={data.regime} /> : null}
      <section className={`status-banner ${blocked ? 'danger' : 'ok'}`}>
        {blocked ? <ShieldX size={22} /> : <ShieldCheck size={22} />}
        <div>
          <strong>{blocked ? '자동 주문 차단됨' : '자동 주문 정상'}</strong>
          <span>{blocked ? 'Kill Switch 상태를 확인하세요.' : '운영 상태가 정상입니다.'}</span>
        </div>
      </section>
      <section className="kpi-grid">
        <Kpi label="총 자산" value={money(data.dashboard.total_assets)} />
        <Kpi label="현재 MDD" value={absPct(data.dashboard.current_mdd)} tone={data.dashboard.current_mdd < -0.2 ? 'danger' : 'info'} />
        <Kpi label="YTD 수익률" value={pct(data.dashboard.ytd_cagr)} tone="good" />
        <Kpi label="활성 전략" value={`${data.dashboard.active_strategies}개`} />
      </section>
      <TrendChart history={history} />
      <HoldingSummary data={data} onGoRisk={onGoRisk} />
      <section>
        <h2>오늘의 운영</h2>
        <div className="metric-list">
          <div><span>미체결 주문</span><b>{data.orders.pending.length}</b></div>
          <div><span>오늘 체결</span><b>{data.orders.fills_today.length}</b></div>
          <div><span>보유 종목</span><b>{data.risk.position_count}</b></div>
          <div><span>청산 승인 대기</span><b className={data.live_monitor.pending_approval_count ? 'text-danger' : ''}>{data.live_monitor.pending_approval_count}</b></div>
        </div>
      </section>
      <section>
        <h2>최근 알림</h2>
        <AlertList items={data.alerts.slice(0, 3)} />
      </section>
    </>
  )
}
