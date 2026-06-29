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
})
