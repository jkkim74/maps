import { ArrowLeft } from 'lucide-react'
import type { Order } from './api'
import { orderStatusLabel, sideLabel } from './format'

// 주문/체결 드릴다운 상세. 목록에서 선택한 Order를 그대로 받아 표시하고,
// onBack으로 목록으로 돌아간다. 별도 서버 호출 없이 목록 데이터만 사용한다.

const won = (value?: number | null) =>
  value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}원`

/** 선택한 주문/체결 항목의 상세 화면. */
export function OrderDetail({ order, onBack }: { order: Order; onBack: () => void }) {
  const qty = order.qty ?? order.fill_qty ?? null
  return (
    <section className="detail">
      <button type="button" className="detail-back" onClick={onBack}>
        <ArrowLeft size={18} />목록으로
      </button>
      <div className="detail-head">
        <div>
          <strong>{order.name || order.ticker}</strong>
          <span>{order.ticker}</span>
        </div>
        {/* pill 클래스는 영문 상태값 유지(.pill.filled 등), 표시 문구만 한글. */}
        <span className={`pill ${order.status.toLowerCase()}`}>{orderStatusLabel(order.status)}</span>
      </div>
      <div className="detail-list">
        <div><span>구분</span><b>{sideLabel(order.side)}</b></div>
        <div><span>수량</span><b>{qty == null ? '—' : `${qty.toLocaleString('ko-KR')}주`}</b></div>
        <div><span>주문가</span><b>{won(order.order_price)}</b></div>
        <div><span>체결가</span><b>{won(order.fill_price)}</b></div>
        <div><span>전략</span><b>{order.strategy_id ?? '수동/브로커'}</b></div>
        <div><span>주문번호</span><b className="detail-mono">{order.order_id}</b></div>
        <div><span>생성시각</span><b>{order.created_at || '—'}</b></div>
      </div>
    </section>
  )
}
