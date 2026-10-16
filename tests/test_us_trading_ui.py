"""US paper operator page rendering and safe asset checks."""
from pathlib import Path
import subprocess
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def test_us_trading_page_renders_safe_manual_controls():
    """Render the page without account data or unsafe default permissions."""
    page = Environment(loader=FileSystemLoader(ROOT / "templates")).get_template("us_trading.html").render(title="미국주식 모의매매", screen="us-trading", nav_keys=[])
    assert 'id="us-order-form"' in page
    assert 'id="us-sell-consent"' in page
    assert '<dialog' in page
    assert '/static/js/us-trading.js' in page
    assert 'id="us-preview" type="submit" disabled' in page


def test_us_trading_script_syntax_and_safe_rendering():
    """Parse actual JS and reject HTML injection sinks."""
    script = ROOT / "static/js/us-trading.js"
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)
    assert "innerHTML" not in script.read_text(encoding="utf-8")


def test_us_trading_browser_flow_blocks_unknown_retries_and_requires_sell_consent():
    """Run actual UI handlers with an offline DOM and broker-free API fixtures."""
    harness = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(){this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.children=[];this.listeners={};this.style={};}
  addEventListener(name, fn){(this.listeners[name] ||= []).push(fn);}
  async emit(name, extra={}){for(const fn of this.listeners[name] || []) await fn({preventDefault(){},...extra});}
  append(...nodes){this.children.push(...nodes);}
  add(node){this.append(node);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;this.emit('close');}
  click(){if(!this.disabled)return this.emit('click');}
}
const stored = new Map(), requests=[];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
let submitFails=true, previewResolve=null, polling=null, errorCode=null, listedOrder=null, receiptStatus='pending';
function app(){
  const nodes=new Map();const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  get('us-side').value='buy';
  const status={environment:'mock',account_masked:'****1234',buy_allowed:true,entry_stopped:false,symbols:[{ticker:'AAPL',exchange:'NASD'}],block_reasons:[]};
  const context={console,Node:Element,Option:class extends Element {constructor(text,value){super();this.textContent=text;this.value=value;}},URLSearchParams,crypto:{randomUUID:()=> 'request-fixed-uuid'},setInterval(fn){polling=fn;},document:{getElementById:get,createElement:()=>new Element()},sessionStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},fetch:async(url, options)=>{
    const body=options.body?JSON.parse(options.body):null; requests.push({url,method:options.method,body});
    let result={};
    if(url.endsWith('/status'))result=status;
    else if(url.endsWith('/account'))result={cash_usd:0,buying_power_usd:null};
    else if(url.includes('/quotes?'))result={price:'100',fresh:true,sellable_quantity:2};
    else if(url.endsWith('/orders/preview')){if(previewResolve)await new Promise(resolve=>{previewResolve.resolve=resolve;});result={allowed:true};}
    else if(url.endsWith('/orders')&&options.method==='POST'){if(errorCode)return {ok:false,status:409,json:async()=>({detail:{code:errorCode,message:'주문 확인 중'}})};if(submitFails)throw new Error('network timeout');result={id:1,status:receiptStatus};}
    else if(url.includes('/orders?'))result={items:listedOrder?[listedOrder]:[]};else if(url.endsWith('/orders/1'))result={id:1,request_id:'request-fixed-uuid',ticker:'AAPL',exchange:'NASD',side:'buy',quantity:1,limit_price:'100',status:'pending',events:[]};else result={items:[]};
    return {ok:true,json:async()=>result};
  }};
  vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
  return {get};
}
(async()=>{
  const {get}=app();await tick();await tick();
  assert.equal(get('us-cash').textContent,'0');assert.equal(get('us-power').textContent,'종목 선택 후 조회');
  get('us-symbol').value='AAPL:NASD';await get('us-symbol').emit('change');
  get('us-quantity').value='2';get('us-price').value='100';get('us-side').value='sell';get('us-input-sell-consent').checked=true;
  await get('us-order-form').emit('submit');
  assert.equal(get('us-submit').disabled,true,'sell requires explicit consent');
  await get('us-submit').emit('click');assert.equal(requests.filter(r=>r.url.endsWith('/orders')&&r.method==='POST').length,0);
  get('us-sell-consent').checked=true;await get('us-sell-consent').emit('change');await get('us-submit').emit('click');
  const sent=requests.filter(r=>r.url.endsWith('/orders')&&r.method==='POST');assert.equal(sent.length,1);assert.equal(sent[0].body.sell_consent,true);assert.equal(sent[0].body.request_id,'request-fixed-uuid');
  assert.ok(stored.size,'uncertain request must survive page reload');
  await get('us-submit').emit('click');assert.equal(requests.filter(r=>r.url.endsWith('/orders')&&r.method==='POST').length,1);
  assert.equal(get('us-preview').disabled,true,'unknown result blocks a fresh request');
  const second=app();await tick();await tick();assert.equal(second.get('us-preview').disabled,true,'reload keeps unknown request blocked');
  stored.clear(); const third=app();await tick();await tick();third.get('us-symbol').value='AAPL:NASD';await third.get('us-symbol').emit('change');third.get('us-quantity').value='1';third.get('us-price').value='100';
  previewResolve={};const stale=third.get('us-order-form').emit('submit');await tick();third.get('us-quantity').value='3';await third.get('us-order-form').emit('input');previewResolve.resolve();await stale;
  assert.notEqual(third.get('us-confirm').open,true,'changed inputs invalidate an in-flight preview');
  previewResolve=null;submitFails=false;await third.get('us-order-form').emit('submit');await third.get('us-submit').emit('click');assert.equal(stored.size,0,'successful receipt resolves persisted request');assert.equal(third.get('us-detail-panel').hidden,false);assert.ok(third.get('us-detail').children[0].textContent.includes('미체결'));
  const count=requests.length;await polling();assert.ok(requests.length>count,'poll refresh reads latest persisted state');
  await third.get('us-order-form').emit('submit');errorCode='order_outcome_unknown';await third.get('us-submit').emit('click');assert.ok(stored.size,'409 unknown must retain exact request');assert.equal(third.get('us-preview').disabled,true);listedOrder={id:1,request_id:'request-fixed-uuid',status:'unknown'};await polling();assert.ok(stored.size,'persisted unknown does not resolve uncertainty');listedOrder.status='pending';await polling();assert.equal(stored.size,0,'confirmed persisted receipt resolves uncertainty');

  errorCode=null;listedOrder=null;receiptStatus='unknown';
  await third.get('us-order-form').emit('submit');await third.get('us-submit').emit('click');
  assert.ok(stored.size,'HTTP 200 unknown receipt must preserve pending request');
  assert.equal(third.get('us-preview').disabled,true,'HTTP 200 unknown cannot permit new request');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    subprocess.run(
        ["node", "-e", harness, str(ROOT / "static/js/us-trading.js")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
