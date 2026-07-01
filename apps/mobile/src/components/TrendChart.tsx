import type { PortfolioHistory } from '../api'
import { money, pct } from '../format'
import { Empty } from './Empty'

/** 의존성 없는 SVG 스파크라인. 상승=녹색, 하락=적색으로 면적+라인을 그린다. */
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null
  const w = 300
  const h = 64
  const pad = 4
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const stepX = (w - pad * 2) / (values.length - 1)
  const coords = values.map((v, i): [number, number] => [
    pad + i * stepX,
    pad + (h - pad * 2) * (1 - (v - min) / span),
  ])
  const line = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${line} L${coords[coords.length - 1][0].toFixed(1)},${h - pad} L${coords[0][0].toFixed(1)},${h - pad} Z`
  const up = values[values.length - 1] >= values[0]
  const stroke = up ? '#22c55e' : '#ef4444'
  const fill = up ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)'
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label="자산 추이 차트">
      <path d={area} fill={fill} stroke="none" />
      <path d={line} fill="none" stroke={stroke} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

/** 홈 탭 자산 추이 차트 — 포트폴리오 일별 총자산 시계열을 스파크라인으로 표시. */
export function TrendChart({ history }: { history?: PortfolioHistory }) {
  const points = history?.points ?? []
  const last = points[points.length - 1]
  const cumulative = history?.cumulative_pct ?? 0
  return (
    <section>
      <h2>자산 추이{points.length ? ` · 최근 ${points.length}일` : ''}</h2>
      {points.length < 2 || !last ? (
        <Empty text="추이 데이터가 아직 없습니다." />
      ) : (
        <div className="trend">
          <div className="trend-head">
            <strong>{money(last.total_value)}</strong>
            <span className={cumulative >= 0 ? 'text-up' : 'text-down'}>{pct(cumulative)} <small>기간 누적</small></span>
          </div>
          <Sparkline values={points.map((p) => p.total_value)} />
        </div>
      )}
    </section>
  )
}
