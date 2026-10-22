import { AlertTriangle } from 'lucide-react'
import type { MobileSummary, Order, OrderPreview } from '../api'
import { Empty } from '../components/Empty'
import { ExecutionReadiness } from '../components/ExecutionReadiness'
import { money, orderStatusLabel, previewSkipLabel, sideLabel, won } from '../format'

/** 다음 거래일 예정 주문 섹션 — 핵심만: 총 예상 건수·금액 + 종목별 예상금액([실주문]/[모의] 구분). */
function OrderPreviewSection({
  preview, loading, error, onReload,
}: {
  preview: OrderPreview | null
  loading: boolean
  error: string
  onReload: () => void
}) {
  return (
    <section>
      <h2>다음 거래일 예정 주문</h2>
      {error ? <div className="error"><AlertTriangle size={20} />{error}<button type="button" onClick={onReload}>다시 시도</button></div> : null}
      {!preview && loading ? <Empty text="불러오는 중…" /> : null}
      {preview ? <PreviewBody preview={preview} /> : null}
    </section>
  )
}

function PreviewBody({ preview }: { preview: OrderPreview }) {
  if (!preview.data_available) {
    return <Empty text="후보 스냅샷 없음 — 데이터 수집 후 다시 확인하세요." />
  }
  if (preview.data_stale) {
    const blocked = preview.stale_reason === 'regime_blocked'
    return (
      <div className="status-banner danger">
        <div>
          <strong>{blocked ? '장세 차단 (수집 정상)' : '데이터 오래됨'}</strong>
          <span>
            {blocked
              ? `장세(${preview.market_regime})로 전 전략 신규 진입 차단 중`
              : '후보 생성이 지연되어 예정 주문을 표시할 수 없습니다.'}
          </span>
        </div>
      </div>
    )
  }

  const valid = preview.items.filter((it) => !it.skipped)
  const skipped = preview.items.filter((it) => it.skipped)
  const total = valid.reduce((sum, it) => sum + it.estimated_amount, 0)

  return (
    <>
      <div className="preview-summary">
        <strong>총 예상 {valid.length}건 · {money(total)}</strong>
        <span>다음 거래일 {preview.next_trading_day} · 가용 {money(preview.assumed_cash)}</span>
      </div>
      {valid.length === 0 && skipped.length === 0 ? <Empty text="예정 주문 없음" /> : null}
      {valid.length > 0 ? (
        <div className="rows">
          {valid.map((it) => (
            <article className="row" key={`${it.ticker}-${it.strategy_id}`}>
              <div>
                <strong>{it.name || it.ticker}</strong>
                <span>{it.ticker} · {it.strategy_id}</span>
              </div>
              <div className="row-end">
                <b>{money(it.estimated_amount)}</b>
                <span>{it.estimated_qty}주 · {won(it.limit_price)}</span>
                <span className={`badge ${it.live_eligible ? 'live' : 'mock'}`}>{it.live_eligible ? '실주문' : '모의'}</span>
              </div>
            </article>
          ))}
        </div>
      ) : null}
      {skipped.length > 0 ? (
        <div className="preview-skips">
          {skipped.map((it) => (
            <div key={`skip-${it.ticker}-${it.strategy_id}`}>
              <span>{it.name || it.ticker} · {it.strategy_id}</span>
              <span className="pill">{previewSkipLabel(it.skip_reason)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
}

/** 주문 탭 — 상단 예정 주문 미리보기 + 미체결/체결/만료 주문 목록. 행 탭 시 상세로 드릴다운. */
export function OrdersScreen({
  data, preview, previewLoading, previewError, onReloadPreview, onSelect,
}: {
  data: MobileSummary
  preview: OrderPreview | null
  previewLoading: boolean
  previewError: string
  onReloadPreview: () => void
  onSelect: (order: Order) => void
}) {
  const groups = [
    { label: '미체결 주문 · 과거 주문 포함', items: data.orders.pending, pending: true },
    { label: '오늘 생성 주문의 누적 체결 (KST)', items: data.orders.fills_today, pending: false },
    { label: '오늘 및 지난 7일 만료 주문 · 최대 100건', items: data.orders.expired, pending: false },
  ]
  const slip = data.orders.slippage
  return (
    <>
      <ExecutionReadiness orders={data.orders} />
      <OrderPreviewSection
        preview={preview} loading={previewLoading} error={previewError} onReload={onReloadPreview}
      />
      <section>
        <p>현재 계좌·환경의 주문 기록 · 실제 체결 시각 기준 아님 · 취소 주문의 부분 체결 포함</p>
        <p>실제 평균 절대 가격 괴리 {slip?.actual == null ? '—' : `${(slip.actual * 100).toFixed(3)}%`} · 표본 {slip?.sample_count ?? 0}건</p>
        <p>오늘 생성 주문의 누적 체결가와 주문가 비교 · 시가총액별 실측 미분류</p>
        <p>슬리피지 가정: 대형 {slip?.large_cap_assumed == null ? '—' : `${slip.large_cap_assumed * 100}%`} · 중소형 {slip?.mid_small_assumed == null ? '—' : `${slip.mid_small_assumed * 100}%`}</p>
      </section>
      {groups.map(group => <section key={group.label}>
        <h2>{group.label}</h2>
        {group.items.length === 0 ? <Empty text="해당 주문 내역이 없습니다." /> : (
          <div className="rows">
            {group.items.map((order) => (
              <article className="row tappable" key={`${order.order_id}-${order.status}`} onClick={() => onSelect(order)}>
                <div><strong>{order.name || order.ticker}</strong><span>{order.ticker} · {order.strategy_id || '수동/브로커'}</span></div>
                {/* pill 클래스는 영문 상태값을 그대로 쓴다(.pill.filled 등). 표시 문구만 한글. */}
                <div className="row-end"><b>{sideLabel(order.side)}{order.qty ? ` ${order.qty.toLocaleString('ko-KR')}주` : ''}</b>
                  {group.pending ? <span>잔량 {order.remaining_qty == null ? '확인 필요' : `${order.remaining_qty.toLocaleString('ko-KR')}주`}</span> : null}
                  {order.fill_qty != null ? <span>누적 체결 {order.fill_qty.toLocaleString('ko-KR')}주</span> : null}
                  <span className={`pill ${order.status.toLowerCase()}`}>{orderStatusLabel(order.status)}</span></div>
              </article>
            ))}
          </div>
        )}
      </section>)}
    </>
  )
}
