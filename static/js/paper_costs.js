/* Evidence-backed paper settlement operator controls. Server owns accounting. */
(() => {
  const get = id => document.getElementById('pc-' + id);
  const endpoint = '/api/v1/risk/execution-safety';
  let current = null, preview = null, busy = false, generation = 0;
  const orderRows = [];
  const invalidate = () => { generation++; preview = null; get('commit').disabled = true; };
  const display = value => value == null ? '확인 불가' : String(value);
  const reasonLabels = {
    paper_costs_missing:'증권사 비용 자료가 없습니다',paper_costs_unstable:'비용 자료의 연속 관측 확인이 필요합니다',
    paper_costs_coverage_missing:'전체 비교 구간의 비용 근거가 부족합니다',paper_costs_overlap:'기확정 비용과 중복되는 구간입니다',
    paper_costs_corrected:'증권사 비용 자료가 수정되어 재검토가 필요합니다',paper_costs_cash_mismatch:'비용과 현금 차이가 일치하지 않습니다',
    paper_costs_order_gross_missing:'주문 체결금액 자료가 부족합니다',paper_costs_observe:'관찰 모드로 자동 정산을 보류합니다',
    paper_costs_account_incomplete:'계좌 또는 주문 검증이 완료되지 않았습니다',actual_costs_reconciled:'실제 비용과 계좌 검증이 완료되었습니다',
    settled_interval_unchanged:'기정산 구간의 비용에 변동이 없습니다',account_baseline_missing:'비교 기준 관측이 없습니다',account_unobserved:'계좌 관측이 없습니다',
    account_cost_coverage_required:'기존 계좌 비용 근거를 선택해야 합니다',broker_statement_provenance_required:'증권사 명세서 출처와 해시가 필요합니다',
    reconciliation_stale:'계좌 관측이 오래되었습니다. 다시 조회하세요',settlement_changed:'미리보기 이후 계좌 또는 정산 자료가 변경되었습니다',version_conflict:'계좌 버전이 변경되었습니다',
    order_cost_identity_mismatch:'주문 정보와 명세서 정보가 일치하지 않습니다',order_cost_exceeds_account_total:'주문 비용 합계가 계좌 근거 총액을 초과합니다',
    exact_order_cost_required:'정확한 주문 체결금액·수수료·세금이 필요합니다',statement_cash_mismatch:'명세서 총액과 계좌 현금 차이가 일치하지 않습니다',
  };
  const reasonText = value => reasonLabels[value] ? `${reasonLabels[value]} (${value})` : display(value);
  const modeText = value => ({observe:'관찰',auto:'자동 정산'})[value] || display(value);
  const sumCosts = (fees, tax) => {
    if (![fees,tax].every(value=>/^\d+(\.\d+)?$/.test(value))) throw new Error('수수료·세금은 음수가 아닌 소수로 입력하세요');
    const scale = Math.max(...[fees,tax].map(value=>(value.split('.')[1] || '').length));
    const integer = value => {const [whole,fraction=''] = value.split('.'); return BigInt(whole + fraction.padEnd(scale,'0'));};
    const total = (integer(fees)+integer(tax)).toString().padStart(scale+1,'0');
    return scale ? total.slice(0,-scale)+'.'+total.slice(-scale) : total;
  };
  const request = async (path, body) => {
    const response = await fetch(endpoint + path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {method:'GET'});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? reasonText(data.detail) : '요청 거절');
    return data;
  };
  const line = (parent, text) => { const node = document.createElement('p'); node.textContent = text; parent.appendChild(node); };
  const renderOrders = () => {
    orderRows.length = 0; get('orders').replaceChildren();
    for (const id of ['amount','source','hash']) get(id).disabled = get('order-only').checked;
    for (const trade of (get('order-only').checked ? current.order_cost_candidates : current.breakdown.trades) || []) {
        const row = document.createElement('fieldset'); const title = document.createElement('legend'); title.textContent = `주문 ${trade.broker_order_id} 명세서 (선택) · ${trade.ticker} ${trade.side} ${trade.qty}주 · ${trade.notional}원` + (get('order-only').checked ? ` · 계좌 근거 ${display(trade.account_evidence_id)} · ${display(trade.period_start)} ~ ${display(trade.period_end)}` : ''); row.appendChild(title);
        const fields = {};
        for (const [name,label,type] of [['selected','이 주문 비용 근거 포함','checkbox'],['gross','명세서 체결금액','number'],['fees','수수료','number'],['tax','세금','number'],['source','주문 명세서 HTTPS URL','url'],['hash','주문 명세서 SHA-256','text']]) {
          const wrapper = document.createElement('label'); wrapper.textContent = label; const input = document.createElement('input'); input.type = type; input.value = ''; input.oninput = invalidate; input.onchange = invalidate; wrapper.appendChild(input); row.appendChild(wrapper); fields[name] = input;
        }
        orderRows.push({trade,fields}); get('orders').appendChild(row);
      }
  };
  const load = async () => {
    invalidate(); current = null; get('preview').disabled = true; orderRows.length = 0; get('orders').replaceChildren();
    try {
      const data = await request(''); current = data.paper_costs;
      if (!current || !current.supported || !current.breakdown?.available) throw new Error('현재 비교 구간 확인 불가');
      const b = current.breakdown;
      get('summary').textContent = `현재 비교 구간 ${display(b.period_start)} ~ ${display(b.period_end)} · 잔차 ${display(b.residual)}원 · 기대 현금 ${display(b.expected_cash)} · 실제 현금 ${display(b.actual_cash)} · 기준 관측 ${display(b.baseline_id)} (${display(b.baseline_observed_at)}) · 최신 관측 ${display(b.observation_id)} (${display(b.observed_at)}) · 비용 ${display(b.broker_cost_delta)} · 정산 ${display(b.settled_costs)} · 수량 차이 ${JSON.stringify(b.quantity_changes ?? {})} · 비용 근거 ${reasonText(current.cost_coverage?.reason)} · 모드 ${modeText(current.mode)} · 버전 ${display(current.version)}`;
      const details = get('details'); details.replaceChildren();
      for (const row of current.pending_adjustments || []) line(details, `과거 미확인 차이 (현재 비용 총액 아님): ${row.amount}원 · ID ${row.id} · 버전 ${row.version} · 근거 ${JSON.stringify(row.evidence ?? {})}`);
      for (const row of b.confirmed_adjustments || []) line(details, `기확정 보정: ${row.kind} ${row.amount}원 · ID ${row.id} · 버전 ${row.version}`);
      for (const row of b.settlements || []) line(details, `기정산 영수증: ID ${row.id} · 차감 ${row.deduction}원`);
      renderOrders();
      get('reviewed').checked = false; get('preview').disabled = false;
    } catch (error) { current = null; get('summary').textContent = `조회 실패: ${error.message}. 정산 불가`; }
  };
  const evidence = (source, hash) => {
    if (new URL(source).protocol !== 'https:' || !/^[a-fA-F0-9]{64}$/.test(hash)) throw new Error('실제 증권사 명세서 HTTPS URL과 SHA-256이 필요합니다');
  };
  const draft = () => {
    if (!current || busy || !get('reviewed').checked) throw new Error('현재 구간과 기확정 비용/영수증 포함 여부를 검토하세요');
    const b = current.breakdown, amount = get('amount').value.trim(), reason = get('reason').value.trim();
    const orderOnly = get('order-only').checked;
    if (!reason) throw new Error('정산 사유를 입력하세요');
    const base = {period_start:b.period_start,period_end:b.period_end};
    const items = [];
    if (!orderOnly) {
      if (!amount || !Number.isFinite(Number(amount)) || Number(amount) < 0) throw new Error('전체 비교 구간의 실제 비용 총액을 입력하세요 (실제 0 허용)');
      evidence(get('source').value.trim(), get('hash').value.trim());
      items.push({...base,scope:'account',amount,source_url:get('source').value.trim(),document_hash:get('hash').value.trim(),included_adjustment_ids:(b.confirmed_adjustments || []).filter(r=>r.kind==='fee').map(r=>r.id),included_settlement_ids:(b.settlements || []).map(r=>r.id)});
    }
    for (const {trade,fields:f} of orderRows) if (f.selected.checked) {
      for (const name of ['gross','fees','tax']) if (!f[name].value.trim() || !Number.isFinite(Number(f[name].value)) || Number(f[name].value)<0) throw new Error('선택 주문의 체결금액, 수수료, 세금을 모두 입력하세요');
      evidence(f.source.value.trim(),f.hash.value.trim());
      items.push({...base,...(orderOnly ? {account_evidence_id:trade.account_evidence_id,period_start:trade.period_start,period_end:trade.period_end} : {}),scope:'order',broker_order_id:trade.broker_order_id,gross:f.gross.value,fees:f.fees.value,tax:f.tax.value,amount:sumCosts(f.fees.value.trim(),f.tax.value.trim()),source_url:f.source.value.trim(),document_hash:f.hash.value.trim()});
    }
    if (!items.length) throw new Error('명세서가 있는 주문을 하나 이상 선택하세요');
    return {reason,items};
  };
  get('refresh').onclick = load;
  get('order-only').onchange = () => {invalidate(); if (current) renderOrders();};
  for (const id of ['amount','source','hash','reason','reviewed']) {get(id).oninput = invalidate; get(id).onchange = invalidate;}
  get('preview').onclick = async () => {
    invalidate(); const token = generation;
    try { const body = draft(); busy = true; get('preview').disabled = true; const result = await request('/settlements/preview',body);
      if (token !== generation) return;
      get('result').textContent = `차감 ${display(result.deduction)}원 · 남은 잔차 ${display(result.remaining_residual)}원 · ${reasonText(result.message)}`;
      if (result.can_commit && result.fingerprint && result.version != null) {preview = {body,result}; get('commit').disabled = false;}
    } catch(error) {invalidate(); get('result').textContent = error.message;} finally {busy = false; get('preview').disabled = !current;}
  };
  get('commit').onclick = async () => {
    if (!preview || busy) return;
    const saved = preview; invalidate(); busy = true; get('preview').disabled = true;
    try { const id = crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(24)), x=>x.toString(16).padStart(2,'0')).join('');
      const receipt = await request('/settlements/commit',{...saved.body,fingerprint:saved.result.fingerprint,version:saved.result.version,idempotency_key:id});
      get('result').textContent = `정산 접수: ${receipt.status || 'awaiting_reconciliation'} · 다음 계좌 검증을 기다립니다. 전략 활성화는 별도입니다.`; current = null;
    } catch(error) {current = null; get('result').textContent = `정산 실패: ${error.message}. 새로 조회하세요`;} finally {busy = false; get('preview').disabled = true;}
  };
  load();
})();
