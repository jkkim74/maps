// 네이티브 푸시(FCM) 초기화 — @capacitor/push-notifications.
//
// 웹/웹뷰(브라우저 프리뷰)에서는 네이티브 플러그인이 없으므로 no-op으로 빠진다.
// 네이티브(android/ios)에서는 권한을 요청하고, 획득한 FCM 등록 토큰을 서버에
// 등록한다. 어떤 실패도 앱을 막지 않도록 방어적으로 처리한다(콘솔 경고만).

import { Capacitor } from '@capacitor/core'
import { PushNotifications } from '@capacitor/push-notifications'

import { deregisterDeviceToken, registerDeviceToken } from './api'

let initialised = false
let lastToken: string | null = null

/** 로그인 이후 1회 호출 — 권한 요청 + 토큰 등록 리스너 연결. */
export async function initPushNotifications(): Promise<void> {
  if (initialised) return
  if (Capacitor.getPlatform() === 'web') return // 웹은 네이티브 푸시 없음
  initialised = true
  try {
    let perm = await PushNotifications.checkPermissions()
    if (perm.receive === 'prompt' || perm.receive === 'prompt-with-rationale') {
      perm = await PushNotifications.requestPermissions()
    }
    if (perm.receive !== 'granted') return

    await PushNotifications.addListener('registration', (token) => {
      lastToken = token.value
      void registerDeviceToken(token.value, Capacitor.getPlatform()).catch((e) =>
        console.warn('디바이스 토큰 등록 실패', e),
      )
    })
    await PushNotifications.addListener('registrationError', (err) => {
      console.warn('푸시 등록 오류', err)
    })
    await PushNotifications.register()
  } catch (e) {
    console.warn('푸시 초기화 실패', e)
  }
}

/** 로그아웃 시 마지막 토큰을 서버에서 비활성화한다(best-effort). */
export async function disablePushNotifications(): Promise<void> {
  if (!lastToken) return
  try {
    await deregisterDeviceToken(lastToken)
  } catch (e) {
    console.warn('디바이스 토큰 해지 실패', e)
  } finally {
    lastToken = null
  }
}
