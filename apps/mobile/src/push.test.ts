import { afterEach, describe, expect, it, vi } from 'vitest'

// @capacitor/* 실제 네이티브 플러그인은 테스트/웹에 없으므로 모듈 자체를 모킹한다.
const getPlatform = vi.fn(() => 'web')
vi.mock('@capacitor/core', () => ({ Capacitor: { getPlatform: () => getPlatform() } }))

const checkPermissions = vi.fn(async () => ({ receive: 'granted' }))
const requestPermissions = vi.fn(async () => ({ receive: 'granted' }))
const register = vi.fn(async () => {})
const listeners: Record<string, (arg: unknown) => void> = {}
const addListener = vi.fn(async (event: string, cb: (arg: unknown) => void) => {
  listeners[event] = cb
  return { remove: vi.fn() }
})
vi.mock('@capacitor/push-notifications', () => ({
  PushNotifications: { checkPermissions, requestPermissions, register, addListener },
}))

const registerDeviceToken = vi.fn(async () => {})
const deregisterDeviceToken = vi.fn(async () => {})
vi.mock('./api', () => ({ registerDeviceToken, deregisterDeviceToken }))

async function freshPush() {
  vi.resetModules()
  return import('./push')
}

describe('initPushNotifications', () => {
  afterEach(() => {
    vi.clearAllMocks()
    getPlatform.mockReturnValue('web')
    for (const k of Object.keys(listeners)) delete listeners[k]
  })

  it('웹에서는 아무 것도 하지 않는다(no-op)', async () => {
    getPlatform.mockReturnValue('web')
    const { initPushNotifications } = await freshPush()
    await initPushNotifications()
    expect(register).not.toHaveBeenCalled()
    expect(addListener).not.toHaveBeenCalled()
  })

  it('네이티브에서 권한 허용 시 리스너를 걸고 register를 호출한다', async () => {
    getPlatform.mockReturnValue('android')
    const { initPushNotifications } = await freshPush()
    await initPushNotifications()
    expect(register).toHaveBeenCalledOnce()
    expect(addListener).toHaveBeenCalledWith('registration', expect.any(Function))

    // registration 콜백이 서버에 토큰을 등록하는지
    listeners['registration']?.({ value: 'fcm-tok' })
    expect(registerDeviceToken).toHaveBeenCalledWith('fcm-tok', 'android')
  })

  it('권한이 거부되면 register하지 않는다', async () => {
    getPlatform.mockReturnValue('android')
    checkPermissions.mockResolvedValueOnce({ receive: 'denied' })
    const { initPushNotifications } = await freshPush()
    await initPushNotifications()
    expect(register).not.toHaveBeenCalled()
  })
})
