import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { App } from './App'
import { OrderDetail } from './OrderDetail'
import { HoldingDetail } from './HoldingDetail'
import { WatchlistScreen } from './screens/WatchlistScreen'
import type { AnalysisPick, Order } from './api'

function summaryWithOrder() {
  return {
    server_time: '2026-06-29T00:00:00Z',
    dashboard: {
      total_assets: 1000000, total_assets_mom_pct: 0.01, ytd_cagr: 0.12,
      current_mdd: -0.05, sharpe_1y: 1.2, active_strategies: 3,
      live_count: 1, mock_count: 2, last_updated: '2026-06-29',
    },
    orders: {
      auto_order_active: true,
      pending: [{
        order_id: 'ORD-1', strategy_id: 'pullback_v3', ticker: '005930', name: '삼성전자',
        side: 'BUY', qty: 10, order_price: 70000, status: 'pending', created_at: '2026-06-29T00:00:00Z',
      }],
      fills_today: [], expired: [],
    },
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

const sampleOrder: Order = {
  order_id: 'ORD-9', strategy_id: 'donchian_v1', ticker: '000660', name: 'SK하이닉스',
  side: 'SELL', qty: 5, order_price: 120000, status: 'filled', created_at: '2026-06-29T01:00:00Z',
  fill_price: 121500, fill_qty: 5,
}

const samplePick: AnalysisPick = {
  id: 42, ticker: '035720', name: '카카오', state: 'BOUGHT', strategy_trade_enabled: true,
  buy_price: 50000, fill_price: 49000, target_price: 60000, stop_price: 45000,
  current_price: 52000, rr_ratio: 2.2,
}

const splitPick = {
  ...samplePick,
  state: 'BOUGHT',
  trade_mode: 'split' as const,
  total_budget: 9900000,
  entries_cancelled: false,
  exit_pending_reason: null,
  filled_legs: 1,
  total_legs: 3,
  next_entry_price: 47000,
  legs: [
    { id: 1, sequence: 1, entry_price: 50000, weight_pct: 30, planned_qty: 59, filled_qty: 59, remaining_qty: 0, fill_price: 49500, order_id: 'one', status: 'FILLED' },
    { id: 2, sequence: 2, entry_price: 47000, weight_pct: 30, planned_qty: 63, filled_qty: 0, remaining_qty: 63, fill_price: null, order_id: null, status: 'PENDING' },
    { id: 3, sequence: 3, entry_price: 44000, weight_pct: 40, planned_qty: 90, filled_qty: 0, remaining_qty: 90, fill_price: null, order_id: null, status: 'PENDING' },
  ],
}

describe('drill-down 상세 화면', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('주문 행을 탭하면 상세가 열리고, 목록으로 돌아갈 수 있다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(summaryWithOrder())))
    render(<App />)

    // 주문 탭으로 이동
    fireEvent.click(screen.getByText('주문'))
    // 목록 렌더 확인 후 행 탭
    const row = await screen.findByText('삼성전자')
    fireEvent.click(row)

    // 상세 화면 필드가 보인다(주문번호 라벨은 상세에만 존재)
    expect(await screen.findByText('주문번호')).toBeInTheDocument()
    expect(screen.getByText('ORD-1')).toBeInTheDocument()

    // 목록으로 복귀
    fireEvent.click(screen.getByText('목록으로'))
    expect(await screen.findByText('오늘 주문 및 체결')).toBeInTheDocument()
    expect(screen.queryByText('주문번호')).not.toBeInTheDocument()
  })

  it('OrderDetail은 체결가를 표시하고 onBack을 호출한다', () => {
    const onBack = vi.fn()
    render(<OrderDetail order={sampleOrder} onBack={onBack} />)
    expect(screen.getByText('SK하이닉스')).toBeInTheDocument()
    expect(screen.getByText('121,500원')).toBeInTheDocument()
    fireEvent.click(screen.getByText('목록으로'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('HoldingDetail은 손익을 표시하고 onBack을 호출한다', () => {
    const onBack = vi.fn()
    render(
      <HoldingDetail
        pick={samplePick} busy={false} onBack={onBack}
        onArm={vi.fn()} onDisarm={vi.fn()} onStopEntries={vi.fn()}
      />,
    )
    expect(screen.getByText('카카오')).toBeInTheDocument()
    // (52000-49000)/49000 = +6.1%
    expect(screen.getByText('+6.1%')).toBeInTheDocument()
    fireEvent.click(screen.getByText('목록으로'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('워치리스트는 3분할 진행과 다음 진입가를 표시한다', () => {
    render(
      <WatchlistScreen
        picks={[splitPick]} loading={false} error="" busyId={null} message=""
        onArm={vi.fn()} onDisarm={vi.fn()} onStopEntries={vi.fn()}
        onReload={vi.fn()} onSelect={vi.fn()}
      />,
    )

    expect(screen.getByText('3분할 · 1/3 체결')).toBeInTheDocument()
    expect(screen.getByText(/다음 진입.*47,000원/)).toBeInTheDocument()
    expect(screen.getByText('분할 매수 중단')).toBeInTheDocument()
  })

  it('HoldingDetail은 모든 분할 회차와 중지 액션을 표시한다', () => {
    const onStopEntries = vi.fn()
    render(
      <HoldingDetail
        pick={splitPick} busy={false} onBack={vi.fn()}
        onArm={vi.fn()} onDisarm={vi.fn()} onStopEntries={onStopEntries}
      />,
    )

    expect(screen.getByText('3분할 · 1/3 체결')).toBeInTheDocument()
    expect(screen.getByText('1차 · 59/59주')).toBeInTheDocument()
    expect(screen.getByText('2차 · 0/63주')).toBeInTheDocument()
    fireEvent.click(screen.getByText('분할 매수 중단'))
    expect(onStopEntries).toHaveBeenCalledWith(splitPick)
  })
})
