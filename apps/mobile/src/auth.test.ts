import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { AuthError, clearToken, getToken, getUsername, login } from './auth'

describe('토큰/사용자명 저장', () => {
  beforeEach(() => localStorage.clear())

  it('기본값: 토큰 없음, username은 mobile', () => {
    expect(getToken()).toBeNull()
    expect(getUsername()).toBe('mobile')
  })

  it('clearToken은 토큰과 username을 모두 제거한다', () => {
    localStorage.setItem('maps.auth.token', 't')
    localStorage.setItem('maps.auth.user', 'admin')
    clearToken()
    expect(getToken()).toBeNull()
    expect(getUsername()).toBe('mobile')
  })
})

describe('login()', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('성공 시 토큰과 username을 저장한다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ token: 'tok123', username: 'admin' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    await login('admin', 'pw')
    expect(getToken()).toBe('tok123')
    expect(getUsername()).toBe('admin')
  })

  it('401이면 AuthError를 던지고 토큰을 저장하지 않는다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"no"}', { status: 401 })))
    await expect(login('admin', 'bad')).rejects.toBeInstanceOf(AuthError)
    expect(getToken()).toBeNull()
  })
})
