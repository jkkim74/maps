import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bell,
  Gauge,
  House,
  ListOrdered,
  LogOut,
  RefreshCw,
  Target,
} from 'lucide-react'
import type { AnalysisPick, Order } from './api'
import { clearToken } from './auth'
import { disablePushNotifications, initPushNotifications } from './push'
import { useAuth } from './hooks/useAuth'
import { useSummary } from './hooks/useSummary'
import { useHistory } from './hooks/useHistory'
import { usePicks } from './hooks/usePicks'
import { useOrderPreview } from './hooks/useOrderPreview'
import { useKillSwitches } from './hooks/useKillSwitches'
import { usePolling } from './hooks/usePolling'
import { HomeScreen } from './screens/HomeScreen'
import { OrdersScreen } from './screens/OrdersScreen'
import { RiskScreen } from './screens/RiskScreen'
import { WatchlistScreen } from './screens/WatchlistScreen'
import { AlertsScreen } from './screens/AlertsScreen'
import { LoginScreen } from './screens/LoginScreen'
import { KillSwitchPanel } from './components/KillSwitchPanel'
import { OrderDetail } from './OrderDetail'
import { HoldingDetail } from './HoldingDetail'

type Tab = 'home' | 'orders' | 'risk' | 'watchlist' | 'alerts'

export function App() {
  const [tab, setTab] = useState<Tab>('home')
  const { needLogin, requireLogin, clearRequireLogin } = useAuth()
  const { data, error, loading, refresh, clear: clearSummary } = useSummary(requireLogin, clearRequireLogin)
  const { history, loadHistory } = useHistory(requireLogin)
  const {
    picks, loading: picksLoading, error: picksError,
    busyId: actionBusyId, message: actionMsg,
    load: loadPicks, onArm, onDisarm,
  } = usePicks(requireLogin)
  const {
    preview, loading: previewLoading, error: previewError, load: loadPreview,
  } = useOrderPreview(requireLogin)
  const {
    data: ks, loading: ksLoading, error: ksError,
    busyId: ksBusyId, message: ksMsg,
    load: loadKillSwitches, onApprove, onRelease,
  } = useKillSwitches(requireLogin)
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null)
  const [selectedPick, setSelectedPick] = useState<AnalysisPick | null>(null)
  const pushInitRef = useRef(false)

  useEffect(() => {
    void refresh()
    void loadHistory()
  }, [refresh, loadHistory])

  // 로그인(인증 확립) 이후 1회 네이티브 푸시를 초기화한다(웹은 push.ts에서 no-op).
  useEffect(() => {
    if (needLogin || pushInitRef.current) return
    pushInitRef.current = true
    void initPushNotifications()
  }, [needLogin])

  useEffect(() => {
    if (tab === 'watchlist') void loadPicks()
    if (tab === 'risk') void loadKillSwitches()
    if (tab === 'orders') void loadPreview()
  }, [tab, loadPicks, loadKillSwitches, loadPreview])

  // 탭 전환 시 열려 있던 드릴다운 상세 화면을 닫는다.
  useEffect(() => {
    setSelectedOrder(null)
    setSelectedPick(null)
  }, [tab])

  // 30초 자동 폴링 — 백그라운드(숨김)에서는 멈추고, 화면 복귀 시 즉시 갱신한다.
  const pollTick = useCallback(() => {
    void refresh()
    if (tab === 'home') void loadHistory()
    if (tab === 'watchlist') void loadPicks()
    if (tab === 'risk') void loadKillSwitches()
    if (tab === 'orders') void loadPreview()
  }, [tab, refresh, loadHistory, loadPicks, loadKillSwitches, loadPreview])
  usePolling(pollTick, !needLogin)

  if (needLogin) {
    return <LoginScreen onSuccess={() => { void refresh(); void loadHistory() }} />
  }

  const logout = () => {
    void disablePushNotifications()
    pushInitRef.current = false
    clearToken()
    clearSummary()
    requireLogin()
  }

  return (
    <div className="app-shell">
      <header>
        <div><span className="brand">MAPS</span><p>Mobile Operations</p></div>
        <div className="header-actions">
          <button className="icon-btn" type="button" onClick={() => { void refresh(); if (tab === 'watchlist') void loadPicks(); if (tab === 'orders') void loadPreview() }} aria-label="새로고침" title="새로고침">
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
        {data && tab === 'home' ? <HomeScreen data={data} history={history} /> : null}
        {data && tab === 'orders' ? (
          selectedOrder
            ? <OrderDetail order={selectedOrder} onBack={() => setSelectedOrder(null)} />
            : <OrdersScreen
                data={data} onSelect={setSelectedOrder}
                preview={preview} previewLoading={previewLoading} previewError={previewError}
                onReloadPreview={() => void loadPreview()}
              />
        ) : null}
        {data && tab === 'risk' ? (
          <>
            <RiskScreen data={data} />
            <KillSwitchPanel
              data={ks} loading={ksLoading} error={ksError}
              busyId={ksBusyId} message={ksMsg}
              onApprove={onApprove} onRelease={onRelease}
            />
          </>
        ) : null}
        {tab === 'watchlist' ? (
          selectedPick
            ? <HoldingDetail
                pick={picks.find((p) => p.id === selectedPick.id) ?? selectedPick}
                busy={actionBusyId === selectedPick.id}
                onBack={() => setSelectedPick(null)}
                onArm={onArm} onDisarm={onDisarm}
              />
            : <WatchlistScreen
                picks={picks} loading={picksLoading} error={picksError}
                busyId={actionBusyId} message={actionMsg}
                onArm={onArm} onDisarm={onDisarm} onReload={() => void loadPicks()}
                onSelect={setSelectedPick}
              />
        ) : null}
        {data && tab === 'alerts' ? <AlertsScreen items={data.alerts} /> : null}
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
