"""Execute the shipped settings JavaScript with synthetic DOM/API responses."""
from pathlib import Path
import json
import subprocess

import pytest

from tests.test_fujimoto_ui import Controls


def run_ui(*, fail=False, conflict=False, confirmed=True):
    """Exercise actual rendering and PUT handling without operational settings."""
    root = Path(__file__).resolve().parents[1]
    controls = Controls()
    controls.feed((root / "templates/ops_config.html").read_text(encoding="utf-8"))
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    script = script[script.index("let executionPolicy ="):script.index("async function loadOpsConfig()")]
    harness = r"""
const fs=require('node:fs'), vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')), elements=input.elements, calls=[];
let policy={enabled:false,scope:'fujimoto_only',source:'database',version:3,environment:'mock',editable:true,
  updated_by:'operator',updated_at:null,readiness:{block_reasons:[]}};
let release; const pending=new Promise(resolve=>release=resolve);
const ctx={document:{getElementById:id=>elements[id]},Date,confirm:()=>input.confirmed,
  apiFetch:async()=>policy, _redirectToLogin:()=>{},loadOpsConfig:async()=>{},
  fetch:async(url,opts)=>{calls.push({url,...opts,body:JSON.parse(opts.body)});await pending;
    if(input.fail)throw new Error('offline');
    if(input.conflict)return {ok:false,status:409,json:async()=>({detail:'execution_policy_version_conflict'})};
    policy={...policy,enabled:calls.at(-1).body.enabled,version:policy.version+1};
    return {ok:true,status:200,json:async()=>policy};}};
vm.createContext(ctx);vm.runInContext(input.script,ctx);
(async()=>{
  await ctx.loadExecutionPolicy();
  const saving=ctx.saveExecutionPolicy(true);
  const during=JSON.parse(JSON.stringify(elements));
  await ctx.saveExecutionPolicy(true); // ignored while already saving
  release(); await saving;
  const enabled=JSON.parse(JSON.stringify(elements));
  if(!input.fail&&!input.conflict&&input.confirmed)await ctx.saveExecutionPolicy(false);
  process.stdout.write(JSON.stringify({during,enabled,final:elements,calls}));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", harness], input=json.dumps({"script": script,
        "elements": controls.elements, "fail": fail, "conflict": conflict, "confirmed": confirmed}),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=15)
    return json.loads(result.stdout)


def test_save_waits_for_server_and_prevents_duplicate_mutation():
    result = run_ui()
    assert result["during"]["execution-policy-enable"]["disabled"]
    assert result["during"]["execution-policy-disable"]["disabled"]
    assert result["enabled"]["execution-policy-state"]["textContent"] == "후지모토만 허용"
    assert result["final"]["execution-policy-state"]["textContent"] == "국내 주문 전체 차단"
    assert [c["body"] for c in result["calls"]] == [
        {"enabled": True, "scope": "fujimoto_only", "expected_version": 3},
        {"enabled": False, "scope": "fujimoto_only", "expected_version": 4}]
    assert all(c["method"] == "PUT" for c in result["calls"])


@pytest.mark.parametrize("kwargs", [{"fail": True}, {"conflict": True}])
def test_failed_save_never_claims_success_and_refreshes_authoritative_state(kwargs):
    result = run_ui(**kwargs)
    assert result["final"]["execution-policy-state"]["textContent"] == "국내 주문 전체 차단"
    assert "저장 완료를 확인하지 못했습니다" in result["final"]["execution-policy-result"]["textContent"]
    assert len(result["calls"]) == 1


def test_dismissed_confirmation_does_not_change_permission():
    result = run_ui(confirmed=False)
    assert result["calls"] == []
