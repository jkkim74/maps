import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { AuthError } from './auth'
import { armPick, fetchPicks, fetchSummary, loadCachedSummary } from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('authedFetch 기반 호출', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('토큰이 있으면 Authorization 헤더를 붙인다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    const fetchMock = vi.fn(async () => jsonResponse({ total: 0, picks: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await fetchPicks()
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('401이면 AuthError를 던지고 토큰을 비운다', async () => {
    localStorage.setItem('maps.auth.token', 'tok')
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'no' }, 401)))
    await expect(fetchPicks()).rejects.toBeInstanceOf(AuthError)
    expect(localStorage.getItem('maps.auth.token')).toBeNull()
  })

  it('arm 실패 시 서버 detail 메시지를 그대로 던진다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: '무장 불가 상태: BOUGHT' }, 409)))
    await expect(armPick(1)).rejects.toThrow('무장 불가 상태: BOUGHT')
  })

  it('fetchSummary는 성공 응답을 캐시에 저장한다', async () => {
    const summary = { server_time: 't', dashboard: { total_assets: 100 } }
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(summary)))
    await fetchSummary()
    expect(loadCachedSummary()).toMatchObject({ dashboard: { total_assets: 100 } })
  })
})
