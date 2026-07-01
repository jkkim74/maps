import type { Alert } from '../api'
import { AlertList } from '../components/AlertList'

/** 알림 탭 — 전체 알림 목록. */
export function AlertsScreen({ items }: { items: Alert[] }) {
  return (
    <section>
      <h2>알림</h2>
      <AlertList items={items} />
    </section>
  )
}
