/* ─────────────────────────────────────────────────────────────────────────
   주식 종목 분석 딥다이브 — 공유 로직
   stock_analysis.html(단독 화면) + analysis_picks.html(워치리스트 모달) 공용.

   진입점:
     runStockAnalysis(ticker)        — 패널(인라인/모달 공용)에서 SSE 분석 실행
     openStockAnalysisModal(ticker)  — 모달을 열고 분석 실행
     closeStockAnalysisModal()       — 모달 닫고 진행 중 SSE 종료 + 패널 초기화
     resetStockAnalysisPanel()       — 패널 상태 초기화

   요구사항: 전역 Plotly(base.html) + marked@9 CDN. 패널 DOM은
   _stock_analysis_panel.html, 스타일은 stock-analysis.css.
   ───────────────────────────────────────────────────────────────────────── */

/* ── 분석 단계 정의 ───────────────────────────────────────── */
const STEP_DEFS = [
  { key: 'resolve',      label: '종목코드 확인',             pctFrom: 0  },
  { key: 'technicals',   label: '시세 · 기술적 지표 수집',   pctFrom: 10 },
  { key: 'fundamentals', label: '밸류에이션 조회',            pctFrom: 40 },
  { key: 'dart_corp',    label: 'DART 법인코드 조회',         pctFrom: 55 },
  { key: 'financials',   label: '재무제표 수집 (3개년)',      pctFrom: 70 },
  { key: 'ai_analysis',  label: 'AI 종합분석 (Claude Opus)', pctFrom: 97 },
];

function stepKeyFromPct(pct) {
  for (let i = STEP_DEFS.length - 1; i >= 0; i--) {
    if (pct >= STEP_DEFS[i].pctFrom) return STEP_DEFS[i].key;
  }
  return STEP_DEFS[0].key;
}

/* ── 모듈 상태 ────────────────────────────────────────────── */
let _elapsedTimer = null;
let _startTime = null;
let _activeEs = null;
let _analysisText = '';
let _aiStarted = false;
let _lastAnalysis = null;
let _lastAnalysisTradePlan = null;
let _tradePlanSource = 'MANUAL_REQUIRED';
let _tradeRationale = '';
let _lastTradePreview = null;
let _lastTradePreviewSignature = '';
let _lastTradeLimits = null;
let _tradeLimitTimer = null;
let _tradePreviewTimer = null;
let _tradeRequestVersion = 0;
const TRADE_DEBOUNCE_MS = 300;

function _byId(id) { return document.getElementById(id); }
function _setBtn(disabled, text) {
  const btn = _byId('sa-btn');
  if (!btn) return;            // 단독 화면 전용 버튼 — 모달에는 없음
  btn.disabled = disabled;
  btn.textContent = text;
}

function _clearTradeTimers() {
  if (_tradeLimitTimer) clearTimeout(_tradeLimitTimer);
  if (_tradePreviewTimer) clearTimeout(_tradePreviewTimer);
  _tradeLimitTimer = null;
  _tradePreviewTimer = null;
}

function _invalidateTradePreview() {
  _lastTradePreview = null;
  _lastTradePreviewSignature = '';
  _tradeRequestVersion += 1;
  const arm = _byId('sa-arm-trade');
  if (arm) arm.disabled = true;
}

/* ── 진행률 UI 업데이트 ──────────────────────────────────── */
function startProgress(ticker) {
  _clearTradeTimers();
  _invalidateTradePreview();
  _startTime = Date.now();
  _analysisText = '';
  _aiStarted = false;
  _lastAnalysis = null;
  _lastAnalysisTradePlan = null;
  _lastTradeLimits = null;
  const tradeButton = _byId('sa-open-trade');
  if (tradeButton) tradeButton.style.display = 'none';
  _byId('sa-progress').style.display = 'block';
  _byId('sa-error').style.display = 'none';
  _byId('sa-result').style.display = 'none';
  _byId('sa-ai-card').style.display = 'none';
  _byId('sa-ai-body').innerHTML = '';
  _byId('prog-title').textContent = `${ticker} 분석 진행 중…`;
  _byId('prog-step').textContent = '';
  _byId('prog-bar').style.width = '0%';
  _byId('prog-pct').textContent = '0%';

  // 단계 체크리스트 초기화
  _byId('prog-steps').innerHTML = STEP_DEFS.map(s =>
    `<div class="sa-step-item" id="step-${s.key}">
       <span class="sa-step-icon">○</span>${s.label}
     </div>`
  ).join('');

  // 경과시간 카운터
  if (_elapsedTimer) clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - _startTime) / 1000);
    _byId('prog-elapsed').textContent = `${sec}초 경과`;
  }, 1000);
}

function updateProgress(step, pct) {
  _byId('prog-step').textContent = step;
  _byId('prog-bar').style.width = pct + '%';
  _byId('prog-pct').textContent = pct + '%';

  const activeKey = stepKeyFromPct(pct);
  STEP_DEFS.forEach(s => {
    const el = _byId(`step-${s.key}`);
    if (!el) return;
    const icon = el.querySelector('.sa-step-icon');
    if (s.pctFrom < STEP_DEFS.find(x => x.key === activeKey).pctFrom) {
      el.className = 'sa-step-item done';
      icon.className = 'sa-step-icon';
      icon.textContent = '✓';
    } else if (s.key === activeKey) {
      el.className = 'sa-step-item active';
      icon.className = 'sa-step-icon spin';
      icon.textContent = '◐';
    } else {
      el.className = 'sa-step-item';
      icon.className = 'sa-step-icon';
      icon.textContent = '○';
    }
  });
}

function stopProgress() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  // 모든 단계 완료 표시
  STEP_DEFS.forEach(s => {
    const el = _byId(`step-${s.key}`);
    if (!el) return;
    el.className = 'sa-step-item done';
    const icon = el.querySelector('.sa-step-icon');
    icon.className = 'sa-step-icon';
    icon.textContent = '✓';
  });
}

/* ── AI 분석 청크 처리 ────────────────────────────────────── */
function _renderMarkdown(text) {
  // marked는 기본적으로 HTML을 살균하지 않는다. web_search 영향 콘텐츠가 섞일 수 있어
  // DOMPurify로 살균한다(로드 실패 시에만 원본 — 그래도 표시는 되도록).
  const html = marked.parse(text);
  return (typeof DOMPurify !== 'undefined') ? DOMPurify.sanitize(html) : html;
}

function appendAnalysisChunk(chunk) {
  if (!_aiStarted) {
    _aiStarted = true;
    updateProgress('AI 종합분석 진행 중… (웹 검색 포함)', 98);
    _byId('sa-ai-card').style.display = 'block';
    _byId('sa-ai-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  _analysisText += chunk;
  const body = _byId('sa-ai-body');
  body.innerHTML = _renderMarkdown(_analysisText) + '<span class="sa-ai-cursor"></span>';
}

function finalizeAnalysis() {
  if (_analysisText) {
    _byId('sa-ai-body').innerHTML = _renderMarkdown(_analysisText);
  }
}

/* ── 패널 초기화 ──────────────────────────────────────────── */
function resetStockAnalysisPanel() {
  _clearTradeTimers();
  _invalidateTradePreview();
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  if (_activeEs) { _activeEs.close(); _activeEs = null; }
  ['sa-progress', 'sa-result', 'sa-ai-card'].forEach(id => {
    const e = _byId(id);
    if (e) e.style.display = 'none';
  });
  const err = _byId('sa-error');
  if (err) { err.style.display = 'none'; err.textContent = ''; }
  const ai = _byId('sa-ai-body');
  if (ai) ai.innerHTML = '';
  _analysisText = '';
  _aiStarted = false;
  _lastAnalysis = null;
  _lastAnalysisTradePlan = null;
  _lastTradeLimits = null;
}

/* ── 저장된 분석 이력 ───────────────────────────────────── */
function _formatHistoryTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR');
}

function _historyCell(row, value, className = '') {
  const cell = row.insertCell();
  cell.textContent = value;
  if (className) cell.className = className;
  return cell;
}

async function loadAnalysisHistory() {
  const body = _byId('sa-history-body');
  const status = _byId('sa-history-status');
  if (!body || !status) return;
  status.textContent = '분석 이력을 불러오는 중입니다.';
  try {
    const result = await _tradeApi('/api/v1/stock-analysis/history?limit=50&offset=0', undefined, 'GET');
    body.innerHTML = '';
    result.items.forEach(item => {
      const row = body.insertRow();
      _historyCell(row, _formatHistoryTime(item.created_at));
      _historyCell(row, `${item.name} (${item.ticker})`);
      _historyCell(row, item.recommendation || '—');
      _historyCell(row, item.analyzed_price ? _won(item.analyzed_price) : '—', 'mono');
      _historyCell(row, item.latest_price ? _won(item.latest_price) : '—', 'mono');
      _historyCell(row, _formatHistoryTime(item.price_refreshed_at));
      const actions = row.insertCell();
      actions.className = 'sa-history-actions';
      const detail = document.createElement('button');
      detail.type = 'button';
      detail.className = 'topbar-btn';
      detail.textContent = '상세';
      detail.addEventListener('click', () => openAnalysisHistory(item.id));
      const reanalyze = document.createElement('button');
      reanalyze.type = 'button';
      reanalyze.className = 'topbar-btn';
      reanalyze.textContent = '재분석';
      reanalyze.addEventListener('click', () => reanalyzeHistory(item.ticker));
      actions.append(detail, reanalyze);
    });
    status.textContent = result.total ? `최근 ${result.items.length}건` : '저장된 분석 이력이 없습니다.';
  } catch (error) {
    status.textContent = `분석 이력을 불러오지 못했습니다: ${error.message}`;
  }
}

// 저장된 이력을 열었을 때만 PDF 를 받을 수 있다 — 방금 돌린 분석은 이력 저장이
// 끝난 뒤에야 내려받을 id 가 생긴다.
function _setPdfLink(historyId) {
  const link = _byId('sa-download-pdf');
  if (!link) return;
  if (!historyId) {
    link.style.display = 'none';
    link.removeAttribute('href');
    return;
  }
  link.href = `/api/v1/stock-analysis/history/${historyId}/pdf`;
  link.style.display = '';
}

function reanalyzeHistory(ticker) {
  const input = _byId('sa-input');
  if (input) input.value = ticker;
  document.querySelector('.sa-search-bar')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  runStockAnalysis(ticker);
}

function _applyHistoryPrice(overlay) {
  const price = _byId('r-price');
  if (price) price.textContent = _won(overlay.current_price);
  const change = _byId('r-change');
  if (change) {
    const pct = overlay.change_pct;
    change.className = pct > 0 ? 'sa-change-pos' : pct < 0 ? 'sa-change-neg' : 'sa-change-neu';
    change.textContent = pct === null || pct === undefined
      ? '' : `${pct >= 0 ? '+' : ''}${Number(pct).toFixed(2)}%`;
  }
  const updated = _byId('r-price-updated');
  if (updated) {
    updated.textContent = `현재가 확인: ${overlay.source} · ${_formatHistoryTime(overlay.refreshed_at)}`;
  }
  const distances = _byId('r-price-distances');
  if (!distances) return;
  const labels = { entry_1: '1차', entry_2: '2차', entry_3: '3차', target: '목표', stop: '손절' };
  distances.innerHTML = '';
  Object.entries(overlay.plan_distances || {}).forEach(([key, distance]) => {
    const item = document.createElement('span');
    const pct = Number(distance.pct);
    item.textContent = `${labels[key] || key} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    distances.appendChild(item);
  });
}

async function openAnalysisHistory(id) {
  const error = _byId('sa-error');
  if (error) error.style.display = 'none';
  try {
    const detail = await _tradeApi(`/api/v1/stock-analysis/history/${id}`, undefined, 'GET');
    _lastAnalysisTradePlan = detail.trade_plan;
    _analysisText = detail.narrative || '';
    renderResult(detail.snapshot);
    const date = _byId('r-date');
    if (date) {
      date.textContent = `분석 기준: ${_formatHistoryTime(detail.created_at)} · 기술 기준일: ${detail.ref_date}`;
    }
    finalizeAnalysis();
    _setPdfLink(id);
    if (_analysisText) _byId('sa-ai-card').style.display = 'block';
    const updated = _byId('r-price-updated');
    if (updated) {
      updated.textContent = detail.price_refreshed_at
        ? `마지막 현재가 확인: ${detail.latest_price_source || '—'} · ${_formatHistoryTime(detail.price_refreshed_at)}`
        : '현재가 확인 전';
    }
    try {
      const overlay = await _tradeApi(
        `/api/v1/stock-analysis/history/${id}/refresh-price`, {}, 'POST'
      );
      _applyHistoryPrice(overlay);
      loadAnalysisHistory();
    } catch {
      if (updated) updated.textContent = '현재가 갱신 실패 · 마지막 확인값';
    }
  } catch (historyError) {
    if (error) {
      error.textContent = `분석 이력을 열지 못했습니다: ${historyError.message}`;
      error.style.display = 'block';
    }
  }
}

/* ── 분석 실행 (SSE) ─────────────────────────────────────── */
function runStockAnalysis(ticker) {
  ticker = (ticker || '').trim();
  if (!ticker) return;

  // 기존 스트림 종료
  if (_activeEs) { _activeEs.close(); _activeEs = null; }
  _setPdfLink(null);

  _setBtn(true, '분석 중…');
  startProgress(ticker);

  const url = `/api/v1/stock-analysis/stream?ticker=${encodeURIComponent(ticker)}`;
  const es = new EventSource(url);
  _activeEs = es;

  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }

    // AI 분석 청크
    if (d.analysis_chunk !== undefined) {
      appendAnalysisChunk(d.analysis_chunk);
      return;
    }

    if (!d.done) {
      updateProgress(d.step, d.pct);
      return;
    }

    // 완료
    es.close();
    _activeEs = null;
    stopProgress();
    finalizeAnalysis();
    _setBtn(false, '▶ 분석');

    if (d.error) {
      _byId('sa-progress').style.display = 'none';
      _byId('sa-error').textContent = '오류: ' + d.error;
      _byId('sa-error').style.display = 'block';
    } else {
      const sec = Math.floor((Date.now() - _startTime) / 1000);
      _byId('prog-step').textContent = `완료 (${sec}초 소요)`;
      _byId('prog-bar').style.width = '100%';
      _byId('prog-pct').textContent = '100%';
      setTimeout(() => {
        _byId('sa-progress').style.display = 'none';
        _lastAnalysisTradePlan = d.trade_plan || null;
        renderResult(d.data);
        if (_byId('sa-history-body')) loadAnalysisHistory();
        if (d.history_error) {
          _byId('sa-error').textContent = `분석은 완료됐지만 이력 저장에 실패했습니다: ${d.history_error}`;
          _byId('sa-error').style.display = 'block';
        }
      }, 600);
    }
  };

  es.onerror = () => {
    es.close();
    _activeEs = null;
    stopProgress();
    finalizeAnalysis();
    _setBtn(false, '▶ 분석');
    _byId('sa-progress').style.display = 'none';
    _byId('sa-error').textContent = '연결 오류: 서버와 통신 중 문제가 발생했습니다.';
    _byId('sa-error').style.display = 'block';
  };
}

/* ── 모달 제어 (워치리스트 클릭→딥다이브) ─────────────────── */
function openStockAnalysisModal(ticker) {
  const modal = _byId('sa-modal');
  if (modal) {
    resetStockAnalysisPanel();
    const title = _byId('sa-modal-ticker');
    if (title) title.textContent = ticker;
    modal.classList.add('open');
  }
  runStockAnalysis(ticker);
}

function closeStockAnalysisModal() {
  const modal = _byId('sa-modal');
  if (modal) modal.classList.remove('open');
  resetStockAnalysisPanel();
}

/* ── 결과 렌더링 ─────────────────────────────────────────── */
function saFmt(n, suffix = '') {
  if (n === null || n === undefined) return '—';
  if (typeof n === 'number') return n.toLocaleString('ko-KR') + suffix;
  return n;
}

function renderResult(d) {
  _lastAnalysis = d;
  const ta  = d['기술적분석']  || {};
  const val = d['밸류에이션']  || {};
  const fin = d['재무제표_3개년'] || {};
  const priceUpdated = _byId('r-price-updated');
  const priceDistances = _byId('r-price-distances');
  if (priceUpdated) priceUpdated.textContent = '';
  if (priceDistances) priceDistances.innerHTML = '';

  _byId('r-name').textContent = d['종목명'] || '—';
  _byId('r-code').textContent = '(' + (d['종목코드'] || '') + ')';
  _byId('r-date').textContent =
    '기준일: ' + (ta['기준일'] || '—') + '  ·  수집: ' + (d['수집시각'] || '—');

  const price  = ta['현재가'];
  const chgPct = ta['전일대비_pct'];
  _byId('r-price').textContent = price
    ? price.toLocaleString('ko-KR') + '원' : '—';

  const chgEl = _byId('r-change');
  if (chgPct !== undefined) {
    const cls = chgPct > 0 ? 'sa-change-pos' : chgPct < 0 ? 'sa-change-neg' : 'sa-change-neu';
    chgEl.className = cls;
    chgEl.textContent = (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%';
  } else {
    chgEl.className = '';
    chgEl.textContent = '';
  }

  const alignEl = _byId('r-align-badge');
  if (ta['정배열_여부'] === true) {
    alignEl.innerHTML = '<span class="sa-badge sa-badge-green">정배열</span>';
  } else if (ta['정배열_여부'] === false) {
    alignEl.innerHTML = '<span class="sa-badge sa-badge-red">역배열</span>';
  } else { alignEl.innerHTML = ''; }

  const rsiCls   = ta['RSI_상태'] === '과매수' ? 'sa-change-pos' : ta['RSI_상태'] === '과매도' ? 'sa-change-neg' : '';
  const cross    = ta['20_60_크로스'];
  const crossLbl = cross === 'golden_cross' ? '🟢 골든크로스' : cross === 'dead_cross' ? '🔴 데드크로스' : '없음';

  _byId('r-technicals').innerHTML = `
    <div class="sa-indicator-row"><span class="sa-indicator-label">52주 고가</span><span class="sa-indicator-val">${saFmt(ta['52주_고가'])}원</span></div>
    <div class="sa-indicator-row"><span class="sa-indicator-label">52주 저가</span><span class="sa-indicator-val">${saFmt(ta['52주_저가'])}원</span></div>
    <div class="sa-indicator-row"><span class="sa-indicator-label">RSI(14)</span><span class="sa-indicator-val"><span class="${rsiCls}">${saFmt(ta['RSI14'])} · ${ta['RSI_상태'] || '—'}</span></span></div>
    <div class="sa-indicator-row"><span class="sa-indicator-label">MACD</span><span class="sa-indicator-val">${saFmt(ta['MACD'])} (시그널: ${saFmt(ta['MACD_signal'])})</span></div>
    <div class="sa-indicator-row"><span class="sa-indicator-label">MACD 방향</span><span class="sa-indicator-val">${ta['MACD_방향'] || '—'}</span></div>
    <div class="sa-indicator-row"><span class="sa-indicator-label">20·60 크로스</span><span class="sa-indicator-val">${crossLbl}</span></div>`;

  const maData = ta['이동평균선'] || {};
  _byId('r-ma').innerHTML = ['MA5','MA20','MA60','MA120'].map(k => {
    const v    = maData[k];
    const diff = (v && price) ? ((price / v - 1) * 100) : null;
    const ds   = diff !== null
      ? (diff >= 0
          ? `<span class="sa-change-pos">+${diff.toFixed(1)}%</span>`
          : `<span class="sa-change-neg">${diff.toFixed(1)}%</span>`)
      : '';
    return `<div class="sa-mini-card">
      <div class="sa-mini-label">${k}</div>
      <div class="sa-mini-val">${v ? v.toLocaleString('ko-KR') : '—'}원</div>
      <div class="sa-mini-sub">현재가 대비 ${ds}</div>
    </div>`;
  }).join('');

  if (val.error) {
    _byId('r-valuation').innerHTML =
      `<div class="text-muted" style="font-size:.82rem">${val.error}</div>`;
  } else {
    _byId('r-valuation').innerHTML = `
      <div class="sa-mini-card"><div class="sa-mini-label">PER</div><div class="sa-mini-val">${saFmt(val['PER'])}배</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">PBR</div><div class="sa-mini-val">${saFmt(val['PBR'])}배</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">EPS</div><div class="sa-mini-val">${saFmt(val['EPS'])}원</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">배당수익률</div><div class="sa-mini-val">${saFmt(val['DIV_배당수익률'])}%</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">시가총액</div><div class="sa-mini-val">${saFmt(val['시가총액_억원'])}억원</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">BPS</div><div class="sa-mini-val">${saFmt(val['BPS'])}원</div></div>
      <div class="sa-mini-card"><div class="sa-mini-label">상장주식수</div><div class="sa-mini-val" style="font-size:.9rem">${saFmt(val['상장주식수'])}주</div></div>`;
  }

  const years = Object.keys(fin).sort().reverse();
  if (fin.error || years.length === 0) {
    _byId('r-financials').innerHTML =
      `<div class="text-muted" style="font-size:.82rem">${fin.error || '재무 데이터 없음'}</div>`;
  } else {
    const rows = [
      ['매출액 (백만원)',       '매출액',          1e6,  ''],
      ['영업이익 (백만원)',     '영업이익',        1e6,  ''],
      ['당기순이익 (백만원)',   '당기순이익',      1e6,  ''],
      ['영업이익률',            '영업이익률_pct',  null, '%'],
      ['ROE',                   'ROE_pct',         null, '%'],
      ['부채비율',              '부채비율_pct',    null, '%'],
      ['자산총계 (백만원)',     '자산총계',        1e6,  ''],
      ['자본총계 (백만원)',     '자본총계',        1e6,  ''],
      ['영업활동현금흐름 (백만원)', '영업활동현금흐름', 1e6, ''],
    ];
    const hdr  = `<tr><th>항목</th>${years.map(y=>`<th>${y}</th>`).join('')}</tr>`;
    const body = rows.map(([lbl, key, div, sfx]) => {
      const cells = years.map(y => {
        const v = fin[y]?.[key];
        if (v === undefined || v === null) return '<td class="mono text-muted">—</td>';
        const disp = div ? Math.round(v / div).toLocaleString('ko-KR') : v.toLocaleString('ko-KR');
        return `<td class="mono">${disp}${sfx}</td>`;
      }).join('');
      return `<tr><td style="font-size:.8rem;color:var(--text-muted)">${lbl}</td>${cells}</tr>`;
    }).join('');
    _byId('r-financials').innerHTML =
      `<table><thead>${hdr}</thead><tbody>${body}</tbody></table>`;
  }

  _byId('sa-result').style.display = 'block';
  _renderAnalysisTradePlan(_lastAnalysisTradePlan);
  const tradeButton = _byId('sa-open-trade');
  if (tradeButton) tradeButton.style.display = 'inline-flex';
  _byId('sa-result').scrollIntoView({ behavior: 'smooth' });

  const chart = ta['차트_6개월'] || [];
  if (chart.length) {
    const dates  = chart.map(c => c.date);
    const closes = chart.map(c => c.close);
    try {
      Plotly.newPlot('sa-chart',
        [{ x: dates, y: closes, type: 'scatter', mode: 'lines',
           line: { color: '#5c9aff', width: 2 },
           fill: 'tozeroy', fillcolor: 'rgba(92,154,255,0.08)', name: '종가' }],
        { paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
          font: { color: '#aaa', size: 11 },
          xaxis: { gridcolor: '#2a2a2a', tickfont: { size: 10 } },
          yaxis: { gridcolor: '#2a2a2a', tickformat: ',d', tickfont: { size: 10 } },
          margin: { l: 55, r: 10, t: 10, b: 40 }, showlegend: false },
        { responsive: true, displayModeBar: false }
      );
    } catch (e) {
      console.error('차트 렌더링 오류:', e);
    }
  }
}

/* ── 분석 결과 → 전략매매 설정 ───────────────────────────── */
function _renderAnalysisTradePlan(plan) {
  const target = _byId('sa-analysis-trade-plan');
  if (!target) return;
  if (!plan || plan.source !== 'AI' || !Array.isArray(plan.entries) || plan.entries.length !== 3) {
    target.innerHTML = '';
    target.style.display = 'none';
    return;
  }
  const recommendation = { BUY: '매수', WATCH: '관찰', SELL: '매도 의견' }[plan.recommendation]
    || plan.recommendation;
  target.innerHTML = `
    <div><span>분석 의견</span><b>${recommendation}</b></div>
    <div><span>1차 매수가</span><b>${_won(plan.entries[0])}</b></div>
    <div><span>2차 매수가</span><b>${_won(plan.entries[1])}</b></div>
    <div><span>3차 매수가</span><b>${_won(plan.entries[2])}</b></div>
    <div><span>목표가</span><b>${_won(plan.target)}</b></div>
    <div><span>손절가</span><b>${_won(plan.stop)}</b></div>`;
  target.style.display = 'grid';
}

function _applyAnalysisTradePlan() {
  const plan = _lastAnalysisTradePlan;
  if (!plan || plan.source !== 'AI' || !Array.isArray(plan.entries) || plan.entries.length !== 3) {
    _clearTradePrices();
    _tradePlanSource = 'MANUAL_REQUIRED';
    _tradeRationale = '';
    _byId('sa-trade-warning').textContent = plan?.message
      || '분석 가격을 사용할 수 없어 가격을 직접 입력해야 합니다.';
    return false;
  }
  plan.entries.forEach((price, index) => {
    _byId(`sa-entry-${index + 1}`).value = price;
  });
  _byId('sa-plan-target').value = plan.target;
  _byId('sa-plan-stop').value = plan.stop;
  _tradePlanSource = 'AI';
  _tradeRationale = plan.rationale || '';
  // 가격은 채우되 권고가 매수가 아니면 그 사실을 화면이 먼저 말한다.
  // WATCH 계획의 가격이 매수 신호로 읽혀 그대로 체결된 적이 있다.
  const label = { WATCH: '관찰', SELL: '매도 의견' }[plan.recommendation];
  _byId('sa-trade-warning').textContent = label
    ? `분석 의견은 '${label}'입니다 — 매수 권고가 아닙니다. 가격은 참고용으로만 불러왔습니다.`
    : '종목분석에 표시된 매수가·목표가·손절가를 그대로 불러왔습니다.';
  return true;
}

function _tradeNumber(id) {
  const value = Number(_byId(id)?.value || 0);
  return Number.isFinite(value) ? value : 0;
}

function _tradeFacts() {
  const data = _lastAnalysis || {};
  const technical = data['기술적분석'] || {};
  const valuation = data['밸류에이션'] || {};
  const averages = technical['이동평균선'] || {};
  return {
    ticker: data['종목코드'] || '',
    name: data['종목명'] || data['종목코드'] || '',
    market: _byId('sa-plan-market')?.value || 'KOSPI',
    ref_date: technical['기준일'] || new Date().toISOString().slice(0, 10),
    current_price: technical['현재가'],
    high_52w: technical['52주_고가'] ?? null,
    low_52w: technical['52주_저가'] ?? null,
    ma20: averages.MA20 ?? null,
    ma60: averages.MA60 ?? null,
    ma120: averages.MA120 ?? null,
    rsi14: technical.RSI14 ?? null,
    macd: technical.MACD ?? null,
    macd_signal: technical.MACD_signal ?? null,
    per: valuation.PER ?? null,
    pbr: valuation.PBR ?? null,
    bps: valuation.BPS ?? null,
  };
}

async function _tradeApi(url, payload, method = 'POST') {
  const options = { method };
  if (payload !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(url, options);
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return body;
}

function _clearTradePrices() {
  ['sa-entry-1', 'sa-entry-2', 'sa-entry-3', 'sa-plan-target', 'sa-plan-stop']
    .forEach(id => { if (_byId(id)) _byId(id).value = ''; });
}

function openTradeSetup() {
  if (!_lastAnalysis) return;
  const dialog = _byId('sa-trade-setup');
  if (!dialog) return;
  _clearTradeTimers();
  _invalidateTradePreview();
  _lastTradeLimits = null;
  _byId('sa-trade-preview').innerHTML = '';
  _byId('sa-trade-validation').textContent = '';
  document.querySelectorAll('input[name="sa-trade-mode"]').forEach(input => { input.checked = false; });
  onTradeModeChange();
  _clearTradePrices();
  _byId('sa-plan-budget').value = '';
  _byId('sa-plan-budget').removeAttribute('max');
  const market = _lastAnalysis['시장'];
  const marketInput = _byId('sa-plan-market');
  if (market && [...marketInput.options].some(option => option.value === market)) {
    marketInput.value = market;
  }
  _byId('sa-trade-symbol').textContent = `${_lastAnalysis['종목명'] || ''} ${_lastAnalysis['종목코드'] || ''}`;
  _byId('sa-trade-warning').textContent = '구조화 AI 매매계획을 확인하고 있습니다.';
  dialog.showModal();
  _applyAnalysisTradePlan();
}

function closeTradeSetup() {
  _clearTradeTimers();
  _invalidateTradePreview();
  const dialog = _byId('sa-trade-setup');
  if (dialog?.open) dialog.close();
}

function onTradeModeChange() {
  const mode = document.querySelector('input[name="sa-trade-mode"]:checked')?.value;
  document.querySelectorAll('.sa-split-only').forEach(element => {
    element.style.display = mode === 'split' ? 'flex' : 'none';
  });
  const weight = _byId('sa-weight-1');
  if (weight) weight.textContent = mode === 'single' ? '100%' : mode === 'split' ? '30%' : '';
  _invalidateTradePreview();
  if (mode) _scheduleTradeLimits(false);
}

function _buildTradeLimitPayload() {
  const mode = document.querySelector('input[name="sa-trade-mode"]:checked')?.value;
  if (!mode) throw new Error('단일매매 또는 3분할매매를 선택하세요.');
  const facts = _tradeFacts();
  const entries = mode === 'single'
    ? [{ sequence: 1, entry_price: _tradeNumber('sa-entry-1'), weight_pct: 100 }]
    : [
        { sequence: 1, entry_price: _tradeNumber('sa-entry-1'), weight_pct: 30 },
        { sequence: 2, entry_price: _tradeNumber('sa-entry-2'), weight_pct: 30 },
        { sequence: 3, entry_price: _tradeNumber('sa-entry-3'), weight_pct: 40 },
      ];
  const target = _tradeNumber('sa-plan-target');
  const stop = _tradeNumber('sa-plan-stop');
  const prices = entries.map(item => item.entry_price);
  if (target <= 0 || stop <= 0 || prices.some(price => price <= 0)) {
    throw new Error('모든 가격을 입력하세요.');
  }
  if (!(target > prices[0] && prices.every((price, index) => index === 0 || prices[index - 1] > price) && prices.at(-1) > stop)) {
    throw new Error('목표가 > 진입가 순서 > 손절가를 확인하세요.');
  }
  const analysisPlan = _lastAnalysisTradePlan;
  const analysisEntries = mode === 'single'
    ? [Number(analysisPlan?.entries?.[0])]
    : (analysisPlan?.entries || []).map(Number);
  const usesAnalysisPrices = analysisPlan?.source === 'AI'
    && entries.every((item, index) => item.entry_price === analysisEntries[index])
    && target === Number(analysisPlan.target)
    && stop === Number(analysisPlan.stop);
  return {
    ticker: facts.ticker,
    name: facts.name,
    market: facts.market,
    ref_date: facts.ref_date,
    source: usesAnalysisPrices ? 'ai_trade_plan' : 'manual',
    trade_mode: mode,
    entries,
    target_price: target,
    stop_price: stop,
    rationale: _tradeRationale || null,
    regime: 'mixed',
    strategy_context: 'stock_analysis',
    ai_recommendation: usesAnalysisPrices ? (analysisPlan.recommendation || null) : null,
  };
}

function _buildTradePayload() {
  const payload = _buildTradeLimitPayload();
  const budget = _tradeNumber('sa-plan-budget');
  if (budget <= 0) throw new Error('총 매수금액을 입력하세요.');
  return { ...payload, total_budget: budget };
}

function _won(value) {
  return `${Number(value || 0).toLocaleString('ko-KR')}원`;
}

function _renderTradePreview(preview) {
  const limits = preview.limits || {};
  const blockerHtml = (preview.blockers || []).map(item =>
    `<li><b>${saFmt(item.code)}</b> ${saFmt(item.message)}</li>`
  ).join('');
  const warningHtml = _tradeWarningHtml(preview.warnings);
  const legRows = (preview.legs || []).map(leg => `
    <tr><td>${leg.sequence}차</td><td>${_won(leg.entry_price)}</td><td>${leg.weight_pct}%</td>
      <td>${leg.planned_qty}주</td><td>${_won(leg.order_amount)}</td></tr>`).join('');
  _byId('sa-trade-preview').innerHTML = `
    <div class="sa-trade-limits">
      <div><span>브로커 현금</span><b>${_won(limits.broker_cash)}</b></div>
      <div><span>동일종목 잔여한도</span><b>${_won(limits.single_exposure)}</b></div>
      <div><span>포트폴리오 여력</span><b>${_won(limits.portfolio_capacity)}</b></div>
      <div><span>손절 위험예산</span><b>${_won(limits.stop_risk)}</b></div>
      <div class="sa-safe-max"><span>안전 최대금액</span><b>${_won(preview.safe_max_amount)}</b></div>
      <div><span>예상 잔여현금</span><b>${_won(preview.expected_remaining_cash)}</b></div>
    </div>
    <table><thead><tr><th>회차</th><th>진입가</th><th>비중</th><th>planned_qty</th><th>주문금액</th></tr></thead>
      <tbody>${legRows}</tbody></table>
    ${warningHtml}
    ${blockerHtml ? `<ul class="sa-trade-blockers">${blockerHtml}</ul>` : '<p class="sa-trade-ok">서버 안전검증을 통과했습니다.</p>'}`;
}

// 경고는 차단이 아니다 — 무장을 막지 않고 상충 사실만 남긴다.
function _tradeWarningHtml(warnings) {
  const html = (warnings || []).map(item =>
    `<li><b>${saFmt(item.code)}</b> ${saFmt(item.message)}</li>`
  ).join('');
  return html ? `<ul class="sa-trade-warnings">${html}</ul>` : '';
}

function _renderTradeLimits(result) {
  const limits = result.limits || {};
  const blockerHtml = (result.blockers || []).map(item =>
    `<li><b>${saFmt(item.code)}</b> ${saFmt(item.message)}</li>`
  ).join('');
  _byId('sa-trade-preview').innerHTML = `
    <div class="sa-trade-limits">
      <div><span>브로커 현금</span><b>${_won(limits.broker_cash)}</b></div>
      <div><span>동일종목 잔여한도</span><b>${_won(limits.single_exposure)}</b></div>
      <div><span>포트폴리오 여력</span><b>${_won(limits.portfolio_capacity)}</b></div>
      <div><span>손절 위험예산</span><b>${_won(limits.stop_risk)}</b></div>
      <div class="sa-safe-max"><span>안전 최대금액</span><b>${_won(result.safe_max_amount)}</b></div>
      <div><span>최소 주문가능 금액</span><b>${_won(result.minimum_orderable_amount)}</b></div>
    </div>
    ${_tradeWarningHtml(result.warnings)}
    ${blockerHtml ? `<ul class="sa-trade-blockers">${blockerHtml}</ul>` : ''}`;
}

function _scheduleTradeLimits(invalidate = true) {
  _clearTradeTimers();
  if (invalidate) _invalidateTradePreview();
  const version = _tradeRequestVersion;
  _tradeLimitTimer = setTimeout(() => _refreshTradeLimits(version), TRADE_DEBOUNCE_MS);
}

function _scheduleTradePreview() {
  _clearTradeTimers();
  _invalidateTradePreview();
  const version = _tradeRequestVersion;
  _tradePreviewTimer = setTimeout(() => previewTradeSetup(version), TRADE_DEBOUNCE_MS);
}

async function _refreshTradeLimits(expectedVersion = _tradeRequestVersion) {
  _tradeLimitTimer = null;
  _byId('sa-trade-validation').textContent = '';
  try {
    const payload = _buildTradeLimitPayload();
    const limits = await _tradeApi('/api/v1/analysis-picks/trade-limits', payload);
    if (expectedVersion !== _tradeRequestVersion) return;
    _lastTradeLimits = limits;
    _renderTradeLimits(limits);
    const budget = _byId('sa-plan-budget');
    const maximum = Math.floor(limits.safe_max_amount);
    const minimum = Math.ceil(limits.minimum_orderable_amount);
    budget.max = String(maximum);
    if (maximum > 0 && maximum >= minimum) {
      budget.value = String(maximum);
      await previewTradeSetup(expectedVersion);
    } else {
      budget.value = '';
      _byId('sa-trade-validation').textContent =
        `안전 최대금액 ${_won(maximum)}이 최소 주문가능 금액 ${_won(minimum)}보다 작습니다.`;
    }
  } catch (error) {
    if (expectedVersion !== _tradeRequestVersion) return;
    _lastTradeLimits = null;
    _byId('sa-plan-budget').value = '';
    _byId('sa-trade-validation').textContent = error.message;
  }
}

async function previewTradeSetup(expectedVersion = _tradeRequestVersion) {
  _tradePreviewTimer = null;
  _byId('sa-arm-trade').disabled = true;
  _byId('sa-trade-validation').textContent = '';
  try {
    const payload = _buildTradePayload();
    const signature = JSON.stringify(payload);
    const preview = await _tradeApi('/api/v1/analysis-picks/trade-preview', payload);
    if (expectedVersion !== _tradeRequestVersion) return;
    _lastTradePreview = preview;
    _lastTradePreviewSignature = signature;
    _renderTradePreview(preview);
    if (!preview.blocked) {
      _byId('sa-arm-trade').disabled = false;
    }
  } catch (error) {
    if (expectedVersion !== _tradeRequestVersion) return;
    _lastTradePreview = null;
    _lastTradePreviewSignature = '';
    _byId('sa-trade-validation').textContent = error.message;
  }
}

async function armTradePlan() {
  if (!_lastTradePreview || _lastTradePreview.blocked) return;
  let payload;
  try {
    payload = _buildTradePayload();
  } catch (error) {
    _byId('sa-trade-validation').textContent = error.message;
    return;
  }
  if (JSON.stringify(payload) !== _lastTradePreviewSignature) {
    _invalidateTradePreview();
    _byId('sa-trade-validation').textContent = '입력값이 바뀌었습니다. 자동 안전검증이 끝난 뒤 다시 실행하세요.';
    return;
  }
  if (!window.confirm('현재 계좌와 안전한도를 다시 확인해 ARMED 상태로 저장할까요?')) return;
  _byId('sa-arm-trade').disabled = true;
  try {
    const armed = await _tradeApi('/api/v1/analysis-picks/arm-plan', payload);
    _byId('sa-trade-validation').innerHTML =
      `ARMED 저장 완료 · 계획 #${armed.pick_id} · 주문은 스케줄러가 조건 충족 시 제출합니다. ` +
      '<a href="/analysis-picks">워치리스트에서 확인</a>';
  } catch (error) {
    _invalidateTradePreview();
    _byId('sa-trade-validation').textContent = `최종 무장 차단: ${error.message}`;
  }
}

/* ── 공통 초기화: ?ticker= 딥링크 + ESC로 모달 닫기 ───────── */
document.addEventListener('DOMContentLoaded', () => {
  const input = _byId('sa-input');
  if (input) input.focus();
  if (_byId('sa-history-body')) loadAnalysisHistory();

  ['sa-plan-market', 'sa-entry-1', 'sa-entry-2', 'sa-entry-3', 'sa-plan-target', 'sa-plan-stop']
    .forEach(id => _byId(id)?.addEventListener('input', () => {
      if (document.querySelector('input[name="sa-trade-mode"]:checked')) {
        _scheduleTradeLimits();
      } else {
        _invalidateTradePreview();
      }
    }));
  _byId('sa-plan-budget')?.addEventListener('input', () => {
    const budget = _byId('sa-plan-budget');
    const maximum = Number(budget.max || 0);
    if (maximum > 0 && Number(budget.value || 0) > maximum) {
      _clearTradeTimers();
      _invalidateTradePreview();
      _byId('sa-trade-validation').textContent = `총 매수금액은 안전 최대금액 ${_won(maximum)} 이하여야 합니다.`;
      return;
    }
    _scheduleTradePreview();
  });

  // 모달이 있는 화면이면 ESC로 닫기
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const modal = _byId('sa-modal');
      if (modal && modal.classList.contains('open')) closeStockAnalysisModal();
    }
  });

  // ?ticker= 딥링크 자동 실행 (단독 화면)
  const qp = new URLSearchParams(window.location.search).get('ticker');
  if (qp && _byId('sa-progress') && !_byId('sa-modal')) {
    if (input) input.value = qp;
    runStockAnalysis(qp);
  }
});
