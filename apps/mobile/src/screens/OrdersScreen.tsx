import type { MobileSummary, Order } from '../api'
import { Empty } from '../components/Empty'

/** 주문 탭 — 미체결/체결/만료 주문을 한 목록으로 표시. 행 탭 시 상세로 드릴다운. */
export function OrdersScreen({ data, onSelect }: { data: MobileSummary; onSelect: (order: Order) => void }) {
  const orders = [...data.orders.pending, ...data.orders.fills_today, ...data.orders.expired]
  return (
    <section>
      <h2>주문 및 체결</h2>
      {orders.length === 0 ? <Empty text="표시할 주문 내역이 없습니다." /> : (
        <div className="rows">
          {orders.map((order) => (
            <article className="row tappable" key={`${order.order_id}-${order.status}`} onClick={() => onSelect(order)}>
              <div><strong>{order.name || order.ticker}</strong><span>{order.ticker} · {order.strategy_id || 'broker'}</span></div>
              <div className="row-end"><b>{order.side} {order.qty}</b><span className={`pill ${order.status.toLowerCase()}`}>{order.status}</span></div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
