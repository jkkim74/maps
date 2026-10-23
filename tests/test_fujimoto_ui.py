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
                "hidden": "hidden" in values, "open": "open" in values,
                "tagName": tag.upper(), "attributes": values,
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


def run_ui(data: dict | None, actions: list[dict] | None = None,
           *, candidates: list[dict] | None = None) -> dict:
    """Run actual inline JavaScript with only DOM/fetch replaced by offline fixtures."""
    template = (Path(__file__).resolve().parents[1] / "templates/fujimoto.html").read_text(encoding="utf-8")
    controls = Controls()
    controls.feed(template)
    script = re.search(r"<script>(.*?)</script>", template, re.S).group(1)
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),elements=input.elements,calls=[];
let data=input.data,candidates=input.candidates,fail=false,reject=false,delayReads=false,failStatus=false;
let clock=0,nextTimer=0;const timers=new Map(),pending=[],snapshots=[];
const document={hidden:false,getElementById:id=>elements[id],querySelector:()=>null,addEventListener(name,fn){this[name]=fn;}};
// Match DOM replacement semantics so stale HTML cannot masquerade as a new message.
for(const element of Object.values(elements)){
  element.querySelectorAll=()=>[];
  element.setAttribute=(name,value)=>{element.attributes[name]=value;};
  element.removeAttribute=name=>{delete element.attributes[name];};
  element.showModal=()=>{element.open=true;};
  element.close=()=>{element.open=false;};
  element.focus=()=>{element.focused=true;};
  let html='',text='';
  Object.defineProperties(element,{
    innerHTML:{enumerable:true,get:()=>html,set:value=>{html=value;text='';}},
    textContent:{enumerable:true,get:()=>text,set:value=>{text=value;html='';}}
  });
}
const fetch=async(url,options)=>{
  calls.push({path:url,method:options.method,body:options.body?JSON.parse(options.body):null});
  const mutation=options.method!=='GET';
  const response=mutation?(reject?{detail:'paper_test_binding_changed;fresh_subscribed_recording_missing'}:{}):
    (url.endsWith('/status')?data:url.includes('/candidates?')?candidates:[]);
  const failed=fail||(failStatus&&url.endsWith('/status')),ok=!(mutation&&reject);
  if(!mutation&&delayReads&&!failed)await new Promise(resolve=>pending.push(resolve));
  if(failed)throw new Error('offline status unavailable');
  return {ok,json:async()=>response};
};
vm.runInNewContext(input.script,{document,fetch,Intl,Date,
  setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,at:clock+ms});return id;},
  clearTimeout:id=>timers.delete(id)});
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
  await settle();
  for(const action of input.actions){
    if('data' in action)data=action.data;
    if(action.candidates)candidates=action.candidates;
    if(action.fail!==undefined)fail=action.fail;
    if(action.failStatus!==undefined)failStatus=action.failStatus;
    if(action.reject!==undefined)reject=action.reject;
    if(action.delayReads!==undefined)delayReads=action.delayReads;
    if(action.releaseReads)pending.splice(0).forEach(resolve=>resolve());
    if(action.hidden!==undefined){document.hidden=action.hidden;if(document.visibilitychange)document.visibilitychange();}
    if(action.tick){clock+=action.tick;const due=[...timers].filter(([,t])=>t.at<=clock);
      for(const [id,t] of due){timers.delete(id);t.fn();}}
    if(action.id){
      const element=elements[action.id];
      if(action.value!==undefined)element.value=action.value;
      if(action.checked!==undefined)element.checked=action.checked;
      if(action.open!==undefined)element.open=action.open;
      if(action.event)element[action.event]({preventDefault(){}});
    }
    await settle();
    snapshots.push(JSON.parse(JSON.stringify({elements,calls,pending:pending.length,timers:timers.size})));
  }
  process.stdout.write(JSON.stringify({elements,calls,snapshots}));
})().catch(error=>{process.stderr.write(String(error.stack));process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", harness], input=json.dumps({"script": script,
        "elements": controls.elements, "data": data, "actions": actions or [],
        "candidates": candidates or []}),
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
    assert "예산 저장" in blocked["elements"]["fj-preparation"]["innerHTML"]
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
        {"id": "fj-activate", "event": "onsubmit"}, {"id": "fj-stop", "event": "onclick"},
        {"id": "fj-stop-commit", "event": "onclick"}])
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
    data = status(state=state, reasons=["paper_test_already_authorized"])
    data["block_reasons"] = ["paper_test_binding_changed", "recording_capacity_exhausted"]
    result = run_ui(data)
    elements = result["elements"]
    assert label in elements["fj-trial-state"]["innerHTML"]
    assert "2026. 10. 12. 09:00 KST" in elements["fj-trial-state"]["innerHTML"]
    assert "2026. 11. 6. 15:20 KST" in elements["fj-trial-state"]["innerHTML"]
    assert "설정·코드가 변경" in elements["fj-preparation"]["innerHTML"]
    assert "기록 용량" in elements["fj-preparation"]["innerHTML"]
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
    assert 'data-label="누적 체결"' in orders
    orders = re.sub(r'<td data-label="[^"]*">', '<td>', orders)
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


@pytest.mark.parametrize("budget,reasons,title,action", [
    (None, ["paper_test_dedicated_budget_required"], "예산·설정", "#fj-budget-details"),
    (5000000, ["usable_screen_candidate_source_missing"], "시작 전 확인", "/batch-monitor"),
    (5000000, ["fujimoto_runtime_disabled"], "시작 전 확인", "/ops-config"),
    (5000000, ["fresh_subscribed_recording_missing"], "시작 전 확인", "#fj-source-details"),
    (5000000, ["unexpected_<reason>"], "시작 전 확인", "#fj-records"),
])
def test_next_action_distinguishes_budget_data_operator_and_unknown_blocks(budget, reasons, title, action):
    data = status(budget=budget, reasons=reasons)
    if reasons == ["usable_screen_candidate_source_missing"]:
        data["screen"] = {"ref_date": "2026-10-21", "screened": 3, "selected": 0}
    elements = run_ui(data)["elements"]
    assert title in elements["fj-next-title"]["textContent"]
    assert elements["fj-next-link"]["href"] == action
    assert elements["fj-trial-start"]["disabled"] is True
    if budget:
        saved = elements["fj-saved-budget"]["textContent"]
        assert "10,000,000원" in saved and "5,000,000원" in saved
    if data["screen"]:
        assert "최근 선별 통과: 0종목" in elements["fj-screen-summary"]["textContent"]


def test_candidate_presence_does_not_override_operator_blocks_and_codes_are_translated_and_escaped():
    candidates = [{"ticker": "<005930>", "payload": {"ref_date": "2026-10-21",
        "rule": {"selection_passed": False, "blocking_reasons": ["incomplete_sector_valuation", "annual_growth_failed", "<unknown>"]}}}]
    elements = run_ui(status(budget=5000000, reasons=["trading_disabled"]), candidates=candidates)["elements"]
    assert elements["fj-next-link"]["href"] == "/ops-config"
    rendered = elements["fj-candidates"]["innerHTML"]
    assert "미통과" in rendered and "업종 PER" in rendered and "연간 성장" in rendered
    assert "&lt;005930&gt;" in rendered and "&lt;unknown&gt;" in rendered
    assert "확인 필요" in rendered


@pytest.mark.parametrize("reason,title", [("trading_disabled", "시작 전 확인"),
    ("fresh_subscribed_recording_missing", "시작 전 확인")])
def test_passing_candidate_still_requires_server_readiness(reason, title):
    candidate = {"ticker": "005930", "payload": {"ref_date": "2026-10-21",
        "rule": {"selection_passed": True, "blocking_reasons": []}}}
    elements = run_ui(status(budget=5000000, reasons=[reason]), candidates=[candidate])["elements"]
    assert title in elements["fj-next-title"]["textContent"]
    assert "<td>통과</td>" in elements["fj-candidates"]["innerHTML"]
    assert elements["fj-trial-start"]["disabled"] is True


@pytest.mark.parametrize("state,title", [("scheduled", "시작 예약"), ("active", "진행 중"),
    ("stopped", "신규 진입 중지"), ("expired", "기간 만료")])
def test_campaign_next_action_monitors_existing_authorization(state, title):
    data = status(budget=5000000, state=state, reasons=["paper_test_binding_changed"])
    data["control"]["stop_reason"] = "paper_test_binding_changed"
    elements = run_ui(data)["elements"]
    assert "운영 중 확인" in elements["fj-next-title"]["textContent"]
    assert title in elements["fj-trial-state"]["innerHTML"]
    assert "진행 확인" in elements["fj-next-action"]["textContent"]
    assert "설정·코드가 변경" in elements["fj-preparation"]["innerHTML"]
    assert elements["fj-trial-start"]["disabled"] is True


def test_ready_guidance_tracks_explicit_consent_and_null_status_fails_closed():
    ready = status(budget=5000000, eligible=True)
    unchecked = run_ui(ready)["elements"]
    assert "시작을 예약" in unchecked["fj-next-title"]["textContent"]
    assert "동의" in unchecked["fj-next-action"]["textContent"]
    failed = run_ui(ready, [{"id": "fj-trial-consent", "checked": True, "event": "onchange"},
        {"data": None, "id": "fj-refresh", "event": "onclick"}])["elements"]
    assert "조회 실패" in failed["fj-next-title"]["textContent"]
    assert "준비 완료" not in failed["fj-next-action"]["textContent"]
    assert failed["fj-trial-start"]["disabled"] is True
    assert "최근 성공" in failed["fj-refresh-state"]["textContent"]


def test_polling_is_get_only_preserves_drafts_and_skips_hidden_page():
    result = run_ui(status(budget=5000000, eligible=True), [
        {"id": "fj-budget", "value": 9000000, "event": "oninput"},
        {"id": "fj-book", "checked": True, "event": "onchange"},
        {"id": "fj-deposit", "value": -1000000, "event": "oninput"},
        {"id": "fj-trial-consent", "checked": True, "event": "onchange"},
        {"tick": 14999}, {"tick": 1}, {"hidden": True, "tick": 15000},
        {"hidden": False},
    ])
    snapshots = result["snapshots"]
    assert len(snapshots[4]["calls"]) == 4
    assert len(snapshots[5]["calls"]) == 8
    assert len(snapshots[6]["calls"]) == 8
    assert len(result["calls"]) == 12
    assert all(call["method"] == "GET" for call in result["calls"])
    elements = result["elements"]
    assert elements["fj-budget"]["value"] == 9000000
    assert elements["fj-book"]["checked"] is True
    assert elements["fj-deposit"]["value"] == -1000000
    assert elements["fj-trial-consent"]["checked"] is True
    assert "최근 성공" in elements["fj-refresh-state"]["textContent"]


def test_pending_poll_does_not_overlap_and_pre_mutation_read_cannot_restore_readiness():
    ready = status(budget=5000000, eligible=True)
    result = run_ui(ready, [{"delayReads": True, "tick": 15000},
        {"tick": 30000, "id": "fj-refresh", "event": "onclick"},
        {"data": status(budget=5000000, reasons=["paper_test_binding_changed"]),
         "id": "fj-stop-commit", "event": "onclick"},
        {"releaseReads": True},
        {"delayReads": False, "releaseReads": True},
        {"id": "fj-trial-consent", "checked": True, "event": "onchange"},
    ])
    snapshots = result["snapshots"]
    assert len(snapshots[0]["calls"]) == 8
    assert len(snapshots[1]["calls"]) == 8
    assert "확인 중" in snapshots[0]["elements"]["fj-refresh-state"]["textContent"]
    assert "시작을 예약할 수" not in snapshots[3]["elements"]["fj-next-title"]["textContent"]
    assert snapshots[3]["elements"]["fj-trial-start"]["disabled"] is True
    assert len(result["calls"]) == 13  # initial + one pending GET batch + stop + one fresh batch
    assert result["elements"]["fj-trial-start"]["disabled"] is True
    assert "시작을 예약할 수" not in result["elements"]["fj-next-title"]["textContent"]
    assert "설정·코드가 변경" in result["elements"]["fj-preparation"]["innerHTML"]


def test_read_error_replaces_ready_guidance_and_later_success_is_required():
    ready = status(budget=5000000, eligible=True)
    result = run_ui(ready, [{"id": "fj-trial-consent", "checked": True, "event": "onchange"},
        {"fail": True, "tick": 15000}, {"id": "fj-trial", "event": "onsubmit"},
        {"fail": False, "tick": 15000}])
    failed = result["snapshots"][1]["elements"]
    assert failed["fj-trial-start"]["disabled"] is True
    assert "조회 실패" in failed["fj-next-title"]["textContent"]
    assert "확인할 수 없" in failed["fj-status"]["textContent"]
    assert all(call["method"] == "GET" for call in result["calls"])
    assert result["elements"]["fj-trial-start"]["disabled"] is False
    assert result["elements"]["fj-message"]["textContent"] == ""


def test_failed_status_disables_approval_while_other_gets_are_still_pending():
    result = run_ui(status(budget=5000000, eligible=True), [
        {"id": "fj-trial-consent", "checked": True, "event": "onchange"},
        {"delayReads": True, "failStatus": True, "tick": 15000},
        {"tick": 15000, "id": "fj-trial", "event": "onsubmit"},
    ])
    failed = result["snapshots"][1]
    assert failed["pending"] == 3
    assert "조회 실패" in failed["elements"]["fj-next-title"]["textContent"]
    assert failed["elements"]["fj-trial-start"]["disabled"] is True
    assert len(result["calls"]) == 8
    assert all(call["method"] == "GET" for call in result["calls"])


def test_missing_optional_status_and_candidate_data_remain_explicit():
    data = status(budget=5000000)
    data.update(paper_test=None, feed_quality=None, storage_limits=None)
    elements = run_ui(data, candidates=[{"ticker": "005930", "payload": None}])["elements"]
    assert "확인 필요" in elements["fj-next-title"]["textContent"]
    assert "기록 없음" in elements["fj-trial-state"]["innerHTML"]
    assert "확인 필요" in elements["fj-candidates"]["innerHTML"]
    assert elements["fj-trial-start"]["disabled"] is True


def test_edits_during_post_write_reload_are_preserved_and_consent_is_cleared():
    result = run_ui(status(budget=5000000, eligible=True), [
        {"id": "fj-trial-consent", "checked": True, "event": "onchange"},
        {"delayReads": True, "id": "fj-config", "event": "onsubmit"},
        {"id": "fj-budget", "value": 8000000, "event": "oninput"},
        {"id": "fj-book", "checked": True, "event": "onchange"},
        {"id": "fj-deposit", "value": -2000000, "event": "oninput"},
        {"delayReads": False, "releaseReads": True},
    ])
    elements = result["elements"]
    assert elements["fj-budget"]["value"] == 8000000
    assert elements["fj-book"]["checked"] is True
    assert elements["fj-deposit"]["value"] == -2000000
    assert elements["fj-trial-consent"]["checked"] is False


def test_saved_budget_and_advanced_details_stay_collapsed_and_manual_open_survives_poll():
    data = status(budget=5000000, reasons=["trading_disabled"])
    elements = run_ui(data)["elements"]
    assert elements["fj-budget-details"]["open"] is False
    assert elements["fj-advanced"]["open"] is False
    assert elements["fj-step-approval"]["hidden"] is True
    result = run_ui(data, [{"id": "fj-edit-budget", "event": "onclick"},
        {"id": "fj-source-details", "open": True}, {"tick": 15000}])
    assert result["elements"]["fj-budget-details"]["open"] is True
    assert result["elements"]["fj-source-details"]["open"] is True
    assert result["elements"]["fj-budget"]["focused"] is True
    assert all(call["method"] == "GET" for call in result["calls"])


def test_stop_dialog_cancel_never_mutates_and_confirm_stops_once():
    result = run_ui(status(budget=5000000, state="active"), [
        {"id": "fj-stop", "event": "onclick"},
        {"id": "fj-stop-cancel", "event": "onclick"},
        {"id": "fj-stop", "event": "onclick"},
        {"id": "fj-stop-commit", "event": "onclick"}])
    assert result["snapshots"][0]["elements"]["fj-stop-dialog"]["open"] is True
    assert result["snapshots"][1]["elements"]["fj-stop-dialog"]["open"] is False
    assert all(call["method"] == "GET" for call in result["snapshots"][2]["calls"])
    assert result["elements"]["fj-stop-dialog"]["open"] is False
    assert [call["path"] for call in result["calls"] if call["method"] != "GET"] == [
        "/api/v1/fujimoto/stop"]


def test_duplicate_blocks_appear_once_with_remedies_and_correct_navigation():
    data = status(budget=5000000, reasons=["trading_disabled", "fresh_subscribed_recording_missing",
        "actual_order_costs_unresolved", "account_unknown_order"])
    data["block_reasons"] = data["paper_test"]["block_reasons"] * 2
    elements = run_ui(data)["elements"]
    issues = elements["fj-preparation"]["innerHTML"]
    assert issues.count('class="fj-issue"') == 4
    assert issues.count("주문 실행이 차단되어") == 1
    assert 'href="/risk#paper-costs"' in issues
    assert 'href="/risk"' in issues
    assert 'href="/ops-config"' in issues
    assert "재기동 없이 변경" in issues
    assert "변경은 별도 서버 운영 절차" not in issues
    assert 'href="#fj-source-details"' in issues
    assert "주문 실행이 차단되어" not in elements["fj-next-action"]["textContent"]


def test_equivalent_budget_and_screen_blocks_are_collapsed():
    data = status(reasons=["paper_test_dedicated_budget_required", "screening_coverage_missing_or_stale"])
    data["block_reasons"] = ["dedicated_budget_required", "screening_coverage_missing"]
    elements = run_ui(data)["elements"]
    assert elements["fj-preparation"]["innerHTML"].count('class="fj-issue"') == 2
    assert elements["fj-budget-details"]["open"] is True


@pytest.mark.parametrize("state", ["scheduled", "active", "stopped", "expired"])
def test_existing_campaign_does_not_treat_new_approval_ineligibility_as_an_incident(state):
    data = status(budget=5000000, state=state, reasons=["paper_test_already_authorized",
        "paper_test_existing_ownership_or_orders"])
    elements = run_ui(data)["elements"]
    assert elements["fj-step-readiness"]["hidden"] is True
    assert elements["fj-step-monitor"]["hidden"] is False
    assert elements["fj-step-approval"]["hidden"] is True
    assert "운영 중 확인할 항목" not in elements["fj-next-title"]["textContent"]
    data["block_reasons"] = ["account_unknown_order"]
    elements = run_ui(data)["elements"]
    assert elements["fj-step-readiness"]["hidden"] is False
    assert "계좌 주문 결과" in elements["fj-preparation"]["innerHTML"]
    assert elements["fj-next-link"]["href"] == "/risk"


@pytest.mark.parametrize("environment,label", [("paper", "모의"), ("live", "실거래")])
def test_validated_operation_is_distinct_from_paper_trial(environment, label):
    data = status(budget=5000000, reasons=["paper_environment_required"])
    data["control"].update(authorization_policy="validated", sell_consent=True,
        execution_mode=environment, entries_enabled=True)
    elements = run_ui(data)["elements"]
    assert "측정 검증 기반 운영" in elements["fj-next-title"]["textContent"]
    assert label in elements["fj-environment"]["textContent"]
    assert elements["fj-step-monitor"]["hidden"] is False
    assert elements["fj-step-approval"]["hidden"] is True
    assert "2026." not in elements["fj-trial-state"]["innerHTML"]
    assert "모의 체험 진행 중" not in elements["fj-next-title"]["textContent"]


def test_unknown_holdings_orders_and_dates_do_not_become_zero_or_estimated():
    data = status(budget=5000000, state="active")
    data["modes"][0]["cycles"] = None
    data["paper_test"].update(starts_at=None, expires_at=None)
    elements = run_ui(data)["elements"]
    assert "0종목" not in elements["fj-metrics"]["innerHTML"]
    assert "0건" not in elements["fj-metrics"]["innerHTML"]
    assert "보유 자료 확인 필요" in elements["fj-cycles"]["innerHTML"]
    assert "주문 자료 확인 필요" in elements["fj-orders"]["innerHTML"]
    assert elements["fj-trial-state"]["innerHTML"].count("기록 없음 · 확인 필요") == 2


def test_partial_and_unknown_orders_do_not_advance_buy_stage_or_hide_reservations():
    data = status(budget=5000000, state="active")
    data["modes"][0]["cycles"] = [{"id": 1, "ticker": "005930", "mode": "safe",
        "state": {"buy_stage": 0, "quantity": 3, "pending_order": True},
        "orders": [{"id": 9, "status": "UNKNOWN", "side": "buy", "quantity": 8,
            "filled_quantity": 3, "remaining_quantity": 5, "reserved_cash": 5000,
            "reserved_quantity": 0}], "accounting": "provisional_costs_missing"}]
    elements = run_ui(data)["elements"]
    assert 'data-done="true"' not in elements["fj-cycles"]["innerHTML"]
    assert "1차 미완료" in elements["fj-cycles"]["innerHTML"]
    assert "결과 불명" in elements["fj-orders"]["innerHTML"]
    assert "5,000원" in elements["fj-orders"]["innerHTML"]
    assert "후지모토 주문 결과" in elements["fj-preparation"]["innerHTML"]
    data["paper_test"]["state"] = "not_started"
    assert "체험 진행 중" not in run_ui(data)["elements"]["fj-next-title"]["textContent"]


@pytest.mark.parametrize("invalid", ["empty_modes", "duplicate_modes", "invalid_budget", "unknown_trial"])
def test_invalid_status_never_enables_trial_approval(invalid):
    data = status(budget=5000000, eligible=True)
    if invalid == "empty_modes":
        data["modes"] = []
    elif invalid == "duplicate_modes":
        data["modes"][1]["mode"] = "safe"
    elif invalid == "invalid_budget":
        data["modes"][0]["budget"] = "unknown"
    else:
        data["paper_test"]["state"] = "unknown"
    elements = run_ui(data, [{"id": "fj-trial-consent", "checked": True, "event": "onchange"}])["elements"]
    assert elements["fj-trial-start"]["disabled"] is True
    assert elements["fj-step-approval"]["hidden"] is True


def test_naive_utc_approval_timestamp_is_displayed_in_kst():
    data = status(budget=5000000, state="active")
    data["paper_test"]["activated_at"] = "2026-10-08T02:00:00"
    assert "2026. 10. 8. 11:00 KST" in run_ui(data)["elements"]["fj-trial-state"]["innerHTML"]


def test_missing_cycle_order_state_and_money_are_explicitly_unknown():
    data = status(budget=5000000, state="active")
    data["modes"][0]["cycles"] = [{"id": 1, "ticker": "005930", "mode": "safe",
        "state": {"buy_stage": 0}, "orders": None, "cost_basis": None, "realized_pnl": None}]
    elements = run_ui(data)["elements"]
    assert "등록된 미완료 주문 없음" not in elements["fj-cycles"]["innerHTML"]
    assert "미해결 주문 확인 필요" in elements["fj-cycles"]["innerHTML"]
    assert "0원" not in elements["fj-cycles"]["innerHTML"]
    assert "확인 필요" in elements["fj-cycles"]["innerHTML"]
