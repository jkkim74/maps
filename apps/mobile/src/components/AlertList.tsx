import { Activity, AlertTriangle } from 'lucide-react'
import type { Alert } from '../api'
import { Empty } from './Empty'

/** 알림 목록 — 홈 탭 최근 알림과 알림 탭에서 재사용한다. */
export function AlertList({ items }: { items: Alert[] }) {
  if (!items.length) return <Empty text="최근 알림이 없습니다." />
  return (
    <div className="alerts">
      {items.map((alert, index) => (
        <article className="alert" key={`${alert.message}-${index}`}>
          {alert.level === 'WARN' || alert.level === 'ERROR' ? <AlertTriangle size={18} /> : <Activity size={18} />}
          <div><strong>{alert.level}</strong><p>{alert.message}</p></div>
          <time>{alert.timestamp}</time>
        </article>
      ))}
    </div>
  )
}
