import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { App } from './App'

function fullSummary() {
  return {
    server_time: '2026-06-29T00:00:00Z',
    dashboard: {
      total_assets: 1000000, total_assets_mom_pct: 0.01, ytd_cagr: 0.12,
      current_mdd: -0.05, sharpe_1y: 1.2, active_strategies: 3,
      live_count: 1, mock_count: 2, last_updated: '2026-06-29',
    },
    orders: { auto_order_active: true, pending: [], fills_today: [], expired: [] },
    risk: {
      short_term_risk: -0.01, short_term_limit: -0.05, long_term_risk: -0.02,
      long_term_limit: -0.1, max_exposure_pct: 0.2, position_count: 0, holdings: [],
    },
    live_monitor: { auto_response_active: true, pending_approval_count: 0, pending_release_count: 0 },
    alerts: [],
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('<App />', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('summary가 401이면 로그인 화면을 보여준다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'no' }, 401)))
    render(<App />)
    expect(await screen.findByText('비밀번호')).toBeInTheDocument()
  })

  it('summary 성공 시 대시보드(총 자산)를 렌더한다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(fullSummary())))
    render(<App />)
    expect(await screen.findByText('총 자산')).toBeInTheDocument()
  })

  it('추이 시계열이 있으면 자산 추이 차트를 렌더한다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    const history = {
      days: 30,
      cumulative_pct: 0.045,
      points: [
        { date: '2026-06-27', total_value: 1000000, pnl_pct: 0 },
        { date: '2026-06-28', total_value: 1100000, pnl_pct: 0.1 },
        { date: '2026-06-29', total_value: 1045000, pnl_pct: -0.05 },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        jsonResponse(url.includes('portfolio-history') ? history : fullSummary()),
      ),
    )
    render(<App />)
    expect(await screen.findByLabelText('자산 추이 차트')).toBeInTheDocument()
  })

  it('추이 요청이 실패해도 대시보드는 정상 렌더한다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.includes('portfolio-history') ? jsonResponse({ detail: 'boom' }, 500) : jsonResponse(fullSummary()),
      ),
    )
    render(<App />)
    expect(await screen.findByText('총 자산')).toBeInTheDocument()
  })
})
