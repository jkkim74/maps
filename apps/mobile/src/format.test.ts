import { describe, it, expect } from 'vitest'
import { brokerNotice, holdingsTotals, orderStatusLabel, sideLabel } from './format'
import type { Holding } from './api'

const holding = (over: Partial<Holding>): Holding => ({
  ticker: '000000', name: '테스트', strategy_id: 'pullback_v3',
  entry_price: 100, current_price: 100, pnl_pct: 0, exposure_pct: 0.1,
  stop_price: 90, quantity: 1, market_value: 100,
  ...over,
})

describe('holdingsTotals', () => {
  it('손익을 비중 가중으로 계산한다 (단순 평균과 다르다)', () => {
    // 큰 포지션 -10%, 작은 포지션 +30% → 실제는 -6%.
    // pnl_pct 단순 평균이면 +10% 로 부호까지 뒤집힌다.
    const totals = holdingsTotals([
      holding({ ticker: 'A', entry_price: 100, quantity: 900, market_value: 81_000, pnl_pct: -0.1 }),
      holding({ ticker: 'B', entry_price: 100, quantity: 100, market_value: 13_000, pnl_pct: 0.3 }),
    ])

    expect(totals.count).toBe(2)
    expect(totals.marketValue).toBe(94_000)
    expect(totals.pnlPct).toBeCloseTo(-0.06, 10)
  })

  it('한 건이라도 실시간 값이 없으면 합계를 내지 않는다', () => {
    const totals = holdingsTotals([
      holding({ ticker: 'A' }),
      holding({ ticker: 'B', quantity: 0, market_value: null }),
    ])

    // 틀린 총액을 보여주는 것보다 공백이 낫다.
    expect(totals.count).toBe(2)
    expect(totals.marketValue).toBeNull()
    expect(totals.pnlPct).toBeNull()
  })

  it('보유가 없으면 0건 · null', () => {
    expect(holdingsTotals([])).toEqual({ count: 0, marketValue: null, pnlPct: null })
  })
})

describe('주문 라벨', () => {
  it('대소문자에 무관하게 한글 라벨을 낸다', () => {
    // 서버가 filled/FILLED, pending/PENDING 을 섞어 내려준다.
    expect(orderStatusLabel('filled')).toBe('체결')
    expect(orderStatusLabel('FILLED')).toBe('체결')
    expect(orderStatusLabel('partially_filled')).toBe('부분체결')
    expect(sideLabel('buy')).toBe('매수')
    expect(sideLabel('SELL')).toBe('매도')
  })

  it('모르는 값은 원문을 유지한다', () => {
    expect(orderStatusLabel('weird_state')).toBe('weird_state')
    expect(sideLabel('short')).toBe('short')
  })
})

describe('brokerNotice', () => {
  it('정상이면 null, 실패면 문구를 낸다', () => {
    expect(brokerNotice('ok')).toBeNull()
    expect(brokerNotice('')).toBeNull()
    expect(brokerNotice('fallback')).toContain('실시간 아님')
    expect(brokerNotice('unavailable')).toContain('조회할 수 없습니다')
  })
})
