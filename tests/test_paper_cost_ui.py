"""Run the shipped settlement UI offline with a minimal native DOM."""
import json
from html.parser import HTMLParser
from pathlib import Path
import subprocess


class ControlIds(HTMLParser):
    """Verify the template supplies every control used by shipped JavaScript."""

    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.add(values['id'])


def test_template_ships_script_and_native_controls():
    """All settlement controls exist and no cost amount is prefilled."""
    template = (Path(__file__).resolve().parents[1] / 'templates/risk.html').read_text(encoding='utf-8')
    controls = ControlIds()
    controls.feed(template)
    assert {f'pc-{name}' for name in ['refresh','summary','details','amount','source','hash','reason','orders','reviewed','preview','commit','result','order-only']} <= controls.ids
    assert 'src="/static/js/paper_costs.js"' in template


def test_statement_preview_commit_and_fail_closed():
    """Require real evidence, invalidate edited previews, and preserve receipt state."""
    script = Path(__file__).resolve().parents[1] / 'static/js/paper_costs.js'
    harness = r'''
const assert=require('node:assert/strict');
const elements={};
function el(){return {value:'',checked:false,disabled:false,textContent:'',children:[],appendChild(x){this.children.push(x)},replaceChildren(){this.children=[]}}}
const document={getElementById(id){return elements[id] ||= el()},createElement(){return el()}};
let fail=false,calls=[];
const costs={supported:true,version:'v1',breakdown:{available:true,period_start:'2026-10-01',period_end:'2026-10-12',residual:'280',confirmed_adjustments:[{id:1,kind:'fee',amount:'80'}],settlements:[{id:2,deduction:'100'}],trades:[{broker_order_id:'order-1',ticker:'005930',side:'sell',qty:1,notional:'1000'}]},pending_adjustments:[{id:3,amount:'120'}],cost_coverage:{complete:false,reason:'missing'}};
costs.order_cost_candidates=[{broker_order_id:'old-order',ticker:'000660',side:'sell',qty:2,notional:'2000',account_evidence_id:77,period_start:'2026-09-01',period_end:'2026-09-02'}];
costs.mode='observe';costs.cost_coverage.reason='paper_costs_coverage_missing';
const fetch=async(path,options={})=>{calls.push({path,body:options.body&&JSON.parse(options.body)});if(fail)throw Error('offline');return {ok:true,json:async()=>path.endsWith('/preview')?{can_commit:true,fingerprint:'fp',version:'v1',deduction:'30000',remaining_residual:'792'}:path.endsWith('/commit')?{status:'awaiting_reconciliation'}:{paper_costs:costs}}};
require('node:vm').runInNewContext(require('node:fs').readFileSync(process.argv[1],'utf8'),{document,fetch,crypto:require('node:crypto').webcrypto,URL,console});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
(async()=>{
await settle();assert.equal(elements['pc-amount'].value,'');assert.equal(elements['pc-commit'].disabled,true);
assert.match(elements['pc-summary'].textContent,/관찰/);assert.match(elements['pc-summary'].textContent,/구간.*근거/);
assert.doesNotMatch(elements['pc-orders'].children[0].children[0].textContent,/확인 불가/);
await elements['pc-preview'].onclick();assert.equal(calls.filter(c=>c.body).length,0);
for(const [id,value] of [['pc-amount','31772'],['pc-source','https://broker.example/statement'],['pc-hash','a'.repeat(64)],['pc-reason','Broker statement reviewed']])elements[id].value=value;
elements['pc-reviewed'].checked=true;await elements['pc-preview'].onclick();await settle();
let request=calls.find(c=>c.path.endsWith('/preview')).body;assert.equal(request.items[0].amount,'31772');assert.deepEqual(request.items[0].included_adjustment_ids,[1]);assert.deepEqual(request.items[0].included_settlement_ids,[2]);assert.equal(elements['pc-commit'].disabled,false);
elements['pc-amount'].oninput();assert.equal(elements['pc-commit'].disabled,true);
const fields=elements['pc-orders'].children[0].children.slice(1).map(label=>label.children[0]);
assert.equal(fields[2].value,'');assert.equal(fields[3].value,'');fields[0].checked=true;
await elements['pc-preview'].onclick();await settle();assert.equal(elements['pc-commit'].disabled,true);
for(const [i,v] of [[1,'1000'],[2,'0.1'],[3,'0.2'],[4,'https://broker.example/order'],[5,'b'.repeat(64)]])fields[i].value=v;
await elements['pc-preview'].onclick();await settle();
assert.equal(calls.filter(c=>c.path.endsWith('/preview')).at(-1).body.items[1].amount,'0.3');
elements['pc-amount'].value='0';await elements['pc-preview'].onclick();await settle();assert.equal(calls.filter(c=>c.path.endsWith('/preview')).at(-1).body.items[0].amount,'0');
elements['pc-order-only'].checked=true;elements['pc-order-only'].onchange();assert.equal(elements['pc-commit'].disabled,true);
elements['pc-amount'].value='';elements['pc-source'].value='';elements['pc-hash'].value='';
const before=calls.length;await elements['pc-preview'].onclick();await settle();assert.equal(calls.length,before);
const historical=elements['pc-orders'].children[0].children.slice(1).map(label=>label.children[0]);
historical[0].checked=true;for(const [i,v] of [[1,'2000'],[2,'3'],[3,'2'],[4,'https://broker.example/historical'],[5,'c'.repeat(64)]])historical[i].value=v;
await elements['pc-preview'].onclick();await settle();const orderOnly=calls.filter(c=>c.path.endsWith('/preview')).at(-1).body;
assert.equal(orderOnly.items.length,1);assert.equal(orderOnly.items[0].scope,'order');assert.equal(orderOnly.items[0].account_evidence_id,77);assert.equal(orderOnly.items[0].period_start,'2026-09-01');assert.equal(orderOnly.items[0].broker_order_id,'old-order');
await elements['pc-preview'].onclick();await settle();await elements['pc-commit'].onclick();await settle();
assert.match(elements['pc-result'].textContent,/awaiting_reconciliation/);assert.ok(calls.find(c=>c.path.endsWith('/commit')).body.idempotency_key);
fail=true;await elements['pc-refresh'].onclick();await settle();assert.equal(elements['pc-preview'].disabled,true);assert.equal(elements['pc-commit'].disabled,true);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness, str(script)], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
