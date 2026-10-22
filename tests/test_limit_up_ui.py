"""Operational status renders startup refusal and actual feed evidence."""

import json
from pathlib import Path
import subprocess


def test_upper_status_displays_startup_and_feed_diagnostics() -> None:
    """A configured mode must not make a stopped engine look active."""
    script = Path("static/js/app.js").read_text(encoding="utf-8")
    payload = dict(mode="off", configured_mode="automatic", running=False,
        start_outcome="refused", start_reason="live_trading_disabled<test>",
        entry_halted=True, halted_reasons=["engine_not_running"], unknown_positions=[],
        feed_state="degraded", feed_age_seconds=120, feed_pending=255,
        feed_stale_drops=12, feed_overflow_drops=45, manual_lock=False,
        daily_pnl=0, attempts=0, pattern_failures=0)
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),el={innerHTML:''};
const context=vm.createContext({document:{getElementById(){return el;},addEventListener(){}},console});
vm.runInContext(input.script,context);context.payload=input.payload;
vm.runInContext('_luRenderStatus(payload,null)',context);process.stdout.write(el.innerHTML);
"""
    result = subprocess.run(["node", "-e", harness],
        input=json.dumps(dict(script=script, payload=payload)),
        text=True, encoding="utf-8", capture_output=True, check=True)
    assert "설정 automatic" in result.stdout
    assert "엔진 정지" in result.stdout
    assert "live_trading_disabled&lt;test&gt;" in result.stdout
    assert "지연" in result.stdout
    assert "120.0초" in result.stdout
    assert "대기 255" in result.stdout
    assert "오래된 시세 12" in result.stdout
    assert "초과 45" in result.stdout
