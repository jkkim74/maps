import { AlertTriangle } from 'lucide-react'
import type { MobileSummary } from '../api'
import { Kpi } from '../components/Kpi'
import { Empty } from '../components/Empty'
import { HoldingCard } from '../components/HoldingCard'
import { absPct, brokerNotice, holdingsTotals, money, pct } from '../format'

/** 리스크 탭 — 보유 현황(주 목적) + 위험 한도 KPI. */
export function RiskScreen({ data }: { data: MobileSummary }) {
  const { holdings, broker_status: brokerStatus, broker_error: brokerError } = data.risk
  const notice = brokerNotice(brokerStatus)
  const totals = holdingsTotals(holdings)
  return (
    <>
      <section>
        <h2>보유 현황</h2>
        {notice ? (
          <div className={`status-banner ${brokerStatus === 'fallback' ? 'warn' : 'danger'}`}>
            <AlertTriangle size={22} />
            <div>
              <strong>{brokerStatus === 'fallback' ? '실시간 잔고 조회 실패' : '보유 내역 조회 불가'}</strong>
              <span>{notice}{brokerError ? ` — ${brokerError}` : ''}</span>
            </div>
          </div>
        ) : null}
        {holdings.length === 0 ? <Empty text="현재 보유 종목이 없습니다." /> : (
          <>
            <div className="preview-summary">
              <strong>
                보유 {totals.count}종목
                {totals.marketValue != null ? ` · 평가 ${money(totals.marketValue)}` : ''}
                {totals.pnlPct != null ? ` · ${pct(totals.pnlPct)}` : ''}
              </strong>
              <span>최대 노출 {absPct(data.risk.max_exposure_pct)}</span>
            </div>
            <div className="holdings">
              {holdings.map((holding) => (
                <HoldingCard holding={holding} key={holding.ticker} />
              ))}
            </div>
          </>
        )}
      </section>
      <section className="kpi-grid">
        <Kpi label="일간 위험" value={absPct(data.risk.short_term_risk)} />
        <Kpi label="위험 한도" value={absPct(data.risk.short_term_limit)} tone="info" />
        <Kpi label="장기 위험" value={absPct(data.risk.long_term_risk)} />
        <Kpi label="장기 한도" value={absPct(data.risk.long_term_limit)} tone="info" />
        <Kpi label="보유 종목" value={`${data.risk.position_count}개`} />
        {/* ?? 0 — 서버 배포보다 앱 설치가 앞서면 필드가 없어 'undefined건'이 찍힌다. */}
        <Kpi
          label="활성 Kill"
          value={`${data.risk.active_kill_count ?? 0}건`}
          tone={data.risk.active_kill_count ? 'danger' : 'info'}
        />
      </section>
    </>
  )
}
