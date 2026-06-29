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
} from 'lucide-react'
import { fetchSummary, type MobileSummary } from './api'
import { AuthError, clearToken, login } from './auth'

type Tab = 'home' | 'orders' | 'risk' | 'alerts'

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
  const orders = [...data.orders.pending, ...data.orders.fills_today]
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

export function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [data, setData] = useState<MobileSummary>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [needLogin, setNeedLogin] = useState(false)

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

  useEffect(() => {
    void refresh()
  }, [refresh])

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
          <button className="icon-btn" type="button" onClick={() => void refresh()} aria-label="새로고침" title="새로고침">
            <RefreshCw size={19} className={loading ? 'spin' : ''} />
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
        {data && tab === 'risk' ? <Risk data={data} /> : null}
        {data && tab === 'alerts' ? <section><h2>알림</h2><Alerts items={data.alerts} /></section> : null}
      </main>
      <nav className="bottom-nav">
        <button className={tab === 'home' ? 'active' : ''} onClick={() => setTab('home')}><House size={20} /><span>홈</span></button>
        <button className={tab === 'orders' ? 'active' : ''} onClick={() => setTab('orders')}><ListOrdered size={20} /><span>주문</span></button>
        <button className={tab === 'risk' ? 'active' : ''} onClick={() => setTab('risk')}><Gauge size={20} /><span>리스크</span></button>
        <button className={tab === 'alerts' ? 'active' : ''} onClick={() => setTab('alerts')}><Bell size={20} /><span>알림</span></button>
      </nav>
    </div>
  )
}
