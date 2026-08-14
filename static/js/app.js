/* MAPS Frontend App v0.2.0 */
'use strict';

// ── API 헬퍼 ──────────────────────────────────────────────────────────────────
// 인증 만료/미인증(401) 시 로그인 페이지로 보낸다.
function _redirectToLogin() {
  const next = encodeURIComponent(location.pathname + location.search);
  window.location.href = '/login?next=' + next;
}

async function apiFetch(path) {
  const res = await fetch('/api/v1' + path);
  if (res.status === 401) { _redirectToLogin(); throw new Error('unauthorized'); }
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

// 부가 정보 조회용. 실패하면 null 을 돌려주고 **로그인으로 보내지 않는다.**
// `apiFetch` 는 401 에서 throw 보다 먼저 리다이렉트해서 try/catch 로 못 막는다 —
// 인증이 꺼진 배포에서는 `/users/me` 가 401 이라 화면이 통째로 튄다.
async function apiFetchQuiet(path) {
  try {
    const res = await fetch('/api/v1' + path);
    return res.ok ? await res.json() : null;
  } catch (_) {
    return null;
  }
}

async function apiPost(path, body) {
  const res = await fetch('/api/v1' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { _redirectToLogin(); throw new Error('unauthorized'); }
  if (!res.ok) {
    let msg = `API POST ${path} → ${res.status}`;
    try { const j = await res.json(); msg = j.detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// ── 포맷 유틸 ────────────────────────────────────────────────────────────────
const fmt = {
  pct:    v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%',
  pct1:   v => v == null ? '—' : (v * 100).toFixed(1) + '%',
  // 소수점 3자리 — 수수료·슬리피지처럼 0.05% 수준의 소액 값에 사용
  pct3:   v => v == null ? '—' : (v * 100).toFixed(3).replace(/\.?0+$/, '') + '%',
  num1:   v => v == null ? '—' : v.toFixed(1),
  num2:   v => v == null ? '—' : v.toFixed(2),
  krw:    v => v == null ? '—' : '₩' + v.toLocaleString('ko-KR'),
  date:   v => v ? v.slice(0, 10) : '—',
  score:  v => v == null ? '—' : Math.round(v).toString(),
};

function badge(text, cls) {
  return `<span class="badge badge-${cls}">${text}</span>`;
}

function stageBadge(stage) {
  const map = {
    live: ['LIVE', 'live'], live_candidate: ['LIVE CAND', 'live'],
    mock_candidate: ['MOCK', 'mock'], alert_only: ['ALERT', 'alert'],
    research: ['RESEARCH', 'research'], rejected: ['REJECTED', 'fail'],
  };
  const [label, cls] = map[stage] || [stage, 'info'];
  return badge(label, cls);
}

function passBadge(passed) {
  return passed ? badge('PASS', 'pass') : badge('FAIL', 'fail');
}

function directionBadge(direction) {
  const map = {
    up: ['UP', 'pass'],
    down: ['DOWN', 'fail'],
    flat: ['FLAT', 'info'],
  };
  const [label, cls] = map[direction] || [direction ?? '—', 'info'];
  return badge(label, cls);
}

function loading(id) {
  document.getElementById(id).innerHTML =
    '<div class="loading"><div class="spinner"></div>로딩 중...</div>';
}

function empty(id, msg = '데이터 없음') {
  document.getElementById(id).innerHTML =
    `<div class="empty-state"><div class="empty-icon">○</div><div class="empty-text">${msg}</div></div>`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// ── Plotly 공통 설정 ─────────────────────────────────────────────────────────
const LAYOUT_BASE = {
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  font: { family: 'JetBrains Mono, IBM Plex Sans KR, sans-serif', color: '#8892a4', size: 11 },
  margin: { l: 48, r: 16, t: 16, b: 40 },
  xaxis: { gridcolor: '#2a3145', linecolor: '#2a3145', tickcolor: '#525c72' },
  yaxis: { gridcolor: '#2a3145', linecolor: '#2a3145', tickcolor: '#525c72' },
  legend: { bgcolor: 'transparent', bordercolor: '#2a3145' },
};
const CONFIG = { displayModeBar: false, responsive: true };

// ── SCR-01 대시보드 ───────────────────────────────────────────────────────────
async function loadDashboard() {
  loading('kpi-area');
  loading('alerts-area');
  loading('contrib-area');
  try {
    const d = await apiFetch('/dashboard');

    // KPI
    document.getElementById('kpi-area').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">총 자산</div><div class="kpi-value">${fmt.krw(d.total_assets)}</div><div class="kpi-sub">${fmt.pct(d.total_assets_mom_pct)} MoM</div></div>
        <div class="kpi-card"><div class="kpi-label">YTD CAGR</div><div class="kpi-value">${fmt.pct(d.ytd_cagr)}</div><div class="kpi-sub">목표 20% 대비</div></div>
        <div class="kpi-card ${d.current_mdd < -0.2 ? 'warn' : ''}"><div class="kpi-label">현재 MDD</div><div class="kpi-value">${fmt.pct(d.current_mdd)}</div><div class="kpi-sub">한도 28% 대비</div></div>
        <div class="kpi-card"><div class="kpi-label">Sharpe (1Y)</div><div class="kpi-value">${fmt.num2(d.sharpe_1y)}</div><div class="kpi-sub">목표 1.0 이상</div></div>
        <div class="kpi-card info"><div class="kpi-label">활성 전략</div><div class="kpi-value">${d.active_strategies}</div><div class="kpi-sub">${d.live_count} Live · ${d.mock_count} Mock</div></div>
      </div>`;

    // 전략 기여도
    if (d.contributions.length === 0) {
      empty('contrib-area', '전략 데이터 없음');
    } else {
      const rows = d.contributions.map(c =>
        `<tr><td class="mono">${c.strategy_id}</td><td>${fmt.pct(c.contribution_pct)}</td><td>${stageBadge(c.stage)}</td></tr>`
      ).join('');
      document.getElementById('contrib-area').innerHTML =
        `<table><thead><tr><th>전략 ID</th><th>기여</th><th>상태</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    // 알림
    if (d.alerts.length === 0) {
      empty('alerts-area', '최근 알림 없음');
    } else {
      const items = d.alerts.map(a =>
        `<div class="alert-item"><div class="alert-dot ${a.level}"></div><div class="alert-msg">${a.message}</div><div class="alert-time">${a.timestamp}</div></div>`
      ).join('');
      document.getElementById('alerts-area').innerHTML = items;
    }
  } catch (e) {
    document.getElementById('kpi-area').innerHTML = `<div class="empty-state"><div class="empty-text">오류: ${e.message}</div></div>`;
  }
}

// ── SCR-02 전략 관리 ─────────────────────────────────────────────────────────
const REGIME_LABELS = { strong: '강세', mixed: '혼조', weak: '약세' };

function regimeBadges(regimes) {
  if (!regimes || regimes.length === 0) return '<span class="text-muted">—</span>';
  const cls = { strong: 'pass', mixed: 'info', weak: 'warn' };
  return regimes.map(r => badge(REGIME_LABELS[r] || r, cls[r] || 'info')).join(' ');
}

function switchStrategyTab(tab) {
  // 상세 패널은 어느 탭으로 옮겨가든 닫는다.
  document.getElementById('strategy-detail').classList.add('hidden');
  document.getElementById('tab-ops').classList.toggle('hidden', tab !== 'ops');
  document.getElementById('tab-guide').classList.toggle('hidden', tab !== 'guide');
  document.querySelectorAll('.tab-bar .tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  if (tab === 'guide') loadStrategyCards();
}

async function loadStrategies() {
  loading('strategies-area');
  try {
    const d = await apiFetch('/strategies');
    if (d.strategies.length === 0) { empty('strategies-area', '등록된 전략 없음'); return; }
    _strategyCache = d.strategies;

    const rows = d.strategies.map(s => `
      <tr class="${s.has_guide ? 'row-clickable' : ''}"
          ${s.has_guide ? `onclick="showStrategyDetail('${esc(s.strategy_id)}')"` : ''}>
        <td>
          <div>${esc(s.display_name)}</div>
          <div class="mono text-muted" style="font-size:11px">${esc(s.strategy_id)}</div>
        </td>
        <td>${stageBadge(s.stage)}</td>
        <td class="mono">${fmt.score(s.tradeability_score)}</td>
        <td class="mono">${fmt.score(s.plateau_score)}</td>
        <td class="mono">${s.mc_mdd_p95 != null ? fmt.pct1(s.mc_mdd_p95) : '—'}</td>
        <td>${s.wfa_passed != null ? passBadge(s.wfa_passed) : '—'}</td>
        <td>${regimeBadges(s.preferred_regimes)}</td>
        <td>${s.promotion_pending ? badge('대기', 'warn') : '—'}</td>
      </tr>`).join('');

    document.getElementById('strategies-area').innerHTML = `
      <div class="flex-between mb-16">
        <span class="text-muted">전체 ${d.total}개 · 승격 대기 ${d.pending_promotions}건</span>
        <span class="text-muted" style="font-size:12px">행을 클릭하면 전략 설명이 열립니다</span>
      </div>
      <table><thead><tr>
        <th>전략</th><th>단계</th><th>Tradeability</th><th>Plateau</th><th>MC MDD p95</th><th>WFA</th><th>선호 장세</th><th>승격</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { empty('strategies-area', `오류: ${e.message}`); }
}

let _strategyCache = null;

async function loadStrategyCards() {
  const host = document.getElementById('strategy-cards');
  if (host.dataset.loaded === '1') return;
  loading('strategy-cards');
  try {
    if (!_strategyCache) _strategyCache = (await apiFetch('/strategies')).strategies;
    const cards = _strategyCache.filter(s => s.has_guide);
    if (cards.length === 0) { empty('strategy-cards', '설명이 등록된 전략 없음'); return; }

    host.innerHTML = `<div class="strat-grid">${cards.map(s => `
      <div class="strat-card" onclick="showStrategyDetail('${esc(s.strategy_id)}')">
        <div class="strat-card-name">${esc(s.display_name)}</div>
        <div class="strat-card-id">${esc(s.strategy_id)}</div>
        <div class="strat-card-summary">${esc(s.summary || '')}</div>
        <div class="strat-card-meta">
          ${regimeBadges(s.preferred_regimes)}
          ${s.stop_loss_pct != null ? badge('손절 ' + fmt.pct1(s.stop_loss_pct), 'info') : ''}
        </div>
      </div>`).join('')}</div>`;
    host.dataset.loaded = '1';
  } catch (e) { empty('strategy-cards', `오류: ${e.message}`); }
}

function paramTable(params) {
  const keys = Object.keys(params || {});
  if (keys.length === 0) return '<span class="text-muted">—</span>';
  return keys.map(k => `${esc(k)} = <span class="mono">${esc(params[k])}</span>`).join(' · ');
}

async function showStrategyDetail(strategyId) {
  const panel = document.getElementById('strategy-detail');
  document.getElementById('tab-ops').classList.add('hidden');
  document.getElementById('tab-guide').classList.add('hidden');
  panel.classList.remove('hidden');
  loading('strategy-detail-body');

  try {
    const g = await apiFetch(`/strategies/guide/${encodeURIComponent(strategyId)}`);
    const rules = (title, items) => `
      <div class="strat-rule-box">
        <div class="strat-rule-head">${title}</div>
        <ul>${items.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      </div>`;

    document.getElementById('strategy-detail-body').innerHTML = `
      <div class="strat-detail-head">
        <div>
          <div class="strat-detail-title">${esc(g.display_name)}</div>
          <div class="mono text-muted" style="font-size:12px">${esc(g.strategy_id)}
            ${g.strategy_group ? '· ' + esc(g.strategy_group) : ''}</div>
        </div>
        <button class="btn-copy" onclick="closeStrategyDetail()">← 목록</button>
      </div>

      <div class="strat-detail-idea">${esc(g.idea)}</div>

      <div class="kpi-grid mb-16">
        <div class="kpi-card">
          <div class="kpi-label">선호 장세</div>
          <div class="kpi-value" style="font-size:18px">${(g.preferred_regimes || []).map(r => REGIME_LABELS[r] || r).join(' · ') || '—'}</div>
          <div class="kpi-sub">이 외 장세에서는 매수 안 함</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">고정 손절</div>
          <div class="kpi-value">${g.stop_loss_pct != null ? fmt.pct1(g.stop_loss_pct) : '—'}</div>
          <div class="kpi-sub">ATR × ${g.atr_multiplier != null ? fmt.num1(g.atr_multiplier) : '—'} 중 넓은 쪽</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">허용 MDD</div>
          <div class="kpi-value">${g.mdd_limit != null ? fmt.pct1(g.mdd_limit) : '—'}</div>
          <div class="kpi-sub">MC p95 한도</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">기본 파라미터</div>
          <div class="kpi-value" style="font-size:13px;line-height:1.6">${paramTable(g.default_params)}</div>
          <div class="kpi-sub">default_params</div>
        </div>
      </div>

      <div class="strat-rule-cols">
        ${rules('진입 조건 (모두 만족)', g.entry_rules)}
        ${rules('청산 조건', g.exit_rules)}
      </div>

      ${g.guide_text ? `
        <div class="strat-guide-head">
          <span class="strat-rule-head" style="margin:0">초보자용 가이드 (블로그 원고)</span>
          <button class="btn-copy" id="guide-copy-btn" onclick="copyGuideText(this)">전체 복사</button>
        </div>
        <div class="strat-guide-text" id="guide-text">${esc(g.guide_text)}</div>
      ` : '<div class="text-muted">가이드 원고 파일이 없습니다.</div>'}`;
  } catch (e) {
    empty('strategy-detail-body', `오류: ${e.message}`);
  }
}

function closeStrategyDetail() {
  const wasGuideTab = document.querySelector('.tab-bar .tab-btn.active')?.dataset.tab === 'guide';
  switchStrategyTab(wasGuideTab ? 'guide' : 'ops');
}

// 전략 가이드·일일 원고 공용. 둘 다 네이버 블로그에 그대로 붙여넣는 평문이라
// 복사 동작이 같아야 한다 — 구현을 나누면 한쪽만 고쳐지고 조용히 어긋난다.
async function copyGuideText(btn, sourceId = 'guide-text') {
  // textContent 로 읽어야 esc() 로 넣은 &amp; 같은 엔티티가 원문으로 돌아온다.
  const text = document.getElementById(sourceId).textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = '복사됨 ✓';
    btn.classList.add('done');
    setTimeout(() => { btn.textContent = '전체 복사'; btn.classList.remove('done'); }, 2000);
  } catch (_) {
    btn.textContent = '복사 실패';
    setTimeout(() => { btn.textContent = '전체 복사'; }, 2000);
  }
}

// ── SCR-03 장세 · 팩터 ───────────────────────────────────────────────────────
function kospiTsLabel(ts) {
  if (ts == null) return { text: '—', cls: '' };
  if (ts >= 80) return { text: '강한 상승', cls: 'pass' };
  if (ts >= 60) return { text: '약한 상승', cls: 'pass' };
  if (ts >= 40) return { text: '중립',     cls: 'info' };
  if (ts >= 20) return { text: '약한 하락', cls: 'warn' };
  return              { text: '강한 하락', cls: 'fail' };
}

async function loadMarket() {
  loading('market-kpi');
  loading('market-assets');
  try {
    const d = await apiFetch('/market');
    const regimeClass = d.weekly_trend === 'fail' ? 'fail' : d.regime === 'strong' ? 'pass' : d.regime === 'weak' ? 'warn' : 'info';
    const ts = d.kospi_ts;
    const tsInfo = kospiTsLabel(ts);
    document.getElementById('market-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${regimeClass}">
          <div class="kpi-label">Market Regime</div>
          <div class="kpi-value">${(d.regime ?? '—').toUpperCase()}</div>
          <div class="kpi-sub">Weekly ${d.weekly_trend ?? '—'}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Entry Limit</div>
          <div class="kpi-value">${fmt.pct1(d.limit_ratio)}</div>
          <div class="kpi-sub">장세 × 주봉 필터</div>
        </div>
        <div class="kpi-card ${tsInfo.cls}">
          <div class="kpi-label">KOSPI TS</div>
          <div class="kpi-value">${ts != null ? fmt.score(ts) : '—'}</div>
          <div class="kpi-sub">${tsInfo.text}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Updated</div>
          <div class="kpi-value">${fmt.date(d.updated_at)}</div>
          <div class="kpi-sub">KRX index weekly</div>
        </div>
      </div>`;

    if (!d.assets || d.assets.length === 0) {
      empty('market-assets', '장세 데이터 없음');
      return;
    }

    const rows = d.assets.map(a => `
      <tr>
        <td>${a.name}</td>
        <td>${directionBadge(a.direction)}</td>
        <td class="mono">${a.value == null ? '—' : a.value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</td>
      </tr>`).join('');
    document.getElementById('market-assets').innerHTML =
      `<table><thead><tr><th>자산군</th><th>방향</th><th>최근 주봉 종가</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    empty('market-kpi', `오류: ${e.message}`);
    empty('market-assets', '');
  }
}

// ── SCR-04 종목 후보 ─────────────────────────────────────────────────────────
const _CANDIDATE_STRATEGIES = [
  'pullback_v3','pullback_v2','ath_breakout_v1','ath_breakout_v2',
  'donchian_v1','donchian_v2','multi_asset_trend_v1',
];

function aiScoreBadge(score) {
  if (score == null) return '<span class="text-muted">—</span>';
  const v = Math.round(score);
  const cls = v >= 80 ? 'pass' : v >= 60 ? 'info' : v >= 40 ? '' : v >= 20 ? 'warn' : 'fail';
  return `<span class="badge${cls ? ' badge-'+cls : ''}" title="AI 기술적 분석 점수">${v}</span>`;
}

function scoreSourceBadge(source) {
  if (source === 'AI') return '<span class="badge badge-info">AI 적용</span>';
  if (source === 'RULE_FALLBACK') return '<span class="badge badge-warn">규칙 대체</span>';
  return '<span class="badge">규칙</span>';
}

const _AI_REASON_LABELS = {
  UPTREND: '상승 추세', DOWNTREND: '하락 추세',
  MOMENTUM_POSITIVE: '모멘텀 양호', MOMENTUM_WEAK: '모멘텀 약화',
  VOLUME_CONFIRMED: '거래량 확인', VOLUME_WEAK: '거래량 부족',
  LOW_VOLATILITY: '변동성 안정', HIGH_VOLATILITY: '고변동성',
  HEALTHY_PULLBACK: '건전한 눌림', BREAKOUT_CONFIRMED: '돌파 확인',
  OVEREXTENDED: '과도한 이격', NEAR_SUPPORT: '지지선 근접',
  RESISTANCE_OVERHEAD: '상단 저항', CONFLICTING_SIGNALS: '지표 충돌',
  INSUFFICIENT_DATA: '판단 자료 부족',
};

function aiReasonText(codes) {
  if (!codes || codes.length === 0) return '<span class="text-muted">—</span>';
  return codes.map(code => esc(_AI_REASON_LABELS[code] || code)).join(', ');
}

function krPrice(p) {
  if (p == null) return '<span class="text-muted">—</span>';
  return `<span class="mono">${Math.round(p).toLocaleString('ko-KR')}</span>`;
}

async function loadCandidates() {
  loading('candidates-kpi');
  loading('candidates-area');
  try {
    const params = new URLSearchParams(location.search);
    const strategyId = params.get('strategy_id') || 'pullback_v3';

    // 전략 선택기 셀렉트박스 동기화
    const sel = document.getElementById('candidates-strategy-select');
    if (sel) sel.value = strategyId;

    const d = await apiFetch(`/candidates?strategy_id=${encodeURIComponent(strategyId)}`);

    // AI 분석 활성 여부 — 하나라도 점수가 있으면 활성
    const aiActive = d.candidates && d.candidates.some(c =>
      c.ai_score != null || (c.ai_scoring_mode && c.ai_scoring_mode !== 'off'));

    // AI 범례 섹션 표시/숨김
    const aiSection = document.getElementById('candidates-ai-section');
    const aiLegend = document.getElementById('candidates-ai-legend');
    if (aiSection) aiSection.style.display = aiActive ? '' : 'none';
    if (aiLegend) aiLegend.style.display = aiActive ? '' : 'none';

    const aiKpi = aiActive
      ? `<div class="kpi-card info"><div class="kpi-label">AI 스코어링</div><div class="kpi-value">ON</div><div class="kpi-sub">점수 출처 및 추천 순위</div></div>`
      : `<div class="kpi-card"><div class="kpi-label">AI 분석</div><div class="kpi-value">—</div><div class="kpi-sub">비활성 (설정 필요)</div></div>`;

    document.getElementById('candidates-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Universe</div><div class="kpi-value">${d.universe_count}</div><div class="kpi-sub">${d.ref_date}</div></div>
        <div class="kpi-card pass"><div class="kpi-label">Final</div><div class="kpi-value">${d.final_count}</div><div class="kpi-sub">저장 후보</div></div>
        <div class="kpi-card warn"><div class="kpi-label">Excluded</div><div class="kpi-value">${d.missing_count}</div><div class="kpi-sub">품질 필터 제외</div></div>
        ${aiKpi}
      </div>`;

    // 개인 필터 배지 — 필터로 목록이 **전부** 걸러진 경우에도 원인과 해제 경로를
    // 보여야 하므로 빈 목록 early return 보다 앞에서 만든다.
    let filterBadge = '';
    const me = await apiFetchQuiet('/users/me');
    if (me) {
      const p = me.preferences || {};
      const parts = [];
      if (p.candidate_min_score != null) parts.push(`점수 ≥ ${p.candidate_min_score}`);
      if (p.candidate_markets && p.candidate_markets.length) parts.push(p.candidate_markets.map(esc).join('·'));
      if (parts.length) {
        filterBadge = `<div id="candidates-filter-badge" class="text-muted mb-16" style="font-size:12px">
          내 필터 적용 중: ${parts.join(' / ')} · <a href="/settings">해제</a>
          <span> — 위 집계는 필터 이전의 파이프라인 값입니다</span>
        </div>`;
      }
    }

    if (!d.candidates || d.candidates.length === 0) {
      empty('candidates-area', filterBadge ? '내 필터 조건에 맞는 후보가 없습니다' : '후보 스냅샷 없음');
      if (filterBadge) document.getElementById('candidates-area').insertAdjacentHTML('afterbegin', filterBadge);
      return;
    }

    const rows = d.candidates.map(c => {
      // AI 분석 행 (메모 툴팁)
      const confidence = c.ai_confidence == null
        ? '<span class="text-muted">—</span>'
        : `<span class="mono">${Math.round(c.ai_confidence * 100)}%</span>`;
      // 목표 수익률 표시 (목표가/매수가 - 1)
      let rrHtml = '<span class="text-muted">—</span>';
      if (c.ai_target_price && c.ai_buy_price && c.ai_buy_price > 0) {
        const rr = ((c.ai_target_price - c.ai_buy_price) / c.ai_buy_price * 100).toFixed(1);
        rrHtml = `<span class="mono text-muted">+${rr}%</span>`;
      }
      return `
      <tr>
        <td class="mono">${c.ticker}</td>
        <td>${c.name} <span class="text-muted" style="font-size:10px">${c.market}</span></td>
        <td>${badge(c.ts_bucket, 'info')}</td>
        <td class="mono">${fmt.score(c.factor_score)}</td>
        <td class="mono">${fmt.score(c.trend_strength)}</td>
        <td class="mono">${fmt.score(c.rule_score)}</td>
        <td>${aiScoreBadge(c.ai_score)}</td>
        <td class="mono"><strong>${fmt.score(c.recommendation_score)}</strong> ${scoreSourceBadge(c.score_source)}</td>
        <td>${confidence}</td>
        <td>${aiReasonText(c.ai_reason_codes)}</td>
        <td>${krPrice(c.ai_buy_price)}</td>
        <td>${krPrice(c.ai_stop_price)}</td>
        <td>${krPrice(c.ai_target_price)}</td>
        <td>${rrHtml}</td>
        <td>${c.weekly_pass ? passBadge(true) : passBadge(false)}</td>
        <td class="mono">${c.estimated_qty ?? '—'}</td>
      </tr>`;
    }).join('');

    document.getElementById('candidates-area').innerHTML = filterBadge + `
      <table>
        <thead>
          <tr>
            <th>티커</th><th>종목명</th><th>TS</th>
            <th>Factor</th><th>Trend</th><th>규칙점수</th>
            <th title="구조화 AI 항목의 서버 합산 점수 (0-100)">AI점수</th>
            <th>추천점수·출처</th><th>신뢰도</th><th>AI 사유</th>
            <th title="규칙 기반 계획 매수가">계획 매수가</th>
            <th title="규칙 기반 계획 손절가">계획 손절가</th>
            <th title="규칙 기반 계획 목표가">계획 목표가</th>
            <th title="규칙 기반 목표 수익률">목표수익</th>
            <th>Weekly</th><th>수량</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    empty('candidates-kpi', `오류: ${e.message}`);
    empty('candidates-area', '');
  }
}

function changeCandidateStrategy(val) {
  const url = new URL(location.href);
  url.searchParams.set('strategy_id', val);
  history.replaceState(null, '', url.toString());
  loadCandidates();
}

// ── SCR-06 리스크 · 모니터 ───────────────────────────────────────────────────
async function loadRisk() {
  loading('risk-kpi');
  loading('risk-gauges');
  loading('risk-holdings');
  try {
    const d = await apiFetch('/risk');
    const shortRatio = d.short_term_limit > 0 ? Math.abs(d.short_term_risk) / d.short_term_limit : 0;
    const longRatio = d.long_term_limit > 0 ? d.long_term_risk / d.long_term_limit : 0;
    document.getElementById('risk-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${shortRatio >= 1 ? 'fail' : shortRatio >= 0.8 ? 'warn' : 'pass'}"><div class="kpi-label">Daily Risk</div><div class="kpi-value">${fmt.pct1(Math.abs(d.short_term_risk))}</div><div class="kpi-sub">한도 ${fmt.pct1(d.short_term_limit)}</div></div>
        <div class="kpi-card ${longRatio >= 1 ? 'fail' : longRatio >= 0.8 ? 'warn' : 'info'}"><div class="kpi-label">MC Risk Ratio</div><div class="kpi-value">${fmt.pct1(d.long_term_risk)}</div><div class="kpi-sub">전략군 최대 비율</div></div>
        <div class="kpi-card"><div class="kpi-label">Max Exposure</div><div class="kpi-value">${fmt.pct1(d.max_exposure_pct)}</div><div class="kpi-sub">단일 보유 비중</div></div>
        <div class="kpi-card"><div class="kpi-label">보유 종목</div><div class="kpi-value">${d.position_count}</div><div class="kpi-sub">활성 Kill ${d.active_kill_count ?? 0}건</div></div>
      </div>`;

    const killArea = document.getElementById('risk-kills');
    if (!d.active_kills || d.active_kills.length === 0) {
      killArea.innerHTML = '';
    } else {
      const rows = d.active_kills.map(k => `
        <tr>
          <td class="mono">${esc(k.strategy_id)}</td>
          <td>${esc(k.reason)}</td>
          <td class="mono">${k.created_at ? new Date(k.created_at).toLocaleString('ko-KR') : '—'}</td>
          <td><button class="topbar-btn" style="color:var(--color-fail);border-color:var(--color-fail)" onclick="releaseKillSwitch('${esc(k.strategy_id)}')">해제</button></td>
        </tr>`).join('');
      killArea.innerHTML = `
        <div class="section-header"><span class="section-title">🔴 Kill Switch 발동 중</span><hr></div>
        <div class="card"><div class="card-body">
          <table><thead><tr><th>전략</th><th>사유</th><th>발동 시각</th><th></th></tr></thead><tbody>${rows}</tbody></table>
          <div class="kpi-sub">해제하면 해당 전략의 신규 진입이 다시 허용됩니다.</div>
        </div></div>`;
    }

    if (!d.gauges || d.gauges.length === 0) {
      empty('risk-gauges', '리스크 게이지 없음');
    } else {
      const rows = d.gauges.map(g => {
        const ratio = Math.max(0, Math.min(g.ratio || 0, 1));
        const fillClass = ratio >= 1 ? 'fail' : ratio >= 0.8 ? 'warn' : 'info';
        return `
          <tr>
            <td class="mono">${g.strategy_id}</td>
            <td class="mono">${fmt.pct1(g.current_risk)}</td>
            <td class="mono">${fmt.pct1(g.limit)}</td>
            <td><div class="gauge-bar"><div class="gauge-fill ${fillClass}" style="width:${Math.round(ratio * 100)}%"></div></div><div class="kpi-sub">${fmt.pct1(g.ratio)}</div></td>
          </tr>`;
      }).join('');
      document.getElementById('risk-gauges').innerHTML =
        `<table><thead><tr><th>전략</th><th>현재 위험</th><th>한도</th><th>사용률</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    let brokerNotice = '';
    if (d.broker_status && d.broker_status !== 'ok') {
      const noticeText = d.broker_status === 'fallback'
        ? '브로커 연결 실패 — DB 기록 기반 근사 보유 내역입니다 (실시간 아님, 비중 미계산)'
        : '브로커 연결 실패 — 보유 내역을 조회할 수 없습니다';
      brokerNotice = `<div class="alert-item"><span class="alert-dot WARN"></span><span class="alert-msg">${noticeText}${d.broker_error ? `<div class="kpi-sub">${esc(d.broker_error)}</div>` : ''}</span></div>`;
    }
    if (!d.holdings || d.holdings.length === 0) {
      if (brokerNotice) {
        document.getElementById('risk-holdings').innerHTML = brokerNotice;
      } else {
        empty('risk-holdings', '보유 종목 없음');
      }
    } else {
      const rows = d.holdings.map(h => `
        <tr>
          <td class="mono">${h.ticker}</td>
          <td>${h.name || '—'}</td>
          <td>${h.strategy_id}</td>
          <td class="mono">${h.quantity ? h.quantity.toLocaleString('ko-KR') : '—'}</td>
          <td class="mono">${h.market_value == null ? '—' : Math.round(h.market_value).toLocaleString('ko-KR')}</td>
          <td class="mono">${h.entry_price.toLocaleString('ko-KR')}</td>
          <td class="mono">${h.current_price == null ? '—' : h.current_price.toLocaleString('ko-KR')}</td>
          <td class="mono">${fmt.pct(h.pnl_pct)}</td>
          <td class="mono">${fmt.pct1(h.exposure_pct)}</td>
          <td class="mono">${h.stop_price == null ? '—' : h.stop_price.toLocaleString('ko-KR')}</td>
        </tr>`).join('');
      document.getElementById('risk-holdings').innerHTML =
        brokerNotice +
        `<table><thead><tr><th>티커</th><th>종목명</th><th>전략</th><th>수량</th><th>평가금액</th><th>진입가</th><th>현재가</th><th>PnL</th><th>비중</th><th>손절</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  } catch (e) {
    empty('risk-kpi', `오류: ${e.message}`);
    empty('risk-gauges', '');
    empty('risk-holdings', '');
    document.getElementById('risk-kills').innerHTML = '';
  }
}

async function releaseKillSwitch(strategyId) {
  if (!confirm(`${strategyId} Kill Switch를 해제할까요?\n해제 즉시 신규 진입이 다시 허용됩니다.`)) return;
  try {
    await apiPost(`/live-monitor/${encodeURIComponent(strategyId)}/release`, { approved_by: 'dashboard' });
    loadRisk();
  } catch (e) {
    alert(`해제 실패: ${e.message}`);
  }
}

// ── SCR-08 Robustness ─────────────────────────────────────────────────────────
async function loadRobustness() {
  const params = new URLSearchParams(location.search);
  const sid = params.get('strategy') || 'pullback_v3';
  const preset = params.get('preset') || 'balanced';
  loading('robustness-kpi');
  loading('breakdown-area');
  loading('plateau-area');
  loading('mc-area');
  try {
    const d = await apiFetch(`/robustness?strategy_id=${sid}&weight_preset=${preset}`);

    document.getElementById('robustness-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${d.tradeability_score >= 75 ? 'pass' : d.tradeability_score >= 60 ? 'info' : 'fail'}">
          <div class="kpi-label">Tradeability</div>
          <div class="kpi-value">${fmt.score(d.tradeability_score)}</div>
          <div class="kpi-sub">${d.tradeability_score >= 75 ? '≥ 75 LIVE 후보' : d.tradeability_score >= 60 ? '≥ 60 MOCK 후보' : '임계 미달'}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Plateau</div>
          <div class="kpi-value">${fmt.score(d.plateau_score)}</div>
          <div class="kpi-sub">${d.plateau_grade ?? '—'} 등급</div>
        </div>
        <div class="kpi-card ${d.mc_mdd_p95 != null && d.mc_mdd_limit != null && Math.abs(d.mc_mdd_p95) > d.mc_mdd_limit ? 'fail' : ''}">
          <div class="kpi-label">MC MDD p95</div>
          <div class="kpi-value">${d.mc_mdd_p95 != null ? fmt.pct1(Math.abs(d.mc_mdd_p95)) : '—'}</div>
          <div class="kpi-sub">한도 ${d.mc_mdd_limit != null ? fmt.pct1(d.mc_mdd_limit) : '—'}</div>
        </div>
        <div class="kpi-card ${d.oos_is_g2p >= 0.6 ? 'pass' : d.oos_is_g2p != null ? 'fail' : ''}">
          <div class="kpi-label">OOS/IS G2P</div>
          <div class="kpi-value">${fmt.num2(d.oos_is_g2p)}</div>
          <div class="kpi-sub">≥ 0.6 통과</div>
        </div>
      </div>`;

    // ── 서브스코어 분해 (실제 가중치 사용)
    const bd = d.breakdown;
    if (bd) {
      const w = bd.weights || {};
      const pct = v => v != null ? `×${(v).toFixed(2)}` : '×—';
      document.getElementById('breakdown-area').innerHTML = `
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-label">Robustness (${pct(w.robustness)})</div><div class="kpi-value">${fmt.score(bd.robustness)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Risk (${pct(w.risk)})</div><div class="kpi-value">${fmt.score(bd.risk)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Recovery (${pct(w.recovery)})</div><div class="kpi-value">${fmt.score(bd.recovery)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Return (${pct(w.return ?? w.ret)})</div><div class="kpi-value">${fmt.score(bd.ret)}</div></div>
        </div>`;
    } else {
      empty('breakdown-area', '백테스트 실행 후 확인 가능합니다');
    }

    // ── Parameter Plateau 상세
    if (d.plateau_total != null) {
      const passRatio = d.plateau_total > 0 ? (d.plateau_positive / d.plateau_total * 100).toFixed(1) : '0.0';
      const paramsHtml = d.plateau_best_params
        ? Object.entries(d.plateau_best_params).map(([k, v]) => `<span class="mono">${k} = ${v}</span>`).join(' &nbsp;·&nbsp; ')
        : '—';
      document.getElementById('plateau-area').innerHTML = `
        <table><thead><tr><th>항목</th><th>값</th></tr></thead><tbody>
          <tr><td>총 파라미터 조합</td><td class="mono">${d.plateau_total}</td></tr>
          <tr><td>양(+) 수익 조합</td><td class="mono">${d.plateau_positive}</td></tr>
          <tr><td>양(+) 비율</td><td class="mono ${parseFloat(passRatio) >= 50 ? 'text-pass' : 'text-warn'}">${passRatio}%</td></tr>
          <tr><td>등급</td><td>${badge(d.plateau_grade ?? '—', d.plateau_grade === 'A' ? 'pass' : d.plateau_grade === 'B' ? 'info' : 'warn')}</td></tr>
          <tr><td>최적 파라미터</td><td>${paramsHtml}</td></tr>
        </tbody></table>`;
    } else {
      empty('plateau-area', '파라미터 검증 데이터 없음');
    }

    // ── MC MDD 분포 요약
    if (d.mc_mdd_p95 != null) {
      const mcPass = Math.abs(d.mc_mdd_p95) <= (d.mc_mdd_limit ?? 1);
      document.getElementById('mc-area').innerHTML = `
        <table><thead><tr><th>항목</th><th>값</th></tr></thead><tbody>
          <tr><td>MC MDD p95</td><td class="mono ${mcPass ? 'text-pass' : 'text-fail'}">${fmt.pct1(Math.abs(d.mc_mdd_p95))}</td></tr>
          <tr><td>허용 한도</td><td class="mono">${fmt.pct1(d.mc_mdd_limit)}</td></tr>
          <tr><td>한도 여유율</td><td class="mono">${fmt.pct1(1 - Math.abs(d.mc_mdd_p95) / d.mc_mdd_limit)} 여유</td></tr>
          <tr><td>통과 여부</td><td>${badge(mcPass ? 'PASS' : 'FAIL', mcPass ? 'pass' : 'fail')}</td></tr>
        </tbody></table>`;
    } else {
      empty('mc-area', 'Monte Carlo 데이터 없음');
    }
  } catch (e) {
    empty('robustness-kpi', `오류: ${e.message}`);
    empty('breakdown-area', '');
    empty('plateau-area', '');
    empty('mc-area', '');
  }
}

// ── SCR-13 Live Monitor ───────────────────────────────────────────────────────
async function loadLiveMonitor() {
  loading('live-kpi');
  loading('live-events');
  try {
    const d = await apiFetch('/live-monitor');

    document.getElementById('live-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${d.auto_response_active ? 'pass' : 'warn'}">
          <div class="kpi-label">자동 대응</div>
          <div class="kpi-value">${d.auto_response_active ? '활성' : '비활성'}</div>
          <div class="kpi-sub">승인 대기 ${d.pending_approval_count}건 / 해제 대기 ${d.pending_release_count}건</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">실측 MDD</div>
          <div class="kpi-value">${d.actual_mdd != null ? fmt.pct(d.actual_mdd) : '—'}</div>
          <div class="kpi-sub">한도 28% × 0.8</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">대형주 슬립</div>
          <div class="kpi-value">${d.large_slip_actual != null ? fmt.pct1(d.large_slip_actual) : '—'}</div>
          <div class="kpi-sub">KOSPI 실측 (최근 50건)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">중소형 슬립</div>
          <div class="kpi-value">${d.mid_small_slip_actual != null ? fmt.pct1(d.mid_small_slip_actual) : '—'}</div>
          <div class="kpi-sub">KOSDAQ 실측 (최근 50건)</div>
        </div>
      </div>`;

    if (d.recent_events.length === 0) {
      empty('live-events', 'Kill Switch 이벤트 없음');
    } else {
      const rows = d.recent_events.map(e => `
        <tr>
          <td class="mono">${e.strategy_id ?? '—'}</td>
          <td>${e.event_type === 'trigger' ? badge('TRIGGER', 'fail') : e.event_type === 'approved' ? badge('APPROVED', 'warn') : badge('RELEASED', 'pass')}</td>
          <td>${e.reason}</td>
          <td>${e.value ?? '—'}</td>
          <td>${e.approved_by ?? badge('대기', 'warn')}</td>
          <td class="mono text-muted">${fmt.date(e.created_at)}</td>
        </tr>`).join('');
      document.getElementById('live-events').innerHTML =
        `<table><thead><tr><th>전략</th><th>유형</th><th>사유</th><th>상세</th><th>승인</th><th>시각</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  } catch (e) {
    empty('live-kpi', `오류: ${e.message}`);
    empty('live-events', '');
  }
}

// ── SCR-14 Data Quality ───────────────────────────────────────────────────────
async function loadLiveMonitorV2() {
  loading('live-kpi');
  loading('live-events');
  try {
    const d = await apiFetch('/live-monitor');
    document.getElementById('live-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${d.auto_response_active ? 'pass' : 'warn'}">
          <div class="kpi-label">Auto Response</div>
          <div class="kpi-value">${d.auto_response_active ? 'ACTIVE' : 'PAUSED'}</div>
          <div class="kpi-sub">approval ${d.pending_approval_count} / release ${d.pending_release_count}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Actual MDD</div>
          <div class="kpi-value">${d.actual_mdd != null ? fmt.pct(d.actual_mdd) : '-'}</div>
          <div class="kpi-sub">portfolio live monitor</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Large Slip</div>
          <div class="kpi-value">${d.large_slip_actual != null ? fmt.pct1(d.large_slip_actual) : '-'}</div>
          <div class="kpi-sub">KOSPI actual (last 50 fills)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Mid/Small Slip</div>
          <div class="kpi-value">${d.mid_small_slip_actual != null ? fmt.pct1(d.mid_small_slip_actual) : '-'}</div>
          <div class="kpi-sub">KOSDAQ actual (last 50 fills)</div>
        </div>
      </div>`;

    if (d.recent_events.length === 0) {
      const failureRows = Object.entries(d.consec_failures || {}).map(([strategyId, count]) => `
        <tr>
          <td class="mono">${strategyId}</td>
          <td>${count > 0 ? badge('WARN', 'warn') : badge('CLEAR', 'pass')}</td>
          <td class="mono ${count > 0 ? 'text-warn' : ''}">${count}</td>
          <td>${count > 0 ? `연속 실패 ${count}회` : 'No Kill Switch events'}</td>
        </tr>`).join('');
      document.getElementById('live-events').innerHTML = `
        <table>
          <thead><tr><th>Strategy</th><th>Status</th><th>Failures</th><th>Note</th></tr></thead>
          <tbody>${failureRows || '<tr><td colspan="4">No live monitor data</td></tr>'}</tbody>
        </table>`;
    } else {
      const rows = d.recent_events.map(e => `
        <tr>
          <td class="mono">${e.strategy_id ?? '-'}</td>
          <td>${e.event_type === 'trigger' ? badge('TRIGGER', 'fail') : e.event_type === 'approved' ? badge('APPROVED', 'warn') : badge('RELEASED', 'pass')}</td>
          <td>${e.reason}</td>
          <td>${e.value ?? '-'}</td>
          <td>${e.approved_by ?? badge('PENDING', 'warn')}</td>
          <td class="mono text-muted">${fmt.date(e.created_at)}</td>
        </tr>`).join('');
      document.getElementById('live-events').innerHTML =
        `<table><thead><tr><th>Strategy</th><th>Type</th><th>Reason</th><th>Detail</th><th>Approval</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  } catch (e) {
    empty('live-kpi', `Error: ${e.message}`);
    empty('live-events', '');
  }
}

async function loadDataQuality() {
  loading('dq-kpi');
  loading('dq-reasons');
  loading('dq-chart');
  try {
    const d = await apiFetch('/data-quality');

    const ratioClass = d.rejection_ratio > 0.40 ? 'fail' : d.rejection_ratio > 0.30 ? 'warn' : 'pass';
    document.getElementById('dq-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">유니버스 후보</div><div class="kpi-value">${d.total_candidates}</div><div class="kpi-sub">KOSPI200 + KOSDAQ150</div></div>
        <div class="kpi-card pass"><div class="kpi-label">통과 (kept)</div><div class="kpi-value">${d.kept_count}</div><div class="kpi-sub">${fmt.pct1(d.kept_count / (d.total_candidates || 1))}</div></div>
        <div class="kpi-card ${ratioClass}"><div class="kpi-label">거부율</div><div class="kpi-value">${fmt.pct1(d.rejection_ratio)}</div><div class="kpi-sub">임계 40%</div></div>
        <div class="kpi-card ${d.alert_sent ? 'warn' : 'pass'}"><div class="kpi-label">알림</div><div class="kpi-value">${d.alert_sent ? '발송됨' : '정상'}</div><div class="kpi-sub">거부율 40% 초과 시</div></div>
      </div>`;

    if (d.rejection_reasons.length === 0) {
      empty('dq-reasons', '거부 사유 데이터 없음');
    } else {
      const rows = d.rejection_reasons.map(r => `
        <tr>
          <td class="mono">${r.reason_code}</td>
          <td>${r.description}</td>
          <td class="mono">${r.count}</td>
          <td class="mono">${fmt.pct1(r.ratio)}</td>
        </tr>`).join('');
      document.getElementById('dq-reasons').innerHTML =
        `<table><thead><tr><th>사유 코드</th><th>설명</th><th>건수</th><th>비율</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    // 거부율 추이 차트
    if (d.history_90d.length > 0) {
      const dates = d.history_90d.map(h => h.date);
      const ratios = d.history_90d.map(h => h.rejection_ratio * 100);
      Plotly.newPlot('dq-chart', [
        { x: dates, y: ratios, type: 'scatter', mode: 'lines', name: '거부율',
          line: { color: '#60a5fa', width: 2 }, fill: 'tozeroy', fillcolor: 'rgba(96,165,250,.08)' },
        { x: [dates[0], dates[dates.length - 1]], y: [40, 40], type: 'scatter', mode: 'lines',
          name: '임계 40%', line: { color: '#fbbf24', width: 1, dash: 'dot' } }
      ], { ...LAYOUT_BASE, height: 200, yaxis: { ...LAYOUT_BASE.yaxis, ticksuffix: '%' } }, CONFIG);
    } else {
      empty('dq-chart', '이력 데이터 없음 (수집 후 자동 표시)');
    }
  } catch (e) {
    empty('dq-kpi', `오류: ${e.message}`);
    empty('dq-reasons', '');
    empty('dq-chart', '');
  }
}

// ── SCR-11 WFA ────────────────────────────────────────────────────────────────
function _wfaSelectedStrategy() {
  const sel = document.getElementById('wfa-strategy');
  return sel ? sel.value : (new URLSearchParams(location.search).get('strategy') || 'pullback_v3');
}

function _renderWfaResult(d) {
  const cv = d.cv;
  document.getElementById('wfa-kpi').innerHTML = `
    <div class="kpi-grid">
      <div class="kpi-card ${d.sharpe_mean > 0 ? 'pass' : d.sharpe_mean != null ? 'fail' : ''}">
        <div class="kpi-label">Sharpe 평균 > 0</div>
        <div class="kpi-value">${fmt.num2(d.sharpe_mean)}</div>
        <div class="kpi-sub">v2.6.2 신규 조건</div>
      </div>
      <div class="kpi-card ${cv != null && cv <= 0.5 ? 'pass' : cv != null ? 'fail' : ''}">
        <div class="kpi-label">변동계수</div>
        <div class="kpi-value">${fmt.num2(cv)}</div>
        <div class="kpi-sub">≤ 0.5 통과</div>
      </div>
      <div class="kpi-card ${d.negative_folds != null && d.negative_folds <= 1 ? 'pass' : d.negative_folds != null ? 'fail' : ''}">
        <div class="kpi-label">음수 fold 수</div>
        <div class="kpi-value">${d.negative_folds ?? '—'}</div>
        <div class="kpi-sub">≤ 1 통과</div>
      </div>
      <div class="kpi-card ${d.mean_g2p >= 0.6 ? 'pass' : d.mean_g2p != null ? 'fail' : ''}">
        <div class="kpi-label">OOS/IS G2P</div>
        <div class="kpi-value">${fmt.num2(d.mean_g2p)}</div>
        <div class="kpi-sub">≥ 0.6 통과</div>
      </div>
      <div class="kpi-card ${d.passed ? 'pass' : 'fail'}">
        <div class="kpi-label">최종 판정</div>
        <div class="kpi-value">${d.passed ? 'PASS' : 'FAIL'}</div>
        <div class="kpi-sub">${d.passed ? '4/4 통과' : (d.fail_reasons.length + '건 실패')}</div>
      </div>
    </div>
    ${d.run_date ? `<div class="text-muted" style="font-size:11px;margin-top:8px">분석일: ${fmt.date(d.run_date)}</div>` : ''}
    ${d.fail_reasons.length ? `<div class="empty-sub" style="margin-top:8px">${d.fail_reasons.join(' / ')}</div>` : ''}`;

  if (d.folds.length === 0) {
    empty('wfa-folds', 'WFA 실행 후 fold 결과가 표시됩니다');
    document.getElementById('wfa-bar-chart').innerHTML =
      '<div class="empty-state" style="padding:32px"><div class="empty-sub">WFA 실행 후 표시됩니다</div></div>';
    return;
  }

  // fold 테이블
  const rows = d.folds.map(f => {
    const cls = f.oos_sharpe >= 0 ? '' : 'style="color:var(--color-fail)"';
    return `<tr>
      <td class="mono">${f.fold_idx + 1}</td>
      <td class="mono text-muted">${fmt.date(f.is_start)} ~ ${fmt.date(f.is_end)}</td>
      <td class="mono text-muted">${fmt.date(f.oos_start)} ~ ${fmt.date(f.oos_end)}</td>
      <td class="mono">${fmt.num2(f.is_sharpe)}</td>
      <td class="mono" ${cls}>${fmt.num2(f.oos_sharpe)}</td>
      <td class="mono">${fmt.num2(f.is_g2p)}</td>
      <td class="mono">${fmt.num2(f.oos_g2p)}</td>
      <td class="mono">${fmt.num2(f.g2p_ratio)}</td>
    </tr>`;
  }).join('');
  document.getElementById('wfa-folds').innerHTML = `
    <table><thead><tr>
      <th>Fold</th><th>IS 기간</th><th>OOS 기간</th>
      <th>IS Sharpe</th><th>OOS Sharpe</th><th>IS G2P</th><th>OOS G2P</th><th>G2P 비율</th>
    </tr></thead><tbody>${rows}</tbody></table>`;

  // fold별 OOS Sharpe 바 차트
  const labels = d.folds.map(f => `Fold ${f.fold_idx + 1}`);
  const values = d.folds.map(f => f.oos_sharpe);
  const colors = values.map(v => v >= 0 ? '#22c55e' : '#ef4444');
  const wfaChartEl = document.getElementById('wfa-bar-chart');
  wfaChartEl.innerHTML = '';
  Plotly.newPlot(wfaChartEl, [{
    type: 'bar',
    x: labels,
    y: values,
    marker: { color: colors },
    text: values.map(v => fmt.num2(v)),
    textposition: 'auto',
  }], {
    ...LAYOUT_BASE,
    height: 200,
    yaxis: { ...LAYOUT_BASE.yaxis, title: 'OOS Sharpe', zeroline: true, zerolinecolor: '#525c72' },
    shapes: [{ type: 'line', x0: -0.5, x1: labels.length - 0.5, y0: 0, y1: 0,
               line: { color: '#fbbf24', width: 1, dash: 'dot' } }],
  }, CONFIG);
}

async function loadWfa() {
  loading('wfa-kpi');
  loading('wfa-folds');
  try {
    const sid = _wfaSelectedStrategy();
    const d = await apiFetch(`/wfa?strategy_id=${encodeURIComponent(sid)}`);
    _renderWfaResult(d);
  } catch (e) {
    empty('wfa-kpi', `오류: ${e.message}`);
    empty('wfa-folds', '');
  }
}

async function runWfa() {
  const btn = document.querySelector('.wfa-run-btn');
  const sid = _wfaSelectedStrategy();
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 실행 중...'; }
  loading('wfa-kpi');
  loading('wfa-folds');
  try {
    const d = await apiPost(`/wfa/run?strategy_id=${encodeURIComponent(sid)}`, {});
    _renderWfaResult(d);
  } catch (e) {
    empty('wfa-kpi', `WFA 실행 실패: ${e.message}`);
    empty('wfa-folds', '');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ WFA 실행'; }
  }
}

// ── SCR-07 백테스트 콘솔 ──────────────────────────────────────────────────────
function _btHistoryEndpoint() {
  const source = document.getElementById('bt-source')?.value;
  return source ? `/backtest?source=${encodeURIComponent(source)}` : '/backtest';
}

async function loadBacktest() {
  loading('bt-progress-section');
  loading('bt-result-kpi');
  loading('bt-runs-area');
  try {
    const d = await apiFetch(_btHistoryEndpoint());
    const runs = d.recent_runs;

    // 전략 드롭다운을 API가 내려준 runnable 목록으로 채운다
    const sel = document.getElementById('bt-strategy');
    if (sel && d.available_strategies && d.available_strategies.length) {
      const current = sel.value;
      sel.innerHTML = d.available_strategies
        .map(s => `<option value="${s}"${s === current ? ' selected' : ''}>${s}</option>`)
        .join('');
    }

    // 실행 설정 패널: DB 실측값으로 채운다 (하드코딩 문구 대체)
    const period = document.getElementById('bt-period');
    if (period && d.data_start && d.data_end) {
      period.textContent = `DB 보유: ${d.data_start} ~ ${d.data_end} (비우면 전체)`;
    }
    // 기간 입력 프리필 — 사용자가 이미 입력했다면 덮어쓰지 않는다
    const startInput = document.getElementById('bt-start');
    const endInput = document.getElementById('bt-end');
    if (startInput && !startInput.value && d.data_start) startInput.value = d.data_start;
    if (endInput && !endInput.value && d.data_end) endInput.value = d.data_end;
    _btUniverseOptions = d.universe_options || {};
    onBtUniverseChange();  // 선택지 도착 후 종속 드롭다운 갱신
    const note = document.getElementById('bt-universe-note');
    if (note && d.max_tickers) {
      note.textContent = `선택한 풀에서 기간 내 거래대금 상위 최대 ${d.max_tickers}개 종목`;
    }
    const cost = document.getElementById('bt-cost');
    if (cost && d.cost_summary) cost.textContent = d.cost_summary;

    _renderBtProgress(runs.length > 0 ? runs[0] : null);
    _renderBtResultKpi(runs.find(r => r.status === 'done') ?? null);
    _renderBtRunsTable(runs);
  } catch (e) {
    empty('bt-progress-section', `오류: ${e.message}`);
    empty('bt-result-kpi', `오류: ${e.message}`);
    empty('bt-runs-area', '');
  }
}

// GET /backtest이 내려준 대상 선택지 (업종·테마 distinct)
let _btUniverseOptions = {};

function onBtUniverseChange() {
  const kind = document.getElementById('bt-universe')?.value || 'all';
  const argSel = document.getElementById('bt-universe-arg');
  const tickersInput = document.getElementById('bt-universe-tickers');
  if (!argSel || !tickersInput) return;

  const argOptions = {
    market: ['KOSPI', 'KOSDAQ'],
    sector: _btUniverseOptions.sectors || [],
    theme: _btUniverseOptions.themes || [],
    index: _btUniverseOptions.indices || ['kospi200', 'kosdaq150'],
    recent_ipo: ['90', '30', '60', '180'],
  }[kind];

  tickersInput.style.display = kind === 'custom' ? '' : 'none';
  argSel.style.display = argOptions ? '' : 'none';
  if (argOptions) {
    argSel.innerHTML = argOptions
      .map(v => `<option value="${esc(v)}">${kind === 'recent_ipo' ? `최근 ${v}일 상장` : esc(v)}</option>`)
      .join('');
  }
}

async function runBacktest() {
  const strategyId = document.getElementById('bt-strategy')?.value || 'pullback_v3';
  const btn = document.querySelector('.bt-run-btn');

  // 버튼 로딩 상태
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 실행 중...'; }

  // 진행률 섹션: 1/5 실행 중 표시
  document.getElementById('bt-progress-section').innerHTML = `
    <div class="bt-steps">
      <div class="bt-step bt-step-running">
        <div class="bt-step-num">1/5</div>
        <div class="bt-step-name">단일 백테스트</div>
        <div class="bt-step-state">실행 중...</div>
      </div>
      ${['Plateau 그리드','Walk-Forward','Monte Carlo','Block Bootstrap'].map((l,i)=>`
      <div class="bt-step bt-step-wait">
        <div class="bt-step-num">${i+2}/5</div>
        <div class="bt-step-name">${l}</div>
        <div class="bt-step-state">대기</div>
      </div>`).join('')}
    </div>`;

  const universeKind = document.getElementById('bt-universe')?.value || 'all';
  const payload = {
    strategy_id: strategyId,
    start: document.getElementById('bt-start')?.value || null,
    end: document.getElementById('bt-end')?.value || null,
    mode: document.getElementById('bt-mode')?.value || 'per_ticker',
    universe: universeKind,
    universe_arg: document.getElementById('bt-universe-arg')?.value || null,
    tickers: universeKind === 'custom'
      ? (document.getElementById('bt-universe-tickers')?.value || '')
          .split(',').map(t => t.trim()).filter(Boolean)
      : null,
  };

  try {
    const result = await apiPost('/backtest/run', payload);

    // 결과로 KPI 갱신
    _renderBtResultKpi(result);

    // 진행률: 1/5 완료로 갱신
    _renderBtProgress({ ...result, status: 'done', progress_pct: 20 });

    // 실행 목록 새로고침
    loading('bt-runs-area');
    const d = await apiFetch(_btHistoryEndpoint());
    _renderBtRunsTable(d.recent_runs);

  } catch (e) {
    document.getElementById('bt-progress-section').innerHTML =
      `<div class="empty-state" style="padding:24px">
         <div class="empty-text" style="color:var(--color-fail)">실행 실패</div>
         <div class="empty-sub">${e.message}</div>
       </div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ 실행'; }
  }
}

const BT_STEPS = [
  { label: '단일 백테스트', maxPct: 20 },
  { label: 'Plateau 그리드', maxPct: 40 },
  { label: 'Walk-Forward', maxPct: 60 },
  { label: 'Monte Carlo',   maxPct: 80 },
  { label: 'Block Bootstrap', maxPct: 100 },
];

function _renderBtProgress(run) {
  if (!run) { empty('bt-progress-section', '최근 실행 이력 없음'); return; }

  const pct = run.status === 'done' ? 100 : run.progress_pct;
  const stepsHtml = BT_STEPS.map((s, i) => {
    const minPct = i * 20;
    let cls, stateLabel;
    if (pct >= s.maxPct) {
      cls = 'bt-step-done'; stateLabel = '완료';
    } else if (pct > minPct) {
      cls = 'bt-step-running';
      stateLabel = Math.round((pct - minPct) * 5) + '%';
    } else {
      cls = 'bt-step-wait'; stateLabel = '대기';
    }
    return `<div class="bt-step ${cls}">
      <div class="bt-step-num">${i + 1}/5</div>
      <div class="bt-step-name">${s.label}</div>
      <div class="bt-step-state">${stateLabel}</div>
    </div>`;
  }).join('');

  const note = run.status === 'done'
    ? `<div class="text-muted" style="font-size:11px;margin-top:16px">전체 검증 완료 · SCR-08 (Robustness)에 자동 등록됩니다.</div>`
    : '';

  document.getElementById('bt-progress-section').innerHTML =
    `<div class="bt-steps">${stepsHtml}</div>${note}`;
}

function _renderBtResultKpi(run) {
  if (!run) {
    document.getElementById('bt-result-kpi').innerHTML =
      `<div class="empty-state" style="padding:32px"><div class="empty-sub">백테스트 실행 후 결과가 표시됩니다</div></div>`;
    return;
  }

  const cagrCls = run.net_cagr == null ? '' : run.net_cagr > 0 ? 'pass' : 'fail';
  const mddCls  = run.mdd == null ? '' : Math.abs(run.mdd) > 0.18 ? 'warn' : '';
  const shpCls  = run.sharpe == null ? '' : run.sharpe >= 1.0 ? 'pass' : '';

  const verdictCard = run.verdict ? `
      <div class="kpi-card ${run.verdict === 'PASS' ? 'pass' : 'fail'}">
        <div class="kpi-label">1차 판정</div>
        <div class="kpi-value">${run.verdict}</div>
        <div class="kpi-sub">승격 게이트 기준</div>
      </div>` : '';

  const criteriaNames = {
    avg_sharpe: '샤프', worst_mdd: 'MDD', total_trades: '거래수', avg_cagr: 'CAGR',
  };
  const criteriaLine = (run.verdict_criteria || []).map(c => {
    const mark = c.passed ? '✓' : '✗';
    const val = typeof c.value === 'number' ? (Number.isInteger(c.value) ? c.value : c.value.toFixed(3)) : c.value;
    return `<span class="${c.passed ? '' : 'text-fail'}" style="margin-right:12px">
      ${mark} ${criteriaNames[c.criterion] ?? c.criterion} ${val} (기준 ${c.threshold})</span>`;
  }).join('');

  const s = run.stats || {};
  const statsBits = [];
  if (s.win_rate != null) statsBits.push(`승률 ${fmt.pct1(s.win_rate)}`);
  if (s.payoff_ratio != null) statsBits.push(`손익비 ${s.payoff_ratio}`);
  if (s.positive_month_ratio != null) statsBits.push(`양의 월 비율 ${fmt.pct1(s.positive_month_ratio)}`);
  const yearlyTable = s.yearly_returns ? `
    <table style="margin-top:8px"><thead><tr>
      ${Object.keys(s.yearly_returns).map(y => `<th>${y}</th>`).join('')}
    </tr></thead><tbody><tr>
      ${Object.values(s.yearly_returns).map(v =>
        `<td class="mono ${v >= 0 ? '' : 'text-fail'}">${fmt.pct(v)}</td>`).join('')}
    </tr></tbody></table>` : '';

  document.getElementById('bt-result-kpi').innerHTML = `
    <div class="kpi-grid">
      ${verdictCard}
      <div class="kpi-card ${cagrCls}">
        <div class="kpi-label">Net CAGR</div>
        <div class="kpi-value">${fmt.pct(run.net_cagr)}</div>
        <div class="kpi-sub">${run.strategy_id}</div>
      </div>
      <div class="kpi-card ${mddCls}">
        <div class="kpi-label">MDD (MC p95)</div>
        <div class="kpi-value">${run.mdd != null ? fmt.pct1(Math.abs(run.mdd)) : '—'}</div>
        <div class="kpi-sub">최대 낙폭</div>
      </div>
      <div class="kpi-card ${shpCls}">
        <div class="kpi-label">Sharpe</div>
        <div class="kpi-value">${fmt.num2(run.sharpe)}</div>
        <div class="kpi-sub">목표 1.0 이상</div>
      </div>
      <div class="kpi-card info">
        <div class="kpi-label">거래 수</div>
        <div class="kpi-value">${run.trade_count ?? '—'}</div>
        <div class="kpi-sub">전략 기간 내</div>
      </div>
    </div>
    ${criteriaLine ? `<div style="font-size:12px;margin-bottom:8px">${criteriaLine}</div>` : ''}
    ${statsBits.length ? `<div class="text-muted" style="font-size:12px;margin-bottom:8px">${statsBits.join(' · ')}</div>` : ''}
    ${yearlyTable}
    <div class="text-muted" style="font-size:11px;margin-bottom:24px">
      1차 판정은 백테스트 집계 기준 — 정식 검증(WFA·MC·Plateau)은 매일 검증 잡이 수행합니다.
    </div>`;
}

function _renderBtRunsTable(runs) {
  if (runs.length === 0) { empty('bt-runs-area', '실행 이력 없음'); return; }

  const statusCls = { done: 'pass', error: 'fail', running: 'warn', queued: 'info' };
  const rows = runs.map(r => {
    const period = (r.start_date && r.end_date) ? `${r.start_date}~${r.end_date}` : '전체';
    const verdictTip = (r.verdict_criteria || [])
      .map(c => `${c.passed ? '✓' : '✗'} ${c.criterion}: ${c.value} (기준 ${c.threshold})`)
      .join('\n');
    const verdictCell = r.verdict
      ? `<span title="${esc(verdictTip)}">${badge(r.verdict, r.verdict === 'PASS' ? 'pass' : 'fail')}</span>`
      : '—';
    return `
    <tr>
      <td class="mono text-muted" style="font-size:11px">${r.run_id}</td>
      <td class="mono">${r.strategy_id}</td>
      <td>${r.source === 'scheduled_validation' ? badge('자동 검증', 'info') : badge('수동', 'research')}</td>
      <td>${badge(r.status.toUpperCase(), statusCls[r.status] ?? 'info')}</td>
      <td class="mono" style="font-size:11px">${period}</td>
      <td class="text-muted" style="font-size:11px">${r.mode === 'portfolio' ? '포트폴리오' : r.mode ? '종목별' : '—'}${r.universe ? ` · ${esc(r.universe)}` : ''}</td>
      <td>${verdictCell}</td>
      <td class="mono">${r.net_cagr != null ? fmt.pct(r.net_cagr) : '—'}</td>
      <td class="mono">${r.mdd != null ? fmt.pct1(Math.abs(r.mdd)) : '—'}</td>
      <td class="mono">${fmt.num2(r.sharpe)}</td>
      <td class="mono">${r.trade_count ?? '—'}</td>
      <td class="mono text-muted">${fmt.date(r.started_at)}</td>
    </tr>`;
  }).join('');

  document.getElementById('bt-runs-area').innerHTML = `
    <div class="text-muted mb-16" style="font-size:12px">총 ${runs.length}건 (최근 50건)</div>
    <table>
      <thead><tr>
        <th>Run ID</th><th>전략</th><th>구분</th><th>상태</th><th>기간</th><th>방식·대상</th><th>판정</th>
        <th>Net CAGR</th><th>MDD</th><th>Sharpe</th><th>거래수</th><th>시작일</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── 페이지별 초기화 디스패처 ──────────────────────────────────────────────────
// Ops Config
async function loadOpsConfig() {
  loading('ops-summary');
  loading('ops-config-area');
  try {
    const d = await apiFetch('/ops/config');
    const stateClass = d.ready ? 'pass' : 'warn';
    document.getElementById('ops-summary').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${stateClass}"><div class="kpi-label">Readiness</div><div class="kpi-value">${d.ready ? 'READY' : 'CHECK'}</div><div class="kpi-sub">${d.missing_required.length} missing required</div></div>
        <div class="kpi-card"><div class="kpi-label">Broker</div><div class="kpi-value">${d.broker_mode}</div><div class="kpi-sub">${d.live_trading_enabled ? 'live switch on' : 'live switch off'}</div></div>
        <div class="kpi-card"><div class="kpi-label">Data</div><div class="kpi-value">${d.data_provider}</div><div class="kpi-sub">market data provider</div></div>
      </div>`;
    document.getElementById('ai-scoring-mode').value = d.ai_scoring_mode;

    const warnings = d.warnings.length
      ? `<div class="alert-item"><div class="alert-dot WARN"></div><div class="alert-msg">${d.warnings.join('<br>')}</div></div>`
      : '';
    const sections = d.sections.map(section => {
      const rows = section.fields.map(f => `
        <tr>
          <td class="mono">${f.env_var}</td>
          <td>${f.description}</td>
          <td>${f.required ? badge('required', 'warn') : badge('optional', 'info')}</td>
          <td>${f.configured ? badge('set', 'pass') : badge('empty', f.required ? 'fail' : 'info')}</td>
          <td class="mono">${f.value || '-'}</td>
        </tr>`).join('');
      return `
        <div class="section-header"><span class="section-title">${section.title} - ${section.status}</span><hr></div>
        <table><thead><tr><th>ENV</th><th>Description</th><th>Required</th><th>Status</th><th>Current</th></tr></thead><tbody>${rows}</tbody></table>`;
    }).join('');
    document.getElementById('ops-config-area').innerHTML = warnings + sections;
  } catch (e) {
    empty('ops-summary', `Error: ${e.message}`);
    empty('ops-config-area', '');
  }
}

async function saveAIScoringMode() {
  const select = document.getElementById('ai-scoring-mode');
  const button = document.getElementById('ai-scoring-save');
  const result = document.getElementById('ai-scoring-result');
  if (select.value === 'replace' && !confirm('기존 점수를 AI 점수로 대체할까요?')) return;
  button.disabled = true;
  result.textContent = '저장 중...';
  try {
    const data = await apiPost('/ops/config/ai-scoring-mode', { mode: select.value });
    result.textContent = `${data.previous_mode} → ${data.mode}`;
    await loadOpsConfig();
  } catch (e) {
    result.textContent = `오류: ${e.message}`;
  } finally {
    button.disabled = false;
  }
}

async function loadTrendStrength() {
  loading('ts-kpi');
  loading('ts-buckets');
  loading('ts-chart');
  try {
    const params = new URLSearchParams(location.search);
    const minBars = params.get('min_bars') || '60';
    const d = await apiFetch(`/trend-strength?min_bars=${encodeURIComponent(minBars)}`);
    const scored = Math.max(0, d.universe_count - d.missing_count);
    const strongest = d.buckets.reduce((best, b) => b.count > best.count ? b : best, d.buckets[0] || { grade: '-', count: 0, ratio: 0 });

    document.getElementById('ts-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">As Of</div><div class="kpi-value">${fmt.date(d.ref_date)}</div><div class="kpi-sub">${minBars} bars minimum</div></div>
        <div class="kpi-card info"><div class="kpi-label">Universe</div><div class="kpi-value">${d.universe_count}</div><div class="kpi-sub">${scored} scored</div></div>
        <div class="kpi-card ${d.missing_count > 0 ? 'warn' : 'pass'}"><div class="kpi-label">Missing</div><div class="kpi-value">${d.missing_count}</div><div class="kpi-sub">${fmt.pct1(d.universe_count ? d.missing_count / d.universe_count : 0)}</div></div>
        <div class="kpi-card"><div class="kpi-label">Dominant</div><div class="kpi-value">${strongest.grade}</div><div class="kpi-sub">${fmt.pct1(strongest.ratio)}</div></div>
      </div>`;

    if (!d.buckets.length) {
      empty('ts-buckets', 'TrendStrength data unavailable');
      empty('ts-chart', '');
      return;
    }

    const rows = d.buckets.map(b => `
      <tr>
        <td class="mono">${b.grade}</td>
        <td>${b.label}</td>
        <td class="mono">${b.count}</td>
        <td class="mono">${fmt.pct1(b.ratio)}</td>
      </tr>`).join('');
    document.getElementById('ts-buckets').innerHTML =
      `<table><thead><tr><th>Bucket</th><th>Label</th><th>Count</th><th>Ratio</th></tr></thead><tbody>${rows}</tbody></table>`;

    const tsChartEl = document.getElementById('ts-chart');
    tsChartEl.innerHTML = '';
    Plotly.newPlot(tsChartEl, [{
      type: 'bar',
      x: d.buckets.map(b => b.grade),
      y: d.buckets.map(b => b.count),
      marker: { color: ['#ef4444', '#f59e0b', '#60a5fa', '#22c55e', '#14b8a6'] },
      text: d.buckets.map(b => fmt.pct1(b.ratio)),
      textposition: 'auto',
    }], {
      ...LAYOUT_BASE,
      yaxis: { ...LAYOUT_BASE.yaxis, title: 'Count' },
    }, CONFIG);
  } catch (e) {
    empty('ts-kpi', `Error: ${e.message}`);
    empty('ts-buckets', '');
    empty('ts-chart', '');
  }
}

async function loadResearch() {
  loading('research-kpi');
  loading('research-area');
  try {
    const d = await apiFetch('/research');
    document.getElementById('research-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Research Total</div><div class="kpi-value">${d.total}</div><div class="kpi-sub">registered strategies</div></div>
        <div class="kpi-card info"><div class="kpi-label">Research</div><div class="kpi-value">${d.mock_count}</div><div class="kpi-sub">pre-gate</div></div>
        <div class="kpi-card warn"><div class="kpi-label">Alert Only</div><div class="kpi-value">${d.alert_only_count}</div><div class="kpi-sub">signal only</div></div>
      </div>`;

    if (!d.strategies || d.strategies.length === 0) {
      empty('research-area', 'Research/Alert Only strategies unavailable');
      return;
    }

    const rows = d.strategies.map(s => `
      <tr>
        <td class="mono">${s.strategy_id}</td>
        <td>${s.strategy_type}</td>
        <td>${stageBadge(s.stage)}</td>
        <td class="mono">${s.signal_count != null ? s.signal_count : '—'}</td>
        <td class="mono ${s.mock_cagr > 0 ? 'text-pass' : s.mock_cagr < 0 ? 'text-fail' : ''}">${s.mock_cagr == null ? '—' : fmt.num2(s.mock_cagr)}</td>
        <td class="mono">${s.mock_mdd == null ? '—' : fmt.pct1(Math.abs(s.mock_mdd))}</td>
        <td class="mono">${s.observation_months == null || s.observation_months === 0 ? '—' : fmt.num1(s.observation_months) + 'mo'}</td>
        <td>${s.next_gate}</td>
      </tr>`).join('');

    document.getElementById('research-area').innerHTML = `
      <table>
        <thead><tr>
          <th>Strategy</th><th>Type</th><th>Stage</th><th>후보 수</th>
          <th>Mock Sharpe</th><th>Mock MDD</th><th>관측 기간</th><th>Next Gate</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    empty('research-kpi', `Error: ${e.message}`);
    empty('research-area', '');
  }
}

async function loadCostSensitivity() {
  loading('cost-kpi');
  loading('cost-assumption');
  loading('cost-scenarios');
  try {
    const params = new URLSearchParams(location.search);
    const strategyId = params.get('strategy_id') || 'pullback_v3';
    const d = await apiFetch(`/cost-sensitivity?strategy_id=${encodeURIComponent(strategyId)}`);
    const base = d.assumption;

    document.getElementById('cost-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Strategy</div><div class="kpi-value">${d.strategy_id}</div><div class="kpi-sub">selected model</div></div>
        <div class="kpi-card info"><div class="kpi-label">Tax</div><div class="kpi-value">${fmt.pct3(base?.tax_rate)}</div><div class="kpi-sub">sell-side</div></div>
        <div class="kpi-card"><div class="kpi-label">Fee</div><div class="kpi-value">${fmt.pct3(base?.commission_rate)}</div><div class="kpi-sub">round trip</div></div>
        <div class="kpi-card"><div class="kpi-label">Large Slip</div><div class="kpi-value">${fmt.pct3(base?.slippage_large)}</div><div class="kpi-sub">base assumption</div></div>
      </div>`;

    if (!base) {
      empty('cost-assumption', 'Cost assumptions unavailable');
    } else {
      document.getElementById('cost-assumption').innerHTML = `
        <table>
          <thead><tr><th>Field</th><th>Value</th></tr></thead>
          <tbody>
            <tr><td>Tax Rate</td><td class="mono">${fmt.pct3(base.tax_rate)}</td></tr>
            <tr><td>Commission</td><td class="mono">${fmt.pct3(base.commission_rate)}</td></tr>
            <tr><td>Slippage Large</td><td class="mono">${fmt.pct3(base.slippage_large)}</td></tr>
            <tr><td>Slippage Mid/Small</td><td class="mono">${fmt.pct3(base.slippage_mid_small)}</td></tr>
            <tr><td>Effective At</td><td class="mono">${fmt.date(base.effective_at)}</td></tr>
          </tbody>
        </table>`;
    }

    if (!d.scenarios || d.scenarios.length === 0) {
      empty('cost-scenarios', 'Scenario data unavailable');
      return;
    }
    const rows = d.scenarios.map(s => `
      <tr>
        <td>${s.label}</td>
        <td class="mono">${fmt.pct(s.slip_delta_pct)}</td>
        <td class="mono text-muted">—</td>
        <td class="mono ${s.net_sharpe != null && s.net_sharpe > 0 ? 'text-pass' : s.net_sharpe != null && s.net_sharpe < 0 ? 'text-fail' : ''}">${s.net_sharpe == null ? '—' : fmt.num2(s.net_sharpe)}</td>
        <td class="mono">${s.tradeability == null ? '—' : fmt.score(s.tradeability)}</td>
        <td>${badge(s.status, s.status === 'baseline' ? 'pass' : 'info')}</td>
      </tr>`).join('');
    document.getElementById('cost-scenarios').innerHTML = `
      <table>
        <thead><tr><th>Scenario</th><th>Slip Delta</th><th>Net CAGR</th><th>Sharpe (WFA)</th><th>Tradeability</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    empty('cost-kpi', `Error: ${e.message}`);
    empty('cost-assumption', '');
    empty('cost-scenarios', '');
  }
}

function _renderOrderPreview(p) {
  if (!p || !p.data_available) {
    empty('orders-preview-kpi', '');
    empty('orders-preview-table', '후보 스냅샷 없음 — 데이터 수집 후 다시 확인하세요');
    return;
  }
  if (p.data_stale) {
    const regimeBlocked = p.stale_reason === 'regime_blocked';
    const statusCard = regimeBlocked ? `
        <div class="kpi-card info">
          <div class="kpi-label">후보 상태</div>
          <div class="kpi-value">장세 차단</div>
          <div class="kpi-sub">데이터 수집 정상 (OHLCV ${p.latest_ohlcv_date || '—'}) — 장세(${(p.market_regime || 'unknown').toUpperCase()})로 전 전략 진입 차단 중</div>
        </div>` : `
        <div class="kpi-card warn">
          <div class="kpi-label">후보 상태</div>
          <div class="kpi-value">데이터 오래됨</div>
          <div class="kpi-sub">기대 기준일 ${p.expected_ref_date} 이후 생성 없음</div>
        </div>`;
    document.getElementById('orders-preview-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card info">
          <div class="kpi-label">다음 거래일</div>
          <div class="kpi-value">${p.next_trading_day}</div>
          <div class="kpi-sub">마지막 생성 기준일 ${p.as_of_date}</div>
        </div>${statusCard}
      </div>`;
    empty('orders-preview-table', regimeBlocked
      ? `신규 후보 없음 — 약세장으로 후보 생성이 차단됨 (수집은 정상, 마지막 후보 기준일 ${p.as_of_date})`
      : `최신 후보 없음 — 데이터가 오래됨 (마지막 기준일 ${p.as_of_date}, 기대 ${p.expected_ref_date} 이후 생성 없음)`);
    return;
  }
  const validItems = (p.items || []).filter(i => !i.skipped);
  const totalAmt = validItems.reduce((s, i) => s + i.estimated_amount, 0);
  const eligible = (p.eligible_strategies || []).join(', ') || '없음';

  const regimeCls = (p.entry_limit_ratio >= 1.0) ? 'pass'
                  : (p.entry_limit_ratio <= 0) ? 'fail' : 'warn';
  const regimeBanner = `
    <div class="kpi-grid" style="margin-bottom:12px">
      <div class="kpi-card ${regimeCls}">
        <div class="kpi-label">장세 (Regime)</div>
        <div class="kpi-value">${(p.market_regime || 'unknown').toUpperCase()}</div>
        <div class="kpi-sub">주봉 트렌드 ${p.weekly_trend || '—'}</div>
      </div>
      <div class="kpi-card ${regimeCls}">
        <div class="kpi-label">신규진입 허용</div>
        <div class="kpi-value">${p.max_orders_effective ?? p.max_orders}건</div>
        <div class="kpi-sub">limit_ratio ${Math.round((p.entry_limit_ratio||0)*100)}%</div>
      </div>
    </div>`;

  document.getElementById('orders-preview-kpi').innerHTML = regimeBanner + `
    <div class="kpi-grid">
      <div class="kpi-card info">
        <div class="kpi-label">다음 거래일</div>
        <div class="kpi-value">${p.next_trading_day}</div>
        <div class="kpi-sub">기준일 ${p.as_of_date}</div>
      </div>
      <div class="kpi-card ${validItems.length > 0 ? 'pass' : 'warn'}">
        <div class="kpi-label">예정 주문 수</div>
        <div class="kpi-value">${validItems.length} / ${p.max_orders_effective ?? p.max_orders}</div>
        <div class="kpi-sub">슬리피지 ${fmt.pct1(p.slippage_pct)} · GAP한도 ${fmt.pct1(p.max_gap_pct)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">예상 투입 금액</div>
        <div class="kpi-value">${fmt.krw(totalAmt)}</div>
        <div class="kpi-sub">가용 현금 ${fmt.krw(p.assumed_cash)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">주문 가능 전략</div>
        <div class="kpi-value" style="font-size:0.9rem">${eligible}</div>
        <div class="kpi-sub">mock/live candidate 이상</div>
      </div>
    </div>`;

  if (!p.items || p.items.length === 0) {
    empty('orders-preview-table', '예정 주문 없음');
    return;
  }
  const rows = p.items.map((item, idx) => {
    const gapCls = item.gap_exceeded ? 'text-fail' : item.gap_pct > 0.01 ? 'text-warn' : '';
    const isRegimeBlock = (item.skip_reason || '').startsWith('preferred_regime_mismatch')
      || (item.skip_reason || '').includes('weak_high_vol')
      || item.skip_reason === 'weekly_trend_fail';
    const skipLabel = {
      gap_exceeded: 'GAP초과',
      insufficient_cash: '수량부족',
      no_entry_signal: '진입신호없음',
      weekly_trend_fail: '주간추세 미달',
    }[item.skip_reason]
      || (isRegimeBlock ? '장세 차단' : null)
      || item.skip_reason || '스킵';
    const statusBadge = item.skipped
      ? badge(skipLabel, isRegimeBlock ? 'warn' : 'fail')
      : badge('예정', 'pass');
    return `
      <tr class="${item.skipped ? 'text-muted' : ''}">
        <td class="mono">${idx + 1}</td>
        <td class="mono">${item.strategy_id}</td>
        <td class="mono">${item.ticker}</td>
        <td>${item.name || '-'}</td>
        <td class="mono text-muted">${fmt.date(item.signal_date)}</td>
        <td class="mono">${item.signal_close.toLocaleString('ko-KR')}</td>
        <td class="mono">${item.current_close.toLocaleString('ko-KR')}</td>
        <td class="mono ${gapCls}">${fmt.pct(item.gap_pct)}</td>
        <td class="mono">${item.limit_price.toLocaleString('ko-KR')}</td>
        <td class="mono">${item.estimated_qty}</td>
        <td class="mono">${fmt.krw(item.estimated_amount)}</td>
        <td>${statusBadge}</td>
      </tr>`;
  }).join('');
  document.getElementById('orders-preview-table').innerHTML =
    `<table><thead><tr>
      <th>#</th><th>전략</th><th>티커</th><th>종목명</th>
      <th>신호일</th><th>신호종가</th><th>현재종가</th><th>GAP</th>
      <th>예상주문가</th><th>예상수량</th><th>예상금액</th><th>상태</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadOrders() {
  loading('orders-kpi');
  loading('orders-pending');
  loading('orders-fills');
  loading('orders-expired');
  loading('orders-preview-kpi');
  loading('orders-preview-table');
  try {
    const [d, preview] = await Promise.all([
      apiFetch('/orders'),
      apiFetch('/orders/preview'),
    ]);
    _renderOrderPreview(preview);
    const slip = d.slippage || {};
    document.getElementById('orders-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${d.auto_order_active ? 'pass' : 'fail'}"><div class="kpi-label">Auto Orders</div><div class="kpi-value">${d.auto_order_active ? 'ACTIVE' : 'BLOCKED'}</div><div class="kpi-sub">Kill Switch aware</div></div>
        <div class="kpi-card"><div class="kpi-label">Pending</div><div class="kpi-value">${d.pending.length}</div><div class="kpi-sub">open queue</div></div>
        <div class="kpi-card info"><div class="kpi-label">Fills Today</div><div class="kpi-value">${d.fills_today.length}</div><div class="kpi-sub">audit log</div></div>
        <div class="kpi-card"><div class="kpi-label">Slip Assumed</div><div class="kpi-value">${fmt.pct1(slip.mid_small_assumed)}</div><div class="kpi-sub">mid/small cap</div></div>
      </div>`;

    if (!d.pending.length) {
      empty('orders-pending', 'No pending orders');
    } else {
      const rows = d.pending.map(o => {
        const statusCls = ['expired','EXPIRED'].includes(o.status) ? 'fail'
                        : ['filled','FILLED'].includes(o.status) ? 'pass' : 'info';
        return `
        <tr>
          <td class="mono">${o.order_id}</td>
          <td class="mono">${o.strategy_id ?? '-'}</td>
          <td class="mono">${o.ticker}</td>
          <td>${o.name || '-'}</td>
          <td>${badge(o.side, o.side === 'BUY' ? 'pass' : 'warn')}</td>
          <td class="mono">${o.qty}</td>
          <td class="mono">${o.order_price == null ? '-' : o.order_price.toLocaleString('ko-KR')}</td>
          <td>${badge(o.status, statusCls)}</td>
          <td class="mono text-muted">${fmt.date(o.created_at)}</td>
        </tr>`;
      }).join('');
      document.getElementById('orders-pending').innerHTML =
        `<table><thead><tr><th>Order</th><th>Strategy</th><th>Ticker</th><th>종목명</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    if (!d.fills_today.length) {
      empty('orders-fills', 'No fills today');
    } else {
      const rows = d.fills_today.map(f => {
        const statusCls = ['filled','FILLED'].includes(f.status) ? 'pass'
                        : ['partially_filled','PARTIAL'].includes(f.status) ? 'warn' : 'info';
        return `
        <tr>
          <td class="mono">${f.order_id}</td>
          <td class="mono">${f.ticker}</td>
          <td>${f.name || '-'}</td>
          <td>${badge(f.side, f.side === 'BUY' ? 'pass' : 'warn')}</td>
          <td class="mono">${f.fill_qty}</td>
          <td class="mono">${f.fill_price == null ? '-' : f.fill_price.toLocaleString('ko-KR')}</td>
          <td>${badge(f.status, statusCls)}</td>
          <td class="mono text-muted">${fmt.date(f.created_at)}</td>
        </tr>`;
      }).join('');
      document.getElementById('orders-fills').innerHTML =
        `<table><thead><tr><th>Order</th><th>Ticker</th><th>종목명</th><th>Side</th><th>Fill Qty</th><th>Fill Price</th><th>Status</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    if (!d.expired || !d.expired.length) {
      empty('orders-expired', 'No expired orders');
    } else {
      const rows = d.expired.map(o => `
        <tr>
          <td class="mono">${o.order_id}</td>
          <td class="mono">${o.strategy_id ?? '-'}</td>
          <td class="mono">${o.ticker}</td>
          <td>${o.name || '-'}</td>
          <td>${badge(o.side, o.side === 'BUY' ? 'pass' : 'warn')}</td>
          <td class="mono">${o.qty}</td>
          <td class="mono">${o.order_price == null ? '-' : o.order_price.toLocaleString('ko-KR')}</td>
          <td>${badge(o.status, 'fail')}</td>
          <td class="mono text-muted">${fmt.date(o.created_at)}</td>
        </tr>`).join('');
      document.getElementById('orders-expired').innerHTML =
        `<table><thead><tr><th>Order</th><th>Strategy</th><th>Ticker</th><th>종목명</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
  } catch (e) {
    empty('orders-kpi', `Error: ${e.message}`);
    empty('orders-pending', '');
    empty('orders-fills', '');
    empty('orders-expired', '');
    empty('orders-preview-kpi', '');
    empty('orders-preview-table', `Error: ${e.message}`);
  }
}

async function loadDataQualityV2() {
  loading('dq-kpi');
  loading('dq-reasons');
  loading('dq-chart');
  try {
    const params = new URLSearchParams(location.search);
    const mode = params.get('mode') || 'live';
    const d = await apiFetch(`/data-quality?mode=${encodeURIComponent(mode)}`);
    const ratioClass = d.rejection_ratio > 0.40 ? 'fail' : d.rejection_ratio > 0.30 ? 'warn' : 'pass';
    document.getElementById('dq-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Universe</div><div class="kpi-value">${d.total_candidates}</div><div class="kpi-sub">${d.mode} · ${d.ref_date}</div></div>
        <div class="kpi-card pass"><div class="kpi-label">Kept</div><div class="kpi-value">${d.kept_count}</div><div class="kpi-sub">${fmt.pct1(d.total_candidates ? d.kept_count / d.total_candidates : 0)}</div></div>
        <div class="kpi-card ${ratioClass}"><div class="kpi-label">Rejected</div><div class="kpi-value">${d.rejected_count}</div><div class="kpi-sub">${fmt.pct1(d.rejection_ratio)}</div></div>
        <div class="kpi-card ${d.alert_sent ? 'warn' : 'pass'}"><div class="kpi-label">Alert</div><div class="kpi-value">${d.alert_sent ? 'SENT' : 'NORMAL'}</div><div class="kpi-sub">threshold 40%</div></div>
      </div>`;

    if (!d.rejection_reasons || d.rejection_reasons.length === 0) {
      empty('dq-reasons', 'No rejection reasons');
    } else {
      const rows = d.rejection_reasons.map(r => `
        <tr>
          <td class="mono">${r.reason_code}</td>
          <td>${r.description}</td>
          <td class="mono">${r.count}</td>
          <td class="mono">${fmt.pct1(r.ratio)}</td>
        </tr>`).join('');
      document.getElementById('dq-reasons').innerHTML =
        `<table><thead><tr><th>Reason</th><th>Description</th><th>Count</th><th>Ratio</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    if (d.history_90d && d.history_90d.length > 0) {
      const dates = d.history_90d.map(h => h.date);
      const ratios = d.history_90d.map(h => h.rejection_ratio * 100);
      const chartEl = document.getElementById('dq-chart');
      chartEl.innerHTML = '';  // Plotly.newPlot(responsive:true)는 기존 내용을 제거하지 않으므로 명시 초기화
      Plotly.newPlot(chartEl, [
        { x: dates, y: ratios, type: 'scatter', mode: 'lines+markers', name: 'Reject ratio',
          line: { color: '#60a5fa', width: 2 }, fill: 'tozeroy', fillcolor: 'rgba(96,165,250,.08)' },
        { x: [dates[0], dates[dates.length - 1]], y: [40, 40], type: 'scatter', mode: 'lines',
          name: 'Threshold 40%', line: { color: '#fbbf24', width: 1, dash: 'dot' } }
      ], { ...LAYOUT_BASE, height: 220, yaxis: { ...LAYOUT_BASE.yaxis, ticksuffix: '%' } }, CONFIG);
    } else {
      empty('dq-chart', 'No quality history');
    }
  } catch (e) {
    empty('dq-kpi', `Error: ${e.message}`);
    empty('dq-reasons', '');
    empty('dq-chart', '');
  }
}

const PAGE_LOADERS = {
  dashboard:        loadDashboard,
  strategies:       loadStrategies,
  market:           loadMarket,
  candidates:       loadCandidates,
  orders:           loadOrders,
  risk:             loadRisk,
  backtest:         loadBacktest,
  robustness:       loadRobustness,
  'live-monitor':   loadLiveMonitorV2,
  'data-quality':   loadDataQualityV2,
  'trend-strength': loadTrendStrength,
  research:         loadResearch,
  'cost-sensitivity': loadCostSensitivity,
  wfa:              loadWfa,
  'ops-config':     loadOpsConfig,
  'stock-report':   () => {},  // stock_report.html 인라인 스크립트로 처리
  'trade-review':   loadTradeReview,
  'batch-monitor':  loadBatchMonitor,
};

document.addEventListener('DOMContentLoaded', () => {
  const screen = document.body.dataset.screen;
  const loader = PAGE_LOADERS[screen];
  if (loader) loader();
});

// ── SCR-17 거래 리뷰 ─────────────────────────────────────────────────────────
async function loadTradeReview() {
  loading('trade-review-kpi');
  loading('trade-review-strategy');
  loading('trade-review-table');
  try {
    const d = await apiFetch('/trade-review');
    const s = d.summary;

    // ── KPI 카드 ────────────────────────────────────────────────────────────
    const returnCls = s.total_return_pct >= 0 ? 'pass' : 'fail';
    const pnlCls    = s.total_pnl >= 0 ? 'pass' : 'fail';
    document.getElementById('trade-review-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card ${returnCls}">
          <div class="kpi-label">총 수익률</div>
          <div class="kpi-value">${fmt.pct(s.total_return_pct / 100)}</div>
          <div class="kpi-sub">${fmt.krw(s.initial_assets)} → ${fmt.krw(s.current_assets)}</div>
        </div>
        <div class="kpi-card ${pnlCls}">
          <div class="kpi-label">추정 손익</div>
          <div class="kpi-value">${fmt.krw(s.total_pnl)}</div>
          <div class="kpi-sub">실현 ${fmt.krw(s.realized_pnl)} / 미실현 ${fmt.krw(s.unrealized_pnl)}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">거래 건수</div>
          <div class="kpi-value">${s.total_trades}</div>
          <div class="kpi-sub">청산 ${s.closed_trades} · 보유 ${s.open_trades}</div>
        </div>
        <div class="kpi-card ${s.win_rate != null && s.win_rate >= 50 ? 'pass' : 'warn'}">
          <div class="kpi-label">승률 (추정)</div>
          <div class="kpi-value">${s.win_rate != null ? s.win_rate.toFixed(1) + '%' : '—'}</div>
          <div class="kpi-sub">승 ${s.winning_trades} / 패 ${s.losing_trades}</div>
        </div>
      </div>`;

    // ── 전략별 성과 ──────────────────────────────────────────────────────────
    if (!d.by_strategy || d.by_strategy.length === 0) {
      empty('trade-review-strategy', '전략별 데이터 없음');
    } else {
      const rows = d.by_strategy.map(s => {
        const retCls = s.return_pct != null ? (s.return_pct >= 0 ? 'text-pass' : 'text-fail') : '';
        return `<tr>
          <td class="mono">${s.strategy_id}</td>
          <td class="mono">${s.total_trades}</td>
          <td class="mono">${s.wins} / ${s.losses}</td>
          <td class="mono">${s.win_rate != null ? s.win_rate.toFixed(1) + '%' : '—'}</td>
          <td class="mono ${retCls}">${s.return_pct != null ? fmt.pct(s.return_pct / 100) : '—'}</td>
          <td class="mono ${retCls}">${fmt.krw(s.total_pnl)}</td>
          <td class="mono text-muted">${fmt.krw(s.total_cost)}</td>
        </tr>`;
      }).join('');
      document.getElementById('trade-review-strategy').innerHTML = `
        <table>
          <thead><tr>
            <th>전략</th><th>건수</th><th>승/패</th><th>승률</th>
            <th>수익률</th><th>손익</th><th>투자원금</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    // ── 거래 상세 ────────────────────────────────────────────────────────────
    const statusLabel = {
      open:           s => badge('보유중', 'info'),
      closed:         s => badge('청산', 'pass'),
      estimated_exit: s => badge('추정청산', 'warn'),
    };
    if (!d.trades || d.trades.length === 0) {
      empty('trade-review-table', '거래 이력 없음');
    } else {
      const rows = d.trades.map(t => {
        // 보유중(미매도) 행은 청산 관련 칸(매도가/손익/수익률)을 비운다 — 미실현 손익은 상단 KPI에서만 표시.
        const isOpen = t.status === 'open';
        const pnlCls = (isOpen || t.pnl == null) ? '' : t.pnl >= 0 ? 'text-pass' : 'text-fail';
        const statusBadge = (statusLabel[t.status] || (() => badge(t.status, 'warn')))(t);
        return `<tr>
          <td>${statusBadge}</td>
          <td class="mono">${t.ticker}</td>
          <td>${t.name}</td>
          <td class="mono text-muted">${t.strategy_id}</td>
          <td class="mono">${fmt.date(t.entry_date)}</td>
          <td class="mono">${t.entry_price.toLocaleString('ko-KR')}</td>
          <td class="mono">${t.qty}</td>
          <td class="mono">${fmt.krw(t.entry_cost)}</td>
          <td class="mono">${t.exit_date ? fmt.date(t.exit_date) : '—'}</td>
          <td class="mono">${(!isOpen && t.exit_price) ? t.exit_price.toLocaleString('ko-KR') : '—'}</td>
          <td class="mono ${pnlCls}">${(!isOpen && t.pnl != null) ? fmt.krw(t.pnl) : '—'}</td>
          <td class="mono ${pnlCls}">${(!isOpen && t.pnl_pct != null) ? fmt.pct(t.pnl_pct / 100) : '—'}</td>
          <td class="mono text-muted">${t.hold_days != null ? t.hold_days + '일' : '—'}</td>
          <td class="text-muted" style="font-size:0.75rem">${t.note || ''}</td>
        </tr>`;
      }).join('');
      document.getElementById('trade-review-table').innerHTML = `
        <table>
          <thead><tr>
            <th>상태</th><th>종목코드</th><th>종목명</th><th>전략</th>
            <th>매수일</th><th>매수가</th><th>수량</th><th>투자금</th>
            <th>매도일</th><th>매도가</th><th>손익</th><th>수익률</th>
            <th>보유일</th><th>비고</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
  } catch (e) {
    empty('trade-review-kpi', `오류: ${e.message}`);
    empty('trade-review-strategy', '');
    empty('trade-review-table', '');
  }
}

// ── SCR-21 배치 모니터 ───────────────────────────────────────────────────────
let _bmDays = 14;

function changeBatchMonitorDays(v) {
  _bmDays = parseInt(v, 10) || 14;
  loadBatchMonitor();
}

const _BM_STATUS = {
  success: ['OK', 'pass'],
  failed:  ['FAIL', 'fail'],
  missed:  ['MISS', 'fail'],
  running: ['실행중', 'info'],
  pending: ['대기', 'info'],
};

function _bmCellHtml(job, cell, isToday) {
  if (cell.status === 'skipped') return '<span class="text-muted">—</span>';
  const [label, cls] = _BM_STATUS[cell.status] || [cell.status, 'info'];
  const tip = [
    cell.message,
    cell.detail,
    cell.duration_sec != null ? `${cell.duration_sec}초 소요` : null,
  ].filter(Boolean).join(' · ');
  let html = `<span title="${esc(tip)}">${badge(label, cls)}</span>`;
  if (isToday && job.rerunnable && (cell.status === 'failed' || cell.status === 'missed')) {
    html += ` <button class="topbar-btn" style="padding:0 6px" title="지금 재실행"
      onclick="rerunBatchJob('${job.name}', this)">↻</button>`;
  }
  return html;
}

async function rerunBatchJob(name, btn) {
  btn.disabled = true;
  btn.textContent = '…';
  try {
    await apiPost('/ops/scheduler/run/' + name, {});
    await loadBatchMonitor();
  } catch (e) {
    alert(`재실행 실패 [${name}]: ${e.message}`);
    btn.disabled = false;
    btn.textContent = '↻';
  }
}

async function loadBatchMonitor() {
  loading('bm-matrix');
  try {
    const d = await apiFetch(`/batch-monitor?days=${_bmDays}`);
    const today = d.days[0];
    const head = d.days.map(day =>
      `<th class="mono" style="font-size:0.72rem">${day.slice(5)}</th>`).join('');
    const rows = d.jobs.map(job => {
      const cells = job.cells.map(c =>
        `<td style="text-align:center">${_bmCellHtml(job, c, c.date === today)}</td>`).join('');
      return `<tr>
        <td><b>${esc(job.label)}</b><br>
          <span class="mono text-muted" style="font-size:0.72rem">${esc(job.schedule)}</span></td>
        ${cells}
      </tr>`;
    }).join('');
    document.getElementById('bm-matrix').innerHTML = `
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>잡</th>${head}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="text-muted" style="font-size:0.75rem;margin-top:8px">
        OK=성공 · FAIL=실패 · MISS=미실행 · 대기=예정 전 · —=비거래일.
        셀에 마우스를 올리면 상세(오류 메시지·소요시간)가 보입니다.
        ↻는 오늘 실패/미실행한 스케줄러 잡만 재실행합니다.
      </div>`;
  } catch (e) {
    empty('bm-matrix', `오류: ${e.message}`);
  }
}
