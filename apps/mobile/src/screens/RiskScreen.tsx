import type { MobileSummary } from '../api'
import { Kpi } from '../components/Kpi'
import { Empty } from '../components/Empty'
import { absPct } from '../format'

/** 리스크 탭 — 위험 한도 KPI + 보유 현황. */
export function RiskScreen({ data }: { data: MobileSummary }) {
  return (
    <>
      <section className="kpi-grid">
        <Kpi label="일간 위험" value={absPct(data.risk.short_term_risk)} />
        <Kpi label="위험 한도" value={absPct(data.risk.short_term_limit)} tone="info" />
        <Kpi label="장기 위험" value={absPct(data.risk.long_term_risk)} />
        <Kpi label="장기 한도" value={absPct(data.risk.long_term_limit)} tone="info" />
        <Kpi label="최대 노출" value={absPct(data.risk.max_exposure_pct)} />
        <Kpi label="보유 종목" value={`${data.risk.position_count}개`} />
      </section>
      <section>
        <h2>보유 현황</h2>
        {data.risk.holdings.length === 0 ? <Empty text="현재 보유 종목이 없습니다." /> : (
          <div className="rows">
            {data.risk.holdings.map((holding) => (
              <article className="row" key={holding.ticker}>
                <div><strong>{holding.ticker}</strong><span>{holding.strategy_id}</span></div>
                <div className="row-end"><b>{absPct(holding.exposure_pct)}</b><span>노출 비중</span></div>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  )
}
