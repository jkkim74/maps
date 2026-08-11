import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { App } from './App'

// 운영 실측(2026-07-30)과 같은 3종목. 수량·평가금액이 있어야 가중 손익이 계산된다.
const HOLDINGS = [
  {
    ticker: '002810', name: '삼영무역', strategy_id: 'multi_asset_trend_v1',
    entry_price: 23344, current_price: 22600, pnl_pct: -0.0319, exposure_pct: 0.06,
    stop_price: 21359, quantity: 226, market_value: 5107600,
  },
  {
    ticker: '082640', name: '동양생명', strategy_id: 'donchian_v1',
    entry_price: 8180, current_price: 8150, pnl_pct: -0.0037, exposure_pct: 0.0609,
    stop_price: 7343, quantity: 636, market_value: 5183400,
  },
  {
    ticker: '089860', name: '롯데렌탈', strategy_id: 'pullback_v3',
    entry_price: 36600, current_price: 37600, pnl_pct: 0.0273, exposure_pct: 0.0498,
    stop_price: 32416, quantity: 113, market_value: 4248800,
  },
]

/** riskOverrides로 risk 블록만 부분 교체한다(fallback 배너 등 케이스용). */
function fullSummary(riskOverrides: Record<string, unknown> = {}) {
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
      long_term_limit: -0.1, max_exposure_pct: 0.0609, position_count: HOLDINGS.length,
      holdings: HOLDINGS, broker_status: 'ok', broker_error: null, active_kill_count: 0,
      ...riskOverrides,
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

  it('분할 매수 중단은 확인 후 stop-entries를 호출한다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const split = {
      id: 7, ticker: '005930', name: '삼성전자', state: 'BOUGHT', strategy_trade_enabled: true,
      buy_price: 70000, fill_price: 70000, target_price: 80000, stop_price: 60000,
      current_price: 69000, rr_ratio: 2, trade_mode: 'split', total_budget: 9900000,
      entries_cancelled: false, exit_pending_reason: null, filled_legs: 1, total_legs: 3,
      next_entry_price: 67000, legs: [],
    }
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/analysis-picks/7/stop-entries')) return jsonResponse({})
      if (url.includes('/analysis-picks')) return jsonResponse({ total: 1, picks: [split] })
      if (url.includes('portfolio-history')) return jsonResponse({ days: 30, cumulative_pct: 0, points: [] })
      return jsonResponse(fullSummary())
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.click(screen.getByText('워치'))
    fireEvent.click(await screen.findByText('분할 매수 중단'))

    expect(window.confirm).toHaveBeenCalled()
    expect(await screen.findByText(/분할 매수 중단됨/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/analysis-picks/7/stop-entries'),
      expect.objectContaining({ method: 'POST' }),
    )
  })
})

describe('보유 현황', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  async function renderApp(riskOverrides: Record<string, unknown> = {}) {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(fullSummary(riskOverrides))))
    render(<App />)
    await screen.findByText('총 자산')
  }

  it('홈에 보유 요약과 종목별 손익이 나온다', async () => {
    await renderApp()
    expect(screen.getByText('보유 3종목')).toBeInTheDocument()
    // 종목별 미니 줄 — 종목명이 보여야 한다(티커만 찍히던 게 문제였다).
    expect(screen.getByText(/삼영무역 -3\.2%/)).toBeInTheDocument()
    expect(screen.getByText(/롯데렌탈 \+2\.7%/)).toBeInTheDocument()
  })

  it('홈 보유 요약 손익은 비중 가중이다(단순 평균이 아니다)', async () => {
    await renderApp()
    // Σmv 14,539,800 / Σ(진입×수량) 14,614,024 − 1 = −0.5%
    // pnl_pct 단순 평균은 (−3.19 −0.37 +2.73)/3 = −0.3% 로 다르다.
    expect(screen.getByText('평가 -0.5%')).toBeInTheDocument()
  })

  it('"보유 전체 보기"를 누르면 리스크 탭 보유 현황으로 이동한다', async () => {
    await renderApp()
    fireEvent.click(screen.getByText('보유 전체 보기'))
    expect(await screen.findByText('보유 현황')).toBeInTheDocument()
    expect(screen.getByText('활성 Kill')).toBeInTheDocument()
  })

  it('리스크 탭이 보유 3건을 종목명·수량·평가금액과 함께 렌더한다', async () => {
    await renderApp()
    fireEvent.click(screen.getByText('리스크'))
    await screen.findByText('보유 현황')

    for (const name of ['삼영무역', '동양생명', '롯데렌탈']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getByText('089860 · pullback_v3')).toBeInTheDocument()
    expect(screen.getByText('113주 · 평가 4,248,800원')).toBeInTheDocument()
    expect(screen.getByText('32,416')).toBeInTheDocument()   // 손절가
    expect(screen.getAllByText('노출 비중')).toHaveLength(3)
  })

  it('브로커 조회 실패(fallback)면 경고 배너를 띄우고 수량 줄을 숨긴다', async () => {
    await renderApp({
      broker_status: 'fallback',
      broker_error: 'connection refused',
      // fallback 경로는 실시간 잔고가 없어 수량·평가금액이 비어 온다.
      holdings: HOLDINGS.map((h) => ({ ...h, quantity: 0, market_value: null })),
    })
    fireEvent.click(screen.getByText('리스크'))

    expect(await screen.findByText('실시간 잔고 조회 실패')).toBeInTheDocument()
    expect(screen.getByText(/connection refused/)).toBeInTheDocument()
    // 0주 / 0원으로 찍으면 실제 값으로 읽힌다 → 아예 숨긴다.
    expect(screen.queryByText(/평가 0원/)).not.toBeInTheDocument()
    expect(screen.queryByText('노출 비중')).not.toBeInTheDocument()
  })

  it('보유가 없으면 빈 상태 문구를 보여준다', async () => {
    await renderApp({ holdings: [], position_count: 0 })
    fireEvent.click(screen.getByText('리스크'))
    expect(await screen.findByText('현재 보유 종목이 없습니다.')).toBeInTheDocument()
  })
})
