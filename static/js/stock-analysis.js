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

function _byId(id) { return document.getElementById(id); }
function _setBtn(disabled, text) {
  const btn = _byId('sa-btn');
  if (!btn) return;            // 단독 화면 전용 버튼 — 모달에는 없음
  btn.disabled = disabled;
  btn.textContent = text;
}

/* ── 진행률 UI 업데이트 ──────────────────────────────────── */
function startProgress(ticker) {
  _startTime = Date.now();
  _analysisText = '';
  _aiStarted = false;
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
}

/* ── 분석 실행 (SSE) ─────────────────────────────────────── */
function runStockAnalysis(ticker) {
  ticker = (ticker || '').trim();
  if (!ticker) return;

  // 기존 스트림 종료
  if (_activeEs) { _activeEs.close(); _activeEs = null; }

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
        renderResult(d.data);
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
  const ta  = d['기술적분석']  || {};
  const val = d['밸류에이션']  || {};
  const fin = d['재무제표_3개년'] || {};

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

/* ── 공통 초기화: ?ticker= 딥링크 + ESC로 모달 닫기 ───────── */
document.addEventListener('DOMContentLoaded', () => {
  const input = _byId('sa-input');
  if (input) input.focus();

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
