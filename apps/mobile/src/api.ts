export type Alert = {
  level: string
  message: string
  timestamp: string
}

export type Order = {
  order_id: string
  strategy_id: string | null
  ticker: string
  name: string
  side: string
  qty: number
  order_price: number | null
  status: string
  created_at: string
}

export type Holding = {
  ticker: string
  strategy_id: string
  entry_price: number
  current_price: number | null
  pnl_pct: number | null
  exposure_pct: number
}

export type MobileSummary = {
  server_time: string
  dashboard: {
    total_assets: number
    total_assets_mom_pct: number
    ytd_cagr: number
    current_mdd: number
    sharpe_1y: number
    active_strategies: number
    live_count: number
    mock_count: number
    last_updated: string
  }
  orders: {
    auto_order_active: boolean
    pending: Order[]
    fills_today: Order[]
    expired: Order[]
  }
  risk: {
    short_term_risk: number
    short_term_limit: number
    long_term_risk: number
    long_term_limit: number
    max_exposure_pct: number
    position_count: number
    holdings: Holding[]
  }
  live_monitor: {
    auto_response_active: boolean
    pending_approval_count: number
    pending_release_count: number
  }
  alerts: Alert[]
}

import { AuthError, clearToken, getToken } from './auth'
import { API_BASE } from './config'

export async function fetchSummary(signal?: AbortSignal): Promise<MobileSummary> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${API_BASE}/api/v1/mobile/summary`, { signal, headers })
  if (response.status === 401) {
    clearToken()
    throw new AuthError('로그인이 필요합니다.')
  }
  if (!response.ok) {
    throw new Error(`서버 응답 오류 (${response.status})`)
  }
  return response.json() as Promise<MobileSummary>
}
