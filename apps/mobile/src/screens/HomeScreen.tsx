import { Minus, ShieldCheck, ShieldX, TrendingDown, TrendingUp } from 'lucide-react'
import type { MobileSummary, PortfolioHistory } from '../api'
import { Kpi } from '../components/Kpi'
import { AlertList } from '../components/AlertList'
import { TrendChart } from '../components/TrendChart'
import { REGIME_LABEL, VOL_LABEL, absPct, money, pct, regimeTone, weeklyLabel } from '../format'

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

/** 홈 탭 — 장세 배너 + 운영 상태 배너 + 핵심 KPI + 자산 추이 + 오늘의 운영 + 최근 알림. */
export function HomeScreen({ data, history }: { data: MobileSummary; history?: PortfolioHistory }) {
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
