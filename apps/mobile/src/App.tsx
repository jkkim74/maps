import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Bell,
  CircleDollarSign,
  Gauge,
  House,
  ListOrdered,
  LogOut,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Target,
} from 'lucide-react'
import {
  approveLiquidation,
  armPick,
  disarmPick,
  fetchKillSwitches,
  fetchPicks,
  fetchSummary,
  loadCachedSummary,
  releaseKillSwitch,
  type AnalysisPick,
  type KillSwitches,
  type MobileSummary,
} from './api'
import { AuthError, clearToken, getUsername, login } from './auth'

type Tab = 'home' | 'orders' | 'risk' | 'watchlist' | 'alerts'

const money = (value: number) => `${Math.round(value).toLocaleString('ko-KR')}원`
const pct = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
const absPct = (value: number) => `${(Math.abs(value) * 100).toFixed(1)}%`

function Kpi({ label, value, tone = 'default' }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`kpi ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>
}

function Home({ data }: { data: MobileSummary }) {
  const blocked = !data.orders.auto_order_active
  return (
    <>
      <section className={`status-banner ${blocked ? 'danger' : 'ok'}`}>
        {blocked ? <ShieldX size={22} /> : <ShieldCheck size={22} />}
        <div>
          <strong>{blocked ? '자동 주문 차단됨' : '자동 주문 정상'}</strong>
          <span>{blocked ? 'Kill Switch 상태를 확인하세요.' : '운영 상태가 정상입니다.'}</span>
        </div>
      </section>
      <section className="kpi-grid">
        <Kpi label="총 자산" value={money(data.dashboard.total_assets)} />
        <Kpi label="현재 MDD" value={absPct(data.dashboard.current_mdd)} tone={data.dashboard.current_mdd < -0.2 ? 'danger' : 'info'} />
        <Kpi label="YTD 수익률" value={pct(data.dashboard.ytd_cagr)} tone="good" />
        <Kpi label="활성 전략" value={`${data.dashboard.active_strategies}개`} />
      </section>
      <section>
        <h2>오늘의 운영</h2>
        <div className="metric-list">
          <div><span>미체결 주문</span><b>{data.orders.pending.length}</b></div>
          <div><span>오늘 체결</span><b>{data.orders.fills_today.length}</b></div>
          <div><span>보유 종목</span><b>{data.risk.position_count}</b></div>
          <div><span>청산 승인 대기</span><b className={data.live_monitor.pending_approval_count ? 'text-danger' : ''}>{data.live_monitor.pending_approval_count}</b></div>
        </div>
      </section>
      <section>
        <h2>최근 알림</h2>
        <Alerts items={data.alerts.slice(0, 3)} />
      </section>
    </>
  )
}

function Orders({ data }: { data: MobileSummary }) {
  const orders = [...data.orders.pending, ...data.orders.fills_today, ...data.orders.expired]
  return (
    <section>
      <h2>주문 및 체결</h2>
      {orders.length === 0 ? <Empty text="표시할 주문 내역이 없습니다." /> : (
        <div className="rows">
          {orders.map((order) => (
            <article className="row" key={`${order.order_id}-${order.status}`}>
              <div><strong>{order.name || order.ticker}</strong><span>{order.ticker} · {order.strategy_id || 'broker'}</span></div>
              <div className="row-end"><b>{order.side} {order.qty}</b><span className={`pill ${order.status.toLowerCase()}`}>{order.status}</span></div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function Risk({ data }: { data: MobileSummary }) {
  return (
    <>
      <section className="kpi-grid">
        <Kpi label="일간 위험" value={absPct(data.risk.short_term_risk)} />
        <Kpi label="위험 한도" value={absPct(data.risk.short_term_limit)} tone="info" />
        <Kpi label="장기 위험" value={absPct(data.risk.long_term_risk)} />
        <Kpi label="장기 한도" value={absPct(data.risk.long_term_limit)} tone="info" />
        <Kpi label="최대 노출" value={absPct(data.risk.max_exposure_pct)} />
        <Kpi label="보유 종목" value={`${data.risk.position_count}개`} />
      </section>
      <section>
        <h2>보유 현황</h2>
        {data.risk.holdings.length === 0 ? <Empty text="현재 보유 종목이 없습니다." /> : (
          <div className="rows">
            {data.risk.holdings.map((holding) => (
              <article className="row" key={holding.ticker}>
                <div><strong>{holding.ticker}</strong><span>{holding.strategy_id}</span></div>
                <div className="row-end"><b>{absPct(holding.exposure_pct)}</b><span>노출 비중</span></div>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

function Alerts({ items }: { items: MobileSummary['alerts'] }) {
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

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      onSuccess()
    } catch (reason) {
      setError(reason instanceof AuthError ? reason.message : '로그인에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="brand">MAPS</span><p>Mobile Operations</p></div>
        <label>아이디
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>비밀번호
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error ? <div className="login-error">{error}</div> : null}
        <button type="submit" className="login-btn" disabled={busy}>
          {busy ? '로그인 중…' : '로그인'}
        </button>
      </form>
    </div>
  )
}

const STATE_LABEL: Record<string, string> = {
  WATCH: '관망', ARMED: '무장', BOUGHT: '보유', CLOSED: '청산', CANCELLED: '취소',
}
const won = (value: number | null) => (value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}`)

function Watchlist({
  picks, loading, error, busyId, message, onArm, onDisarm, onReload,
}: {
  picks: AnalysisPick[]
  loading: boolean
  error: string
  busyId: number | null
  message: string
  onArm: (pick: AnalysisPick) => void
  onDisarm: (pick: AnalysisPick) => void
  onReload: () => void
}) {
  return (
    <section>
      <h2>분석 워치리스트</h2>
      {message ? <div className="action-msg">{message}</div> : null}
      {error ? <div className="error"><AlertTriangle size={20} />{error}<button type="button" onClick={onReload}>다시 시도</button></div> : null}
      {loading && !picks.length ? <Empty text="불러오는 중…" /> : null}
      {!loading && !picks.length && !error ? <Empty text="워치리스트가 비어 있습니다." /> : null}
      <div className="rows">
        {picks.map((p) => {
          const busy = busyId === p.id
          const armed = p.state === 'ARMED'
          const bought = p.state === 'BOUGHT'
          return (
            <article className="pick" key={p.id}>
              <div className="pick-head">
                <div><strong>{p.name}</strong><span>{p.ticker}</span></div>
                <span className={`badge ${p.state.toLowerCase()}`}>{STATE_LABEL[p.state] ?? p.state}</span>
              </div>
              <div className="pick-prices">
                매수 {won(p.buy_price)} · 목표 {won(p.target_price)} · 손절 {won(p.stop_price)}
                {p.rr_ratio != null ? ` · R:R ${p.rr_ratio}` : ''}
              </div>
              {p.fill_price != null ? (
                <div className="pick-fill">
                  체결 {won(p.fill_price)}
                  {p.current_price != null ? ` · 현재 ${won(p.current_price)}` : ''}
                  {p.fill_price && p.current_price != null
                    ? ` (${p.current_price >= p.fill_price ? '+' : ''}${(((p.current_price - p.fill_price) / p.fill_price) * 100).toFixed(1)}%)`
                    : ''}
                </div>
              ) : null}
              <div className="pick-actions">
                <button type="button" disabled={busy || armed || bought} onClick={() => onArm(p)}>무장</button>
                <button type="button" className="ghost" disabled={busy || p.state === 'WATCH' || bought} onClick={() => onDisarm(p)}>무장해제</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function KillSwitch({
  data, loading, error, busyId, message, onApprove, onRelease,
}: {
  data: KillSwitches
  loading: boolean
  error: string
  busyId: string | null
  message: string
  onApprove: (strategyId: string) => void
  onRelease: (strategyId: string) => void
}) {
  const empty = !data.approvals.length && !data.releases.length
  return (
    <section>
      <h2>Kill-Switch</h2>
      {message ? <div className="action-msg">{message}</div> : null}
      {error ? <div className="error"><AlertTriangle size={20} />{error}</div> : null}
      {loading && empty ? <Empty text="불러오는 중…" /> : null}
      {!loading && empty && !error ? <Empty text="대기 중인 Kill-Switch 작업이 없습니다." /> : null}
      <div className="rows">
        {data.approvals.filter((k) => k.strategy_id).map((k) => (
          <article className="ks danger" key={`a-${k.strategy_id}`}>
            <div className="ks-head">
              <div><strong>{k.strategy_id}</strong><span>{k.reason || '발동됨'}</span></div>
              <span className="badge armed">청산 승인 대기</span>
            </div>
            <button
              type="button" className="danger-btn"
              disabled={busyId === k.strategy_id}
              onClick={() => onApprove(k.strategy_id as string)}
            >보유 포지션 청산 승인</button>
          </article>
        ))}
        {data.releases.filter((k) => k.strategy_id).map((k) => (
          <article className="ks" key={`r-${k.strategy_id}`}>
            <div className="ks-head">
              <div><strong>{k.strategy_id}</strong><span>청산 완료 — 해제 대기</span></div>
              <span className="badge bought">해제 대기</span>
            </div>
            <button
              type="button"
              disabled={busyId === k.strategy_id}
              onClick={() => onRelease(k.strategy_id as string)}
            >Kill-Switch 해제(신규 진입 재개)</button>
          </article>
        ))}
      </div>
    </section>
  )
}

const POLL_INTERVAL = 30000 // 30초 자동 갱신

export function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [data, setData] = useState<MobileSummary | undefined>(() => loadCachedSummary())
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [needLogin, setNeedLogin] = useState(false)
  const [picks, setPicks] = useState<AnalysisPick[]>([])
  const [picksLoading, setPicksLoading] = useState(false)
  const [picksError, setPicksError] = useState('')
  const [actionBusyId, setActionBusyId] = useState<number | null>(null)
  const [actionMsg, setActionMsg] = useState('')
  const [ks, setKs] = useState<KillSwitches>({ approvals: [], releases: [] })
  const [ksLoading, setKsLoading] = useState(false)
  const [ksError, setKsError] = useState('')
  const [ksBusyId, setKsBusyId] = useState<string | null>(null)
  const [ksMsg, setKsMsg] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await fetchSummary())
      setNeedLogin(false)
    } catch (reason) {
      if (reason instanceof AuthError) {
        setNeedLogin(true)
      } else {
        setError(reason instanceof Error ? reason.message : '데이터를 불러오지 못했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  const loadPicks = useCallback(async () => {
    setPicksLoading(true)
    setPicksError('')
    try {
      setPicks(await fetchPicks())
    } catch (reason) {
      if (reason instanceof AuthError) {
        setNeedLogin(true)
      } else {
        setPicksError(reason instanceof Error ? reason.message : '워치리스트를 불러오지 못했습니다.')
      }
    } finally {
      setPicksLoading(false)
    }
  }, [])

  const runAction = useCallback(
    async (pick: AnalysisPick, action: 'arm' | 'disarm') => {
      setActionBusyId(pick.id)
      setActionMsg('')
      try {
        if (action === 'arm') await armPick(pick.id)
        else await disarmPick(pick.id)
        setActionMsg(`${pick.name} ${action === 'arm' ? '무장됨' : '무장해제됨'}`)
        await loadPicks()
      } catch (reason) {
        if (reason instanceof AuthError) setNeedLogin(true)
        else setActionMsg(`실패: ${reason instanceof Error ? reason.message : '요청 오류'}`)
      } finally {
        setActionBusyId(null)
      }
    },
    [loadPicks],
  )

  const onArm = useCallback(
    (pick: AnalysisPick) => {
      if (window.confirm(`${pick.name}을(를) 무장하면 자동매수가 활성화됩니다. 계속할까요?`)) {
        void runAction(pick, 'arm')
      }
    },
    [runAction],
  )
  const onDisarm = useCallback((pick: AnalysisPick) => void runAction(pick, 'disarm'), [runAction])

  const loadKillSwitches = useCallback(async () => {
    setKsLoading(true)
    setKsError('')
    try {
      setKs(await fetchKillSwitches())
    } catch (reason) {
      if (reason instanceof AuthError) setNeedLogin(true)
      else setKsError(reason instanceof Error ? reason.message : 'Kill-Switch 현황을 불러오지 못했습니다.')
    } finally {
      setKsLoading(false)
    }
  }, [])

  const onApprove = useCallback(
    (strategyId: string) => {
      const ok = window.confirm(
        `⚠️ 실거래 경고\n\n[${strategyId}] 보유 포지션을 시장가로 청산 승인합니다.\n` +
        `이 작업은 되돌릴 수 없습니다. 정말 진행할까요?`,
      )
      if (!ok) return
      setKsBusyId(strategyId)
      setKsMsg('')
      approveLiquidation(strategyId, getUsername())
        .then(() => { setKsMsg(`${strategyId} 청산 승인됨`); return loadKillSwitches() })
        .catch((e) => {
          if (e instanceof AuthError) setNeedLogin(true)
          else setKsMsg(`실패: ${e instanceof Error ? e.message : '요청 오류'}`)
        })
        .finally(() => setKsBusyId(null))
    },
    [loadKillSwitches],
  )

  const onRelease = useCallback(
    (strategyId: string) => {
      const ok = window.confirm(
        `[${strategyId}] Kill-Switch를 해제하면 신규 진입이 재개됩니다.\n청산이 완료됐는지 확인했나요?`,
      )
      if (!ok) return
      setKsBusyId(strategyId)
      setKsMsg('')
      releaseKillSwitch(strategyId, getUsername())
        .then(() => { setKsMsg(`${strategyId} Kill-Switch 해제됨`); return loadKillSwitches() })
        .catch((e) => {
          if (e instanceof AuthError) setNeedLogin(true)
          else setKsMsg(`실패: ${e instanceof Error ? e.message : '요청 오류'}`)
        })
        .finally(() => setKsBusyId(null))
    },
    [loadKillSwitches],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (tab === 'watchlist') void loadPicks()
    if (tab === 'risk') void loadKillSwitches()
  }, [tab, loadPicks, loadKillSwitches])

  // 30초 자동 폴링 — 백그라운드(숨김)에서는 멈추고, 화면 복귀 시 즉시 갱신한다.
  useEffect(() => {
    if (needLogin) return
    const tick = () => {
      if (document.hidden) return
      void refresh()
      if (tab === 'watchlist') void loadPicks()
      if (tab === 'risk') void loadKillSwitches()
    }
    const timer = window.setInterval(tick, POLL_INTERVAL)
    const onVisible = () => { if (!document.hidden) tick() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [needLogin, tab, refresh, loadPicks, loadKillSwitches])

  if (needLogin) {
    return <Login onSuccess={() => void refresh()} />
  }

  const logout = () => {
    clearToken()
    setData(undefined)
    setNeedLogin(true)
  }

  return (
    <div className="app-shell">
      <header>
        <div><span className="brand">MAPS</span><p>Mobile Operations</p></div>
        <div className="header-actions">
          <button className="icon-btn" type="button" onClick={() => { void refresh(); if (tab === 'watchlist') void loadPicks() }} aria-label="새로고침" title="새로고침">
            <RefreshCw size={19} className={loading || picksLoading ? 'spin' : ''} />
          </button>
          <button className="icon-btn" type="button" onClick={logout} aria-label="로그아웃" title="로그아웃">
            <LogOut size={19} />
          </button>
        </div>
      </header>
      <main>
        {loading && !data ? <div className="loading"><RefreshCw className="spin" />운영 데이터를 불러오는 중입니다.</div> : null}
        {error ? <div className="error"><AlertTriangle size={20} />{error}<button type="button" onClick={() => void refresh()}>다시 시도</button></div> : null}
        {data && tab === 'home' ? <Home data={data} /> : null}
        {data && tab === 'orders' ? <Orders data={data} /> : null}
        {data && tab === 'risk' ? (
          <>
            <Risk data={data} />
            <KillSwitch
              data={ks} loading={ksLoading} error={ksError}
              busyId={ksBusyId} message={ksMsg}
              onApprove={onApprove} onRelease={onRelease}
            />
          </>
        ) : null}
        {tab === 'watchlist' ? (
          <Watchlist
            picks={picks} loading={picksLoading} error={picksError}
            busyId={actionBusyId} message={actionMsg}
            onArm={onArm} onDisarm={onDisarm} onReload={() => void loadPicks()}
          />
        ) : null}
        {data && tab === 'alerts' ? <section><h2>알림</h2><Alerts items={data.alerts} /></section> : null}
      </main>
      <nav className="bottom-nav">
        <button className={tab === 'home' ? 'active' : ''} onClick={() => setTab('home')}><House size={20} /><span>홈</span></button>
        <button className={tab === 'orders' ? 'active' : ''} onClick={() => setTab('orders')}><ListOrdered size={20} /><span>주문</span></button>
        <button className={tab === 'risk' ? 'active' : ''} onClick={() => setTab('risk')}><Gauge size={20} /><span>리스크</span></button>
        <button className={tab === 'watchlist' ? 'active' : ''} onClick={() => setTab('watchlist')}><Target size={20} /><span>워치</span></button>
        <button className={tab === 'alerts' ? 'active' : ''} onClick={() => setTab('alerts')}><Bell size={20} /><span>알림</span></button>
      </nav>
    </div>
  )
}
