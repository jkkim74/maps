/* US paper operator. API values are rendered only as text. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const base = '/api/v1/us-trading';
  const labels = {BUY:'매수', SELL:'매도', SUBMITTED:'접수', PENDING:'접수 대기', OPEN:'미체결', PARTIALLY_FILLED:'부분체결', FILLED:'체결 완료', CANCEL_PENDING:'취소 확인 중', CANCELLED:'취소', REJECTED:'거절', UNKNOWN:'결과 불명', mock:'로컬 모의', kis_paper:'KIS 모의', regular:'정규장', closed:'장 종료', holiday:'휴장', unknown:'확인 불가', manual:'관리자 수동', matched:'일치', mismatch:'불일치', unattributed:'미귀속 보유'};
  Object.assign(labels, {buy:'매수',sell:'매도',mock_paper:'로컬 모의',confirmed:'일치',pending:'미체결',partially_filled:'부분체결',filled:'체결 완료',cancelled:'취소',rejected:'거절',cancel_pending:'취소 확인 중',unknown:'결과 불명'});
  Object.assign(labels, {us_disabled:'미국 모의매매 설정이 필요합니다.',us_trading_disabled:'미국 모의매매가 비활성 상태입니다.',dry_run:'주문 전송이 비활성 상태입니다.',us_paper_only:'모의환경만 허용합니다.',us_account_missing:'미국 모의계좌 설정이 필요합니다.',us_account_not_dedicated:'미국 전용 모의계좌를 확인하세요.',us_broker_unconfigured:'모의 브로커 설정을 확인하세요.',entry_stop:'신규 매수가 수동 중단되었습니다.',initial_sync_required:'최초 계좌 동기화가 필요합니다.',sync_stale:'계좌 동기화 자료가 오래되었습니다.',observation_stale:'관측 자료가 오래되었습니다.',observation_timestamp_missing:'관측 시각을 확인할 수 없습니다.',unknown_order_unresolved:'결과 불명 주문을 대조 중입니다. 재주문하지 마세요.',costs_unconfirmed:'체결 비용이 미확정입니다.',usd_limits_missing:'USD 주문·노출·손실 한도 설정이 필요합니다.',us_market_closed:'미국 정규장이 아닙니다.',quote_realtime_unconfirmed:'실시간 시세를 확인할 수 없습니다.',symbol_not_allowed:'허용되지 않은 종목입니다.',whole_shares_required:'양의 정수 수량만 허용합니다.',us_price_tick_invalid:'USD 지정가는 0.01 단위로 입력하세요.',order_limit:'주문 금액 한도를 초과했습니다.',exposure_limit:'계좌 노출 한도를 초과했습니다.',daily_loss_limit:'일일 손실 한도에 도달했습니다.',buying_power_insufficient:'주문 가능 금액 또는 수량이 부족합니다.',owned_shares_insufficient:'해당 출처의 매도 가능 수량이 부족합니다.',broker_sellable_shares_insufficient:'브로커 매도 가능 수량이 부족합니다.',sell_consent_required:'해당 종목과 수량의 명시적 매도 승인이 필요합니다.',request_id_conflict:'동일 요청 ID의 주문 조건이 다릅니다.',unexplained_cash_movement:'계좌 현금 변동 대조가 필요합니다.',unexplained_position_movement:'보유 수량 변동 대조가 필요합니다.',owned_shares_exceed_broker:'귀속 수량과 브로커 보유가 다릅니다.',loss_baseline_missing:'미국 거래일 손실 기준값을 확인해야 합니다.'});
  const label = value => labels[value] || value || '—';
  const num = value => value == null ? '—' : Number(value).toLocaleString('ko-KR', {maximumFractionDigits:4});
  const time = value => value ? new Date(value).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', hour12:false}) + ' KST' : '—';
  const message = value => { $('us-message').textContent = value; };
  let status = null, quote = null, pending = null, submitting = false, uncertain = false, cancelOrder = null, loading = false, revision = 0;
  const storageKey = 'maps-us-paper-pending-request';
  try { pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); uncertain = !!pending; } catch (error) { message('이전 요청 복원 실패. 주문 목록과 대조 결과를 확인하세요.'); uncertain = true; }
  async function call(path, method='GET', body) {
    const response = await fetch(base + path, {method, headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) { const detail = data.detail; const error = new Error(typeof detail === 'string' ? label(detail) : label(detail?.message || detail?.code) || '요청 실패 (' + response.status + ')'); error.definite = response.status >= 400 && response.status < 500 && detail?.code !== 'order_outcome_unknown'; throw error; }
    return data;
  }
  function table(target, headers, rows) {
    const container = $(target); container.replaceChildren();
    if (!rows.length) { container.textContent = '조회된 기록이 없습니다.'; return; }
    const table = document.createElement('table'), head = document.createElement('thead'), tr = document.createElement('tr');
    headers.forEach(text => { const th = document.createElement('th'); th.scope='col'; th.textContent=text; tr.append(th); }); head.append(tr); table.append(head);
    const body = document.createElement('tbody');
    rows.forEach(cells => { const row = document.createElement('tr'); cells.forEach(value => { const cell = document.createElement('td'); if (value instanceof Node) cell.append(value); else cell.textContent = value == null ? '—' : String(value); row.append(cell); }); body.append(row); });
    table.append(body); container.append(table);
  }
  function button(text, action) { const node=document.createElement('button'); node.type='button'; node.textContent=text; node.addEventListener('click', action); return node; }
  function selected() { return status?.symbols?.find(s => s.ticker + ':' + s.exchange === $('us-symbol').value); }
  function updateGate() { $('us-preview').disabled = !status || !selected() || quote?.fresh !== true || submitting || uncertain || ($('us-side').value === 'buy' && status.buy_allowed !== true); }
  function discardPreview() { if (!uncertain && !submitting) { pending=null; $('us-confirm').close(); } updateGate(); }
  function summary(order) { return ['모의계좌: ' + (status?.account_masked || '—'), '종목: ' + order.ticker + ' · ' + order.exchange, '방향: ' + label(order.side), '수량: ' + num(order.quantity), '지정가: ' + num(order.limit_price) + ' USD', '금액: ' + num(Number(order.quantity)*Number(order.limit_price)) + ' USD · 수수료 별도', '출처: '+label(order.source || 'manual'), '실행 인스턴스: '+(order.instance_id || 'manual'), '전략 버전: '+(order.strategy_version || '—'), '요청 ID: ' + order.request_id].join('\n'); }
  async function loadQuote() {
    quote=null; updateGate(); const symbol=selected(); if (!symbol) { $('us-quote').textContent='종목을 선택하세요.'; return; }
    $('us-quote').textContent='시세 조회 중'; const key=$('us-symbol').value;
    try { const data=await call('/quotes?' + new URLSearchParams({ticker:symbol.ticker,exchange:symbol.exchange})); if (key!==$('us-symbol').value) return; quote=data;
      $('us-quote').textContent = symbol.ticker + ' · ' + symbol.exchange + ' · USD\n현재가 ' + num(data.price) + ' USD\n관측 ' + time(data.observed_at) + '\n경과 ' + num(data.age_seconds) + '초 · ' + (data.fresh===true?'최신 시세':'지연 또는 미확인 · 신규 주문 차단') + '\n매도 가능 수량 ' + num(data.sellable_quantity) + '\n종목 주문 가능액 ' + num(data.buying_power_usd) + ' USD';
      $('us-quote').style.whiteSpace='pre-wrap';
    } catch(error) { $('us-quote').textContent='시세 조회 실패: ' + error.message; } updateGate();
  }
  async function loadOrders() {
    const query = new URLSearchParams(); [['ticker','symbol'],['side','side'],['status','status'],['source','source']].forEach(([key,id])=>{const value=$('us-filter-'+id).value.trim();if(value)query.set(key,value);});
    const date=$('us-filter-date').value; if(date){query.set('date_from',date);query.set('date_to',date);}
    const data=await call('/orders?'+query);
    const recovered = uncertain && pending && data.items.find(order => order.request_id === pending.request_id && !['unknown','UNKNOWN'].includes(order.status));
    if (recovered) { sessionStorage.removeItem(storageKey); pending=null; uncertain=false; message('이전 요청 확인: '+label(recovered.status)+' · 주문 상세에서 체결과 잔량을 확인하세요.'); updateGate(); }
    table('us-orders',['시각 KST','종목 · 거래소','방향 · 출처','주문 / 체결 / 잔량','지정가 / 평균가 USD','상태','동작'],data.items.map(order=>[time(order.created_at),order.ticker+' · '+order.exchange,label(order.side)+' · '+label(order.source),[order.quantity,order.filled_quantity,order.remaining_quantity].map(num).join(' / '),[order.limit_price,order.average_fill_price].map(num).join(' / '),label(order.status),button('상세 보기',()=>showOrder(order.id))]));
  }
  async function showOrder(id) {
    try { const order=await call('/orders/'+encodeURIComponent(id)); $('us-detail-panel').hidden=false; const box=$('us-detail'); box.replaceChildren(); const pre=document.createElement('pre');
      pre.textContent=summary(order)+'\n브로커 주문번호: '+(order.broker_order_id || '—')+'\n상태: '+label(order.status)+'\n체결 / 잔량: '+num(order.filled_quantity)+' / '+num(order.remaining_quantity)+'\n비용: '+(order.costs_confirmed?'확정':'미확정')+'\n감사 이력:\n'+(order.events || []).map(event=>time(event.created_at)+' '+label(event.event_type)+' '+(event.message || '')).join('\n');box.append(pre);
      if(order.cancellable===true && Number(order.remaining_quantity)>0 && order.broker_order_id) box.append(button('잔량 취소 요청',()=>{cancelOrder=order;$('us-cancel-summary').textContent=order.ticker+' · 잔량 '+num(order.remaining_quantity)+' · '+label(order.status);$('us-cancel-dialog').showModal();}));
    } catch(error) { message('주문 상세 조회 실패: '+error.message); }
  }
  async function refresh() {
    if(loading)return;loading=true;$('us-refresh').disabled=true;
    try { const [s,a,p,e]=await Promise.all([call('/status'),call('/account'),call('/positions'),call('/events')]);status=s;
      $('us-environment').textContent=label(s.environment);$('us-account-label').textContent='미국 전용 모의계좌 '+(s.account_masked || '—');
      $('us-market').textContent=label(s.market_state)+' · 미국 거래일 '+(s.trading_date || '—')+' · 마지막 동기화 '+time(s.last_sync_at);
      $('us-state').textContent=(s.buy_allowed===true?'신규 매수 가능':'신규 매수 차단')+'\n'+(s.block_reasons || []).map(label).join(' · ');
      [['cash','cash_usd'],['power','buying_power_usd'],['value','market_value_usd'],['unknown','unknown_order_count']].forEach(([id,key])=>$('us-'+id).textContent=num(a[key]));
      if(a.buying_power_usd==null)$('us-power').textContent='종목 선택 후 조회';
      const select=$('us-symbol'), old=select.value;select.replaceChildren(new Option('종목을 선택하세요',''));(s.symbols || []).forEach(symbol=>select.add(new Option(symbol.ticker+' · '+symbol.exchange+' · '+(symbol.name || ''),symbol.ticker+':'+symbol.exchange)));select.value=old;select.disabled=false;
      $('us-sync').disabled=s.sync_running===true;$('us-stop').disabled=s.entry_stopped===true;$('us-resume').disabled=s.entry_stopped!==true;
      table('us-positions',['종목 · 거래소','출처','브로커 / 귀속 / 예약 / 매도가능','평균 / 현재가 USD','평가손익 USD · 잠정','대조','동작'],p.items.map(position=>[position.ticker+' · '+position.exchange,label(position.source),[position.broker_quantity,position.owned_quantity,position.reserved_quantity,position.sellable_quantity].map(num).join(' / '),[position.average_price,position.current_price].map(num).join(' / '),num(position.unrealized_pnl),label(position.reconciliation_state),position.source==='manual' && Number(position.sellable_quantity)>0?button('매도 입력',()=>{select.value=position.ticker+':'+position.exchange;$('us-side').value='sell';$('us-input-sell-label').hidden=false;$('us-input-sell-consent').checked=false;$('us-quantity').value=position.sellable_quantity;discardPreview();loadQuote();$('us-quantity').focus();}):'미귀속 / 매도 불가']));
      $('us-reconciliation').textContent='최근 동기화 '+time(s.last_sync_at)+' · '+(s.sync_running?'동기화 진행 중':'저장된 대조 상태');
      table('us-events',['시각 KST','유형','주문','처리자','결과'],e.items.map(event=>[time(event.created_at),label(event.event_type),event.order_id,event.actor,event.message]));
      await loadOrders(); if(selected())await loadQuote(); if(!uncertain)message('저장된 상태 조회 완료');
    } catch(error) { status=null;quote=null;message('조회 실패: '+error.message+' · 마지막 성공 자료는 최신 상태가 아닐 수 있습니다.');['us-sync','us-stop','us-resume'].forEach(id=>$(id).disabled=true); }
    finally {loading=false;$('us-refresh').disabled=false;updateGate();}
  }
  $('us-order-form').addEventListener('input',event=>{revision++;if(event.target?.id!=='us-input-sell-consent')$('us-input-sell-consent').checked=false;$('us-input-sell-label').hidden=$('us-side').value!=='sell';discardPreview();const q=Number($('us-quantity').value),price=Number($('us-price').value);$('us-notional').textContent='주문 금액: '+(q>0&&price>0?num(q*price):'—')+' USD · 수수료 별도';});
  $('us-symbol').addEventListener('change',loadQuote);
  $('us-order-form').addEventListener('submit',async event=>{event.preventDefault();if($('us-preview').disabled)return;const symbol=selected(),quantity=Number($('us-quantity').value),price=Number($('us-price').value);if(!Number.isSafeInteger(quantity)||quantity<=0||!Number.isFinite(price)||price<=0){message('양의 정수 수량과 유효한 지정가를 입력하세요.');return;}
    if($('us-side').value==='sell'&&!$('us-input-sell-consent').checked){message('현재 종목과 수량의 매도 승인을 먼저 확인하세요.');return;} const previewRevision=revision; const order={ticker:symbol.ticker,exchange:symbol.exchange,side:$('us-side').value,quantity,limit_price:$('us-price').value,request_id:crypto.randomUUID(),sell_consent:$('us-side').value==='sell'&&$('us-input-sell-consent').checked};$('us-preview').disabled=true;
    try{const result=await call('/orders/preview','POST',order);if(previewRevision!==revision){message('입력이 변경되어 이전 검토를 폐기했습니다. 다시 검토하세요.');return;}if(result.allowed!==true){message('주문 차단: '+(result.block_reasons || []).map(label).join(' · '));return;}pending=order;$('us-confirm-summary').textContent=summary(order);$('us-sell-consent').checked=false;$('us-sell-label').hidden=order.side!=='sell';$('us-submit').textContent='모의 '+label(order.side)+' 제출';$('us-submit').disabled=order.side==='sell';$('us-confirm-message').textContent='서버 검토 통과 · 제출 시 최신 조건을 다시 검증합니다.';$('us-confirm').showModal();}catch(error){message('주문 검토 실패: '+error.message);}finally{updateGate();}
  });
  $('us-sell-consent').addEventListener('change',()=>{$('us-submit').disabled=!$('us-sell-consent').checked||submitting;});
  $('us-confirm-close').addEventListener('click',()=>$('us-confirm').close());
  $('us-confirm').addEventListener('cancel',event=>{if(submitting)event.preventDefault();});
  $('us-confirm').addEventListener('close',()=>{if(!uncertain&&!submitting)pending=null;});
  $('us-submit').addEventListener('click',async()=>{if(!pending||submitting||uncertain)return;if(pending.side==='sell'&&!$('us-sell-consent').checked)return;pending.sell_consent=pending.side==='sell';
    try{sessionStorage.setItem(storageKey,JSON.stringify(pending));}catch(error){$('us-confirm-message').textContent='요청 ID를 안전하게 보관할 수 없어 제출을 중단했습니다.';return;}
    submitting=true;uncertain=true;updateGate();$('us-submit').disabled=true;$('us-confirm-close').disabled=true;$('us-confirm-message').textContent='주문 결과 확인 중입니다. 같은 주문을 다시 제출하지 마세요.';
    try{const order=await call('/orders','POST',pending);if(String(order.status).toLowerCase()==='unknown'){$('us-confirm-message').textContent='주문 결과 확인 중입니다. 같은 주문을 다시 제출하지 마세요. 요청 ID: '+pending.request_id;message('주문 결과 불명 · 예약 유지 · 동기화와 주문 목록에서 확인하세요.');}else{sessionStorage.removeItem(storageKey);pending=null;uncertain=false;$('us-confirm').close();message('주문 접수: '+label(order.status)+' · 체결 완료 여부는 주문 상세에서 확인하세요.');}await showOrder(order.id);}
    catch(error){if(error.definite){sessionStorage.removeItem(storageKey);pending=null;uncertain=false;$('us-confirm-message').textContent='제출 차단: '+error.message+' · 닫고 다시 검토하세요.';}else{$('us-confirm-message').textContent='주문 결과 확인 중입니다. 같은 주문을 다시 제출하지 마세요. 요청 ID: '+pending.request_id;message('주문 결과 불명 · 예약 유지 · 동기화와 주문 목록에서 확인하세요.');}}
    finally{submitting=false;$('us-confirm-close').disabled=false;updateGate();await refresh();}
  });
  $('us-cancel-close').addEventListener('click',()=>$('us-cancel-dialog').close());
  $('us-cancel-submit').addEventListener('click',async()=>{if(!cancelOrder)return;$('us-cancel-submit').disabled=true;try{await call('/orders/'+encodeURIComponent(cancelOrder.id)+'/cancel','POST',{});message('취소 확인 중 · 실제 잔량과 최종 취소 결과를 기다립니다.');$('us-cancel-dialog').close();await showOrder(cancelOrder.id);await refresh();}catch(error){message('취소 결과 확인 필요: '+error.message);}finally{$('us-cancel-submit').disabled=false;}});
  for(const action of ['sync','stop','resume'])$('us-'+action).addEventListener('click',async()=>{const node=$('us-'+action);node.disabled=true;try{await call('/'+({sync:'sync',stop:'entry-stop',resume:'entry-resume'}[action]),'POST',{});message(action==='sync'?'동기화 요청 접수':'신규 매수 제어 반영 · 나머지 안전 조건은 계속 적용됩니다.');await refresh();}catch(error){message('요청 실패: '+error.message);await refresh();}});
  $('us-refresh').addEventListener('click',refresh);$('us-filter').addEventListener('click',()=>loadOrders().catch(error=>message('주문 목록 조회 실패: '+error.message)));
  ['orders','positions','events'].forEach((name,index,array)=>{const tab=$('us-'+name+'-tab');tab.addEventListener('click',()=>{array.forEach(item=>{$('us-'+item+'-tab').setAttribute('aria-selected',String(item===name));$('us-'+item+'-panel').hidden=item!==name;});});tab.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const next=array[(index+(event.key==='ArrowRight'?1:2))%3];$('us-'+next+'-tab').click();$('us-'+next+'-tab').focus();}});});
  if(uncertain)message('이전 주문 결과 확인 중입니다. 같은 주문을 다시 제출하지 마세요. 요청 ID: '+(pending?.request_id || '복원 불가')+' · 주문 목록과 대조 결과를 확인하세요.');
  refresh();setInterval(refresh,30000);
})();
