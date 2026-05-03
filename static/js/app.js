/* MAPS Frontend App v0.2.0 */
'use strict';

// ── API 헬퍼 ──────────────────────────────────────────────────────────────────
async function apiFetch(path) {
  const res = await fetch('/api/v1' + path);
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

// ── 포맷 유틸 ────────────────────────────────────────────────────────────────
const fmt = {
  pct:    v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%',
  pct1:   v => v == null ? '—' : (v * 100).toFixed(1) + '%',
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

function loading(id) {
  document.getElementById(id).innerHTML =
    '<div class="loading"><div class="spinner"></div>로딩 중...</div>';
}

function empty(id, msg = '데이터 없음') {
  document.getElementById(id).innerHTML =
    `<div class="empty-state"><div class="empty-icon">○</div><div class="empty-text">${msg}</div></div>`;
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
async function loadStrategies() {
  loading('strategies-area');
  try {
    const d = await apiFetch('/strategies');
    if (d.strategies.length === 0) { empty('strategies-area', '등록된 전략 없음'); return; }

    const rows = d.strategies.map(s => `
      <tr>
        <td class="mono">${s.strategy_id}</td>
        <td>${stageBadge(s.stage)}</td>
        <td class="mono">${fmt.score(s.tradeability_score)}</td>
        <td class="mono">${fmt.score(s.plateau_score)}</td>
        <td class="mono">${s.mc_mdd_p95 != null ? fmt.pct1(s.mc_mdd_p95) : '—'}</td>
        <td>${s.wfa_passed != null ? passBadge(s.wfa_passed) : '—'}</td>
        <td>${s.promotion_pending ? badge('대기', 'warn') : '—'}</td>
      </tr>`).join('');

    document.getElementById('strategies-area').innerHTML = `
      <div class="flex-between mb-16">
        <span class="text-muted">전체 ${d.total}개 · 승격 대기 ${d.pending_promotions}건</span>
      </div>
      <table><thead><tr>
        <th>전략 ID</th><th>단계</th><th>Tradeability</th><th>Plateau</th><th>MC MDD p95</th><th>WFA</th><th>승격</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) { empty('strategies-area', `오류: ${e.message}`); }
}

// ── SCR-08 Robustness ─────────────────────────────────────────────────────────
async function loadRobustness() {
  const params = new URLSearchParams(location.search);
  const sid = params.get('strategy') || 'pullback_v3';
  const preset = params.get('preset') || 'balanced';
  loading('robustness-kpi');
  loading('breakdown-area');
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

    const bd = d.breakdown;
    if (bd) {
      document.getElementById('breakdown-area').innerHTML = `
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-label">Robustness (×${bd.weight_preset === 'balanced' ? '0.30' : '?'})</div><div class="kpi-value">${fmt.score(bd.robustness)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Risk (×0.30)</div><div class="kpi-value">${fmt.score(bd.risk)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Recovery (×0.20)</div><div class="kpi-value">${fmt.score(bd.recovery)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Return (×0.20)</div><div class="kpi-value">${fmt.score(bd.ret)}</div></div>
        </div>`;
    } else {
      empty('breakdown-area', '백테스트 실행 후 확인 가능합니다');
    }
  } catch (e) {
    empty('robustness-kpi', `오류: ${e.message}`);
    empty('breakdown-area', '');
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
          <div class="kpi-sub">가정 0.05%</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">중소형 슬립</div>
          <div class="kpi-value">${d.mid_small_slip_actual != null ? fmt.pct1(d.mid_small_slip_actual) : '—'}</div>
          <div class="kpi-sub">가정 0.15%</div>
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
async function loadDataQuality() {
  loading('dq-kpi');
  loading('dq-reasons');
  loading('dq-chart');
  try {
    const d = await apiFetch('/data-quality');

    const ratioClass = d.rejection_ratio > 0.15 ? 'fail' : d.rejection_ratio > 0.05 ? 'warn' : 'pass';
    document.getElementById('dq-kpi').innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">유니버스 후보</div><div class="kpi-value">${d.total_candidates}</div><div class="kpi-sub">KOSPI200 + KOSDAQ150</div></div>
        <div class="kpi-card pass"><div class="kpi-label">통과 (kept)</div><div class="kpi-value">${d.kept_count}</div><div class="kpi-sub">${fmt.pct1(d.kept_count / (d.total_candidates || 1))}</div></div>
        <div class="kpi-card ${ratioClass}"><div class="kpi-label">거부율</div><div class="kpi-value">${fmt.pct1(d.rejection_ratio)}</div><div class="kpi-sub">임계 5%</div></div>
        <div class="kpi-card ${d.alert_sent ? 'warn' : 'pass'}"><div class="kpi-label">알림</div><div class="kpi-value">${d.alert_sent ? '발송됨' : '정상'}</div><div class="kpi-sub">거부율 5% 초과 시</div></div>
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
        { x: [dates[0], dates[dates.length - 1]], y: [5, 5], type: 'scatter', mode: 'lines',
          name: '임계 5%', line: { color: '#fbbf24', width: 1, dash: 'dot' } }
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
async function loadWfa() {
  loading('wfa-kpi');
  loading('wfa-folds');
  try {
    const params = new URLSearchParams(location.search);
    const sid = params.get('strategy') || 'pullback_v3';
    const d = await apiFetch(`/wfa?strategy_id=${sid}`);

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
          <div class="kpi-sub">${d.passed ? '4/4 통과' : d.fail_reasons.length + '건 실패'}</div>
        </div>
      </div>`;

    if (d.folds.length === 0) {
      empty('wfa-folds', 'WFA 실행 후 fold 결과가 표시됩니다');
    }
  } catch (e) { empty('wfa-kpi', `오류: ${e.message}`); }
}

// ── 페이지별 초기화 디스패처 ──────────────────────────────────────────────────
const PAGE_LOADERS = {
  dashboard:        loadDashboard,
  strategies:       loadStrategies,
  robustness:       loadRobustness,
  'live-monitor':   loadLiveMonitor,
  'data-quality':   loadDataQuality,
  wfa:              loadWfa,
};

document.addEventListener('DOMContentLoaded', () => {
  const screen = document.body.dataset.screen;
  const loader = PAGE_LOADERS[screen];
  if (loader) loader();
});
