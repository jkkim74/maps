import { ShieldCheck, ShieldX } from 'lucide-react'
import type { MobileSummary, PortfolioHistory } from '../api'
import { Kpi } from '../components/Kpi'
import { AlertList } from '../components/AlertList'
import { TrendChart } from '../components/TrendChart'
import { absPct, money, pct } from '../format'

/** 홈 탭 — 운영 상태 배너 + 핵심 KPI + 자산 추이 + 오늘의 운영 + 최근 알림. */
export function HomeScreen({ data, history }: { data: MobileSummary; history?: PortfolioHistory }) {
  const blocked = !data.orders.auto_order_active
  return (
    <>
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
