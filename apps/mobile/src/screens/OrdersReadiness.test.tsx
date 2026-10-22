import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MobileSummary } from '../api'
import { OrdersScreen } from './OrdersScreen'
import { HomeScreen } from './HomeScreen'

function summary(state = 'blocked') {
  return {
    dashboard: { total_assets: 0, current_mdd: 0, ytd_cagr: 0, active_strategies: 0 },
    orders: { auto_order_active: true, execution_state: state, block_reasons: ['trading_disabled', 'new_reason'],
      checked_at: '2026-10-22T03:00:00+00:00', environment: 'mock',
      strategy_blocks: { stopped_strategy: ['strategy_kill_switch', 'daily_loss'] },
      pending: [], fills_today: [], expired: [],
      slippage: { actual: null, sample_count: 0, large_cap_assumed: .0005, mid_small_assumed: .0015 } },
    risk: { holdings: [], position_count: 0 }, live_monitor: { pending_approval_count: 0 }, alerts: [],
  } as unknown as MobileSummary
}

describe('execution readiness evidence', () => {
  it.each([['blocked', '자동 주문 차단됨'], ['unknown', '자동 주문 확인 필요']])('home shows %s and actual reasons', (state, label) => {
    render(<HomeScreen data={summary(state)} onGoRisk={() => {}} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(/전역 주문 OFF/)).toBeInTheDocument()
    expect(screen.getByText(/new_reason/)).toBeInTheDocument()
    expect(screen.getByText(/stopped_strategy/)).toBeInTheDocument()
    expect(screen.getByText(/대사 확인/)).toBeInTheDocument()
  })

  it('orders shows cumulative order-day collection basis, missing actual samples and partial remainder', () => {
    const data = summary()
    data.orders.pending = [{ order_id: 'partial', ticker: '005930', name: '삼성전자', strategy_id: 's',
      side: 'buy', qty: 10, fill_qty: 4, remaining_qty: 6, order_price: 100,
      status: 'PARTIALLY_FILLED', created_at: '2026-10-01T03:00:00+00:00' }]
    render(<OrdersScreen data={data} preview={null} previewLoading={false} previewError=""
      onReloadPreview={() => {}} onSelect={() => {}} />)
    expect(screen.getByText('자동 주문 차단됨')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '오늘 생성 주문의 누적 체결 (KST)' })).toBeInTheDocument()
    expect(screen.getByText(/실제 체결 시각 기준 아님/)).toBeInTheDocument()
    expect(screen.getByText(/표본 0건/)).toBeInTheDocument()
    expect(screen.getByText(/잔량 6주/)).toBeInTheDocument()
    expect(screen.getByText(/누적 체결 4주/)).toBeInTheDocument()
  })
})
