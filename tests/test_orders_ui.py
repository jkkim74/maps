"""예정 주문 화면이 유동성 축소·차단을 드러내는지 검사한다."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess

import pytest


def run_orders_ui(payload: dict) -> dict:
    """Execute shipped screen JavaScript with a synthetic read-only API and DOM."""
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),elements={},calls=[];
const document={getElementById(id){return elements[id]??=( {innerHTML:''});},addEventListener(){}};
const fetch=async path=>{calls.push(path);return {ok:true,status:200,json:async()=>path.endsWith('/preview')?{data_available:false}:input.payload};};
const context=vm.createContext({document,fetch,console});
vm.runInContext(input.script,context);
vm.runInContext('loadOrders()',context).then(()=>process.stdout.write(JSON.stringify({elements,calls})));
"""
    result = subprocess.run(["node", "-e", harness], input=json.dumps({
        "payload": payload, "script": Path("static/js/app.js").read_text(encoding="utf-8")}),
        text=True, encoding="utf-8", capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("state,label", [("blocked", "차단"), ("unknown", "확인 필요"), ("ready", "준비됨")])
def test_readiness_and_truthful_basis_are_visible(state, label) -> None:
    """Account state, actual reasons, sample absence and strategy scope reach the DOM."""
    payload = dict(execution_state=state, auto_order_active=state == "ready",
                   block_reasons=[] if state == "ready" else ["trading_disabled", "new_<reason>"],
                   checked_at="2026-10-22T03:00:00+00:00", environment="mock",
                   strategy_blocks={"blocked_<strategy>": ["strategy_kill_switch", "daily_loss"]},
                   pending=[], fills_today=[], expired=[], slippage=dict(actual=None,
                   sample_count=0, mid_small_assumed=.0015, large_cap_assumed=.0005))
    result = run_orders_ui(payload)
    html = result["elements"]["orders-kpi"]["innerHTML"]
    assert label in html
    if state != "ready":
        assert "전역 주문 OFF" in html
        assert "new_&lt;reason&gt;" in html
    assert "blocked_&lt;strategy&gt;" in html
    assert "표본 0건" in html
    assert "오늘 생성 주문의 누적 체결" in html
    assert "실제 체결 시각 기준 아님" in html
    assert "대사 확인" in html
    assert result["calls"] == ["/api/v1/orders/preview", "/api/v1/orders"]


def test_partial_pending_displays_remaining_quantity() -> None:
    """A 10-share order filled by 4 must display 6 outstanding shares."""
    result = run_orders_ui(dict(execution_state="unknown", block_reasons=[], strategy_blocks={},
        checked_at=None, auto_order_active=False, slippage={}, expired=[], fills_today=[], pending=[dict(
        order_id="old-partial", strategy_id="s", ticker="005930", name="", side="buy", qty=10,
        fill_qty=4, remaining_qty=6, order_price=100, status="PARTIALLY_FILLED", created_at="2026-10-01T03:00:00+00:00")]))
    html = result["elements"]["orders-pending"]["innerHTML"]
    assert "잔량" in html
    assert ">6</td>" in html
    assert ">4</td>" in html


def test_orders_screen_shows_liquidity_cap() -> None:
    """축소된 예정 주문이 원래 수량과 사유를 보여야 한다.

    조용히 줄어들면 사용자는 왜 이만큼만 샀는지 알 방법이 없다.
    """
    template = Path("templates/orders.html").read_text(encoding="utf-8")
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "유동성" in template
    assert "liquidity_reason" in script
    assert "유동성 축소" in script
    assert "원래 수량" in script


def test_orders_screen_distinguishes_liquidity_block_from_cash() -> None:
    """유동성 차단을 '수량부족'과 같은 말로 뭉뚱그리면 안 된다."""
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "insufficient_liquidity" in script
    assert "거래대금 미확인" in script
