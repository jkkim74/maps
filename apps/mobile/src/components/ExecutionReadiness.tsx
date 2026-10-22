import { ShieldCheck, ShieldX } from 'lucide-react'
import type { MobileSummary } from '../api'

const REASON_LABEL: Record<string, string> = {
  trading_disabled: '전역 주문 OFF', dry_run: '드라이런 모드',
  real_trading_unconfirmed: '실거래 확인 미완료', broker_execution_unsupported: '지원하지 않는 주문 브로커',
  account_unobserved: '계좌 대사 근거 없음', account_environment_mismatch: '계좌 환경 불일치',
  reconciliation_stale: '계좌 대사 근거가 없거나 오래됨', account_warmup: '계좌 준비 중',
  account_blocked: '계좌 실행 차단', account_status_unknown: '계좌 상태 확인 필요',
  account_kill_switch: '계좌 Kill Switch', strategy_kill_switch: '전략 Kill Switch',
}

/** Keep server account gates and strategy-specific blocks visible on both tabs. */
export function ExecutionReadiness({ orders }: { orders: MobileSummary['orders'] }) {
  const ready = orders.execution_state === 'ready'
  const state = ready ? '자동 주문 준비됨' : orders.execution_state === 'blocked' ? '자동 주문 차단됨' : '자동 주문 확인 필요'
  const reasons = (orders.block_reasons ?? []).map(reason => REASON_LABEL[reason] ?? reason)
  const checked = orders.checked_at ? new Date(orders.checked_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '근거 없음'
  return (
    <section className={`status-banner ${ready ? 'ok' : 'danger'}`}>
      {ready ? <ShieldCheck size={22} /> : <ShieldX size={22} />}
      <div>
        <strong>{state}</strong>
        <span>{reasons.join(' · ') || (ready ? '계좌 공통 조건 통과 · 전략별 진입 조건은 별도 확인' : '실행 근거 확인 필요')}</span>
        <span>대사 확인 {checked} (KST) · 환경 {orders.environment ?? '확인 필요'}</span>
        {Object.entries(orders.strategy_blocks ?? {}).map(([strategy, blocks]) => (
          <span key={strategy}>전략 차단 {strategy}: {blocks.map(reason => REASON_LABEL[reason] ?? reason).join(' · ')}</span>
        ))}
      </div>
    </section>
  )
}
