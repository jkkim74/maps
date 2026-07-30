// 공용 표시 포매터 — 화면/컴포넌트에서 재사용한다.

import type { Holding } from './api'

/** 정수 원화(1,234,567원). */
export const money = (value: number): string => `${Math.round(value).toLocaleString('ko-KR')}원`

/** 부호 포함 퍼센트(+12.3% / -4.5%). */
export const pct = (value: number): string => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`

/** 절댓값 퍼센트(12.3%). */
export const absPct = (value: number): string => `${(Math.abs(value) * 100).toFixed(1)}%`

/** 가격(원 단위, null이면 대시). 단위 접미사는 붙이지 않는다. */
export const won = (value: number | null): string =>
  value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}`

/** 워치리스트 픽 상태 라벨. */
export const STATE_LABEL: Record<string, string> = {
  WATCH: '관망',
  ARMED: '무장',
  BOUGHT: '보유',
  CLOSED: '청산',
  CANCELLED: '취소',
}

/** 픽 만료 사유 라벨. 서버가 내려준 코드를 라벨로만 바꾼다(판정은 서버가 한다). */
export const STALE_REASON_LABEL: Record<string, string> = {
  expired: '기준일 만료',
}

/** 만료 배지 문구. 신선하면 null. */
export const staleLabel = (pick: {
  data_stale?: boolean
  stale_reason?: string | null
  age_trading_days?: number | null
}): string | null => {
  if (!pick.data_stale) return null
  const base = STALE_REASON_LABEL[pick.stale_reason ?? 'expired'] ?? '기준일 만료'
  return pick.age_trading_days != null ? `${base} ${pick.age_trading_days}거래일` : base
}

/** 장세(시장 국면) 라벨. */
export const REGIME_LABEL: Record<string, string> = {
  strong: '강세',
  mixed: '혼조',
  weak: '약세',
  unknown: '판정없음',
}

/** 변동성 국면 라벨. */
export const VOL_LABEL: Record<string, string> = {
  low: '낮음',
  normal: '보통',
  high: '높음',
}

/** 주봉 추세 라벨. */
export const weeklyLabel = (v: string): string =>
  v === 'pass' ? '통과' : v === 'fail' ? '미달' : '—'

/**
 * 장세 → 배너 톤. 웹 대시보드(static/js/app.js) 규칙을 미러:
 * 주봉 fail이면 무조건 danger(빨강), strong→ok(녹), weak→warn(앰버), 그 외→info(파랑).
 */
export const regimeTone = (regime: string, weeklyTrend: string): 'ok' | 'warn' | 'danger' | 'info' => {
  if (weeklyTrend === 'fail') return 'danger'
  if (regime === 'strong') return 'ok'
  if (regime === 'weak') return 'warn'
  return 'info'
}

/** 주문 방향 라벨. */
export const SIDE_LABEL: Record<string, string> = { buy: '매수', sell: '매도' }

/** 주문 상태 라벨. */
export const ORDER_STATUS_LABEL: Record<string, string> = {
  filled: '체결',
  partially_filled: '부분체결',
  partial: '부분체결',
  pending: '미체결',
  expired: '만료',
  cancelled: '취소',
  canceled: '취소',
  rejected: '거부',
}

// 서버는 같은 값을 대소문자 섞어 내려준다(orders.py의 필터가 'filled'/'FILLED',
// 'pending'/'PENDING' 을 모두 받는다). 반드시 소문자로 정규화해 조회한다.

/** 매수/매도 한글 라벨. 미지의 값은 원문 유지. */
export const sideLabel = (value: string): string => SIDE_LABEL[value.toLowerCase()] ?? value

/** 주문 상태 한글 라벨. 미지의 값은 원문 유지. */
export const orderStatusLabel = (value: string): string =>
  ORDER_STATUS_LABEL[value.toLowerCase()] ?? value

/**
 * 브로커 조회 상태 → 경고 문구. 정상(ok)이면 null.
 * 웹 대시보드(static/js/app.js) 문구를 미러한다 — 앱만 조용히 축소된 목록을
 * 보여주면 사용자가 보유가 사라진 것으로 오해한다.
 */
export const brokerNotice = (status: string): string | null => {
  if (!status || status === 'ok') return null
  if (status === 'fallback') {
    return 'DB 기록 기반 근사 보유 내역입니다 (실시간 아님, 비중 미계산)'
  }
  return '브로커 연결 실패 — 보유 내역을 조회할 수 없습니다'
}

export type HoldingTotals = {
  count: number
  /** Σ 평가금액. 한 건이라도 실시간 값이 없으면 null. */
  marketValue: number | null
  /** Σ평가금액 / Σ(진입가 × 수량) − 1. 계산 불가면 null. */
  pnlPct: number | null
}

/**
 * 보유 합계. 홈 요약과 리스크 탭이 **같은 식**을 쓰도록 여기 한 곳에 둔다.
 *
 * 손익은 비중 가중이어야 한다 — `pnl_pct` 의 단순 평균은 큰 포지션과 작은
 * 포지션을 같게 취급해 실제와 어긋난다. 그래서 수량이 필요하다.
 */
export const holdingsTotals = (holdings: Holding[]): HoldingTotals => {
  let marketValue = 0
  let cost = 0
  let priced = 0
  for (const h of holdings) {
    if (h.market_value == null || !h.quantity) continue
    marketValue += h.market_value
    cost += h.entry_price * h.quantity
    priced += 1
  }
  // 일부만 실시간이면 합계를 내지 않는다(틀린 총액보다 공백이 낫다).
  const complete = holdings.length > 0 && priced === holdings.length
  return {
    count: holdings.length,
    marketValue: complete ? marketValue : null,
    pnlPct: complete && cost > 0 ? marketValue / cost - 1 : null,
  }
}

/** 예정 주문 스킵 사유 → 짧은 한글 라벨. */
export const previewSkipLabel = (reason: string | null): string => {
  if (!reason) return '스킵'
  if (reason === 'gap_exceeded') return 'GAP초과'
  if (reason === 'insufficient_cash') return '수량부족'
  if (reason === 'no_entry_signal') return '진입신호없음'
  // preferred_regime_mismatch:*, weekly_trend_fail, weak_high_vol 등 장세 계열
  if (reason.startsWith('preferred_regime_mismatch') || reason.includes('weekly_trend') || reason.includes('regime') || reason.includes('weak')) {
    return '장세차단'
  }
  return reason
}
