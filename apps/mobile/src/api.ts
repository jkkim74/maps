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

// 분석 워치리스트(SCR-19) 픽 — 서버 AnalysisPickItem의 앱 사용 필드.
export type AnalysisPick = {
  id: number
  ticker: string
  name: string
  state: string // WATCH | ARMED | BOUGHT | CLOSED | CANCELLED
  strategy_trade_enabled: boolean
  buy_price: number | null
  fill_price: number | null // 실제 진입 체결가(보유/체결 시)
  target_price: number | null
  stop_price: number | null
  current_price: number | null
  rr_ratio: number | null
}

type PicksResponse = { total: number; picks: AnalysisPick[] }

// 추이 차트(SCR home) — portfolio_snapshot 기반 일별 자산 시계열.
export type PortfolioHistoryPoint = {
  date: string // YYYY-MM-DD
  total_value: number // 해당일 총 자산(원)
  pnl_pct: number // 전일 대비 수익률(소수)
}

export type PortfolioHistory = {
  days: number
  cumulative_pct: number // 기간 누적 수익률(소수)
  points: PortfolioHistoryPoint[]
}

/** 토큰을 주입하고 401을 AuthError로 변환하는 공통 fetch. */
async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  const token = getToken()
  const headers: Record<string, string> = { ...((init?.headers as Record<string, string>) ?? {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    throw new AuthError('로그인이 필요합니다.')
  }
  return res
}

/** POST 액션 — 실패 시 서버 detail 메시지를 그대로 던진다(예: "무장 불가 상태"). */
async function postAction(path: string, body?: unknown): Promise<void> {
  const init: RequestInit = { method: 'POST' }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const res = await authedFetch(path, init)
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      /* JSON 아님 — 기본 메시지 유지 */
    }
    throw new Error(detail)
  }
}

const SUMMARY_CACHE_KEY = 'maps.cache.summary'

/** 마지막 성공 요약을 보관해 콜드스타트 즉시 표시·오프라인 폴백에 쓴다. */
export function loadCachedSummary(): MobileSummary | undefined {
  try {
    const raw = localStorage.getItem(SUMMARY_CACHE_KEY)
    return raw ? (JSON.parse(raw) as MobileSummary) : undefined
  } catch {
    return undefined
  }
}

function saveCachedSummary(summary: MobileSummary): void {
  try {
    localStorage.setItem(SUMMARY_CACHE_KEY, JSON.stringify(summary))
  } catch {
    /* 용량 초과 등 무시 */
  }
}

export async function fetchSummary(signal?: AbortSignal): Promise<MobileSummary> {
  const response = await authedFetch('/api/v1/mobile/summary', { signal })
  if (!response.ok) {
    throw new Error(`서버 응답 오류 (${response.status})`)
  }
  const summary = (await response.json()) as MobileSummary
  saveCachedSummary(summary)
  return summary
}

const HISTORY_CACHE_KEY = 'maps.cache.portfolio_history'

/** 마지막 성공 추이를 보관해 콜드스타트 즉시 표시·오프라인 폴백에 쓴다. */
export function loadCachedHistory(): PortfolioHistory | undefined {
  try {
    const raw = localStorage.getItem(HISTORY_CACHE_KEY)
    return raw ? (JSON.parse(raw) as PortfolioHistory) : undefined
  } catch {
    return undefined
  }
}

function saveCachedHistory(history: PortfolioHistory): void {
  try {
    localStorage.setItem(HISTORY_CACHE_KEY, JSON.stringify(history))
  } catch {
    /* 용량 초과 등 무시 */
  }
}

export async function fetchPortfolioHistory(
  days = 30,
  signal?: AbortSignal,
): Promise<PortfolioHistory> {
  const response = await authedFetch(`/api/v1/mobile/portfolio-history?days=${days}`, { signal })
  if (!response.ok) {
    throw new Error(`서버 응답 오류 (${response.status})`)
  }
  const history = (await response.json()) as PortfolioHistory
  saveCachedHistory(history)
  return history
}

export async function fetchPicks(signal?: AbortSignal): Promise<AnalysisPick[]> {
  const response = await authedFetch('/api/v1/analysis-picks', { signal })
  if (!response.ok) {
    throw new Error(`서버 응답 오류 (${response.status})`)
  }
  const data = (await response.json()) as PicksResponse
  return data.picks
}

export async function armPick(id: number): Promise<void> {
  await postAction(`/api/v1/analysis-picks/${id}/arm`)
}

export async function disarmPick(id: number): Promise<void> {
  await postAction(`/api/v1/analysis-picks/${id}/disarm`)
}

// FCM 네이티브 푸시 — 기기 등록 토큰 등록/해지.
/** 로그인 후 획득한 FCM 토큰을 서버에 등록한다. */
export async function registerDeviceToken(token: string, platform: string): Promise<void> {
  await postAction('/api/v1/mobile/device-token', { token, platform })
}

/** 로그아웃/해지 시 토큰을 비활성화한다. */
export async function deregisterDeviceToken(token: string): Promise<void> {
  const res = await authedFetch('/api/v1/mobile/device-token', {
    method: 'DELETE',
    body: JSON.stringify({ token }),
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`)
}

// Kill-Switch (실거래 모니터) — 청산 승인 / 해제 대기 항목.
export type KillSwitchEntry = {
  strategy_id: string | null
  reason: string
  event_type: string
  created_at: string
}

type LiveMonitorResp = {
  pending_approvals?: KillSwitchEntry[]
  pending_releases?: KillSwitchEntry[]
}

export type KillSwitches = { approvals: KillSwitchEntry[]; releases: KillSwitchEntry[] }

export async function fetchKillSwitches(signal?: AbortSignal): Promise<KillSwitches> {
  const res = await authedFetch('/api/v1/live-monitor', { signal })
  if (!res.ok) {
    throw new Error(`서버 응답 오류 (${res.status})`)
  }
  const d = (await res.json()) as LiveMonitorResp
  return { approvals: d.pending_approvals ?? [], releases: d.pending_releases ?? [] }
}

/** 보유 포지션 청산 승인 (고위험 — 실매도). */
export async function approveLiquidation(strategyId: string, approvedBy: string): Promise<void> {
  await postAction(
    `/api/v1/live-monitor/${encodeURIComponent(strategyId)}/approve-liquidation`,
    { approved_by: approvedBy },
  )
}

/** Kill-Switch 완전 해제 (신규 진입 재개). */
export async function releaseKillSwitch(strategyId: string, approvedBy: string): Promise<void> {
  await postAction(
    `/api/v1/live-monitor/${encodeURIComponent(strategyId)}/release`,
    { approved_by: approvedBy },
  )
}
