/** 라벨 + 값 KPI 카드. */
export function Kpi({ label, value, tone = 'default' }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`kpi ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
