"""Execute the shipped operator script offline against its public JSON contract."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess

import pytest


class Controls(HTMLParser):
    """Collect native controls without a browser or additional dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, dict] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.elements[values["id"]] = {
                "value": values.get("value", ""), "checked": "checked" in values,
                "disabled": "disabled" in values, "required": "required" in values,
                "textContent": "", "innerHTML": "", "style": {},
            }


def status(*, budget: int | None = None, eligible: bool = False,
           reasons: list[str] | None = None, state: str = "not_started") -> dict:
    """Representative API data; readiness and all quantities stay server supplied."""
    trial = state != "not_started"
    return {
        "control": {"execution_mode": "paper" if trial else "observe", "entries_enabled": trial},
        "block_reasons": [], "screen": None, "feed_quality": {"reason": "no_recorded_feed"},
        "storage_limits": {"quote_rows": 100000, "candidate_rows": 10000},
        "unresolved_cost_orders": [],
        "paper_test": {"eligible": eligible, "block_reasons": reasons or [], "state": state,
            "authorization_policy": "paper_test" if trial else "validated",
            "authorization_id": "approval" if trial else None,
            "activated_at": "2026-10-08T02:00:00+00:00" if trial else None,
            "starts_at": "2026-10-12T09:00:00+09:00" if trial else None,
            "expires_at": "2026-11-06T15:20:00+09:00" if trial else None},
        "modes": [{"mode": mode, "budget": budget, "settings": {"with_orderbook": False},
            "cash": budget, "reserved_cash": 0, "cycles": []} for mode in ("safe", "original")],
    }


def run_ui(data: dict, actions: list[dict] | None = None) -> dict:
    """Run actual inline JavaScript with only DOM/fetch replaced by offline fixtures."""
    template = (Path(__file__).resolve().parents[1] / "templates/fujimoto.html").read_text(encoding="utf-8")
    controls = Controls()
    controls.feed(template)
    script = re.search(r"<script>(.*?)</script>", template, re.S).group(1)
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),elements=input.elements,calls=[];
let data=input.data,fail=false,reject=false;
const fetch=async(url,options)=>{
  calls.push({path:url,method:options.method,body:options.body?JSON.parse(options.body):null});
  if(fail)throw new Error('offline status unavailable');
  const mutation=options.method!=='GET';
  return {ok:!(mutation&&reject),json:async()=>mutation?
    (reject?{detail:'paper_test_binding_changed;fresh_subscribed_recording_missing'}:{}):
    (url.endsWith('/status')?data:[])};
};
vm.runInNewContext(input.script,{document:{getElementById:id=>elements[id]},fetch,Intl,Date});
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
  await settle();
  for(const action of input.actions){
    if(action.data)data=action.data;
    if(action.fail!==undefined)fail=action.fail;
    if(action.reject!==undefined)reject=action.reject;
    if(action.id){
      const element=elements[action.id];
      if(action.value!==undefined)element.value=action.value;
      if(action.checked!==undefined)element.checked=action.checked;
      if(action.event)element[action.event]({preventDefault(){}});
    }
    await settle();
  }
  process.stdout.write(JSON.stringify({elements,calls}));
})().catch(error=>{process.stderr.write(String(error.stack));process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", harness], input=json.dumps({"script": script,
        "elements": controls.elements, "data": data, "actions": actions or []}),
        text=True, capture_output=True, encoding="utf-8", check=True, timeout=15)
    assert not result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("budget,expected", [(None, 10000000), (500000, 1000000)])
def test_budget_recommendation_never_writes_and_refresh_preserves_draft(budget, expected):
    initial = run_ui(status(budget=budget))
    assert int(initial["elements"]["fj-budget"]["value"]) == expected
    assert initial["elements"]["fj-book"]["checked"] is False
    assert all(call["method"] == "GET" for call in initial["calls"])
    draft = run_ui(status(budget=budget), [{"id": "fj-budget", "value": 9000000},
        {"id": "fj-book", "checked": True}, {"id": "fj-refresh", "event": "onclick"}])
    assert draft["elements"]["fj-budget"]["value"] == 9000000
    assert draft["elements"]["fj-book"]["checked"] is True
    assert all(call["method"] == "GET" for call in draft["calls"])


def test_trial_requires_current_readiness_and_consent_and_uses_only_trial_endpoint():
    ready = status(budget=5000000, eligible=True)
    assert run_ui(ready)["elements"]["fj-trial-start"]["disabled"] is True
    checked = [{"id": "fj-trial-consent", "checked": True, "event": "onchange"}]
    assert run_ui(ready, checked)["elements"]["fj-trial-start"]["disabled"] is False
    blocked = run_ui(status(reasons=["paper_test_dedicated_budget_required"]), checked + [
        {"id": "fj-trial", "event": "onsubmit"}])
    assert blocked["elements"]["fj-trial-start"]["disabled"] is True
    assert "예산 저장" in blocked["elements"]["fj-trial-readiness"]["innerHTML"]
    assert all(call["method"] == "GET" for call in blocked["calls"])
    approved = run_ui(ready, checked + [{"id": "fj-trial", "event": "onsubmit"}])
    mutations = [call for call in approved["calls"] if call["method"] != "GET"]
    assert mutations == [{"path": "/api/v1/fujimoto/activate-paper-test", "method": "POST",
        "body": {"sell_consent": True}}]
    assert approved["elements"]["fj-trial-consent"]["checked"] is False


def test_explicit_budget_measured_activation_and_stop_keep_their_separate_contracts():
    result = run_ui(status(), [{"id": "fj-budget", "value": 10000000},
        {"id": "fj-config", "event": "onsubmit"}, {"id": "fj-mode", "value": "paper"},
        {"id": "fj-replay", "value": 17}, {"id": "fj-consent", "checked": True},
        {"id": "fj-activate", "event": "onsubmit"}, {"id": "fj-stop", "event": "onclick"}])
    assert [call for call in result["calls"] if call["method"] != "GET"] == [
        {"path": "/api/v1/fujimoto/config", "method": "PUT",
         "body": {"budget": 10000000, "deposit": 0, "with_orderbook": False}},
        {"path": "/api/v1/fujimoto/activate", "method": "POST",
         "body": {"execution_mode": "paper", "replay_id": 17, "sell_consent": True}},
        {"path": "/api/v1/fujimoto/stop", "method": "POST", "body": None},
    ]


@pytest.mark.parametrize("state,label", [("scheduled", "시작 예약"), ("active", "진행 중"),
    ("stopped", "신규 진입 중지"), ("expired", "기간 만료")])
def test_server_campaign_dates_states_and_changed_permission_are_visible(state, label):
    result = run_ui(status(state=state, reasons=["paper_test_binding_changed",
        "paper_test_recording_capacity_exhausted", "paper_test_already_authorized"]))
    elements = result["elements"]
    assert label in elements["fj-trial-state"]["innerHTML"]
    assert "2026. 10. 12. 09:00 KST" in elements["fj-trial-state"]["innerHTML"]
    assert "2026. 11. 6. 15:20 KST" in elements["fj-trial-state"]["innerHTML"]
    assert "설정·코드가 변경" in elements["fj-trial-readiness"]["innerHTML"]
    assert "기록 용량" in elements["fj-trial-readiness"]["innerHTML"]
    assert elements["fj-trial-start"]["disabled"] is True


def test_cumulative_orders_show_server_remainder_and_reservations_without_inference():
    data = status(state="active")
    data["modes"][0]["cycles"] = [{"id": 9, "mode": "safe", "ticker": "005930",
        "state": {"buy_stage": 1, "quantity": 3, "pending_order": True},
        "cost_basis": 3000, "realized_pnl": 0, "accounting": "provisional_costs_missing",
        "orders": [{"id": 42, "side": "buy", "status": "CANCEL_REQUESTED",
            "quantity": 8, "filled_quantity": 3, "remaining_quantity": 5,
            "reserved_cash": 5020, "reserved_quantity": 0},
            {"id": 43, "side": "sell", "status": "CANCELLED", "quantity": 3,
             "filled_quantity": 1, "remaining_quantity": 2, "reserved_cash": 0,
             "reserved_quantity": 0}]}]
    elements = run_ui(data)["elements"]
    assert "비용 미확정" in elements["fj-cycles"]["innerHTML"]
    orders = elements["fj-orders"]["innerHTML"]
    assert "취소 요청 · 확인 대기" in orders and "취소 확인" in orders
    assert "<td>8</td><td>3</td><td>5</td><td>5,020원</td><td>0</td>" in orders
    assert "<td>3</td><td>1</td><td>2</td><td>0원</td><td>0</td>" in orders


def test_read_failure_and_api_rejection_remove_start_permission_and_explain_reasons():
    ready = status(budget=5000000, eligible=True)
    checked = [{"id": "fj-trial-consent", "checked": True, "event": "onchange"}]
    failed = run_ui(ready, checked + [{"fail": True, "id": "fj-refresh", "event": "onclick"},
        {"id": "fj-trial", "event": "onsubmit"}])
    assert failed["elements"]["fj-trial-start"]["disabled"] is True
    assert "상태 조회에 실패" in failed["elements"]["fj-trial-readiness"]["textContent"]
    assert all(call["method"] == "GET" for call in failed["calls"])
    rejected = run_ui(ready, checked + [{"reject": True, "id": "fj-trial", "event": "onsubmit"}])
    assert rejected["elements"]["fj-trial-start"]["disabled"] is True
    assert "설정·코드가 변경" in rejected["elements"]["fj-message"]["textContent"]
    assert "최근 3초" in rejected["elements"]["fj-message"]["textContent"]
