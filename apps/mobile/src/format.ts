// 공용 표시 포매터 — 화면/컴포넌트에서 재사용한다.

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
