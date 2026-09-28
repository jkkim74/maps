"""Counterfactual gate reports must not imply executable or profitable trades."""

import datetime as dt

import pytest

from scripts.limit_up_cross_samples_report import KST, build_report, guard_phase, read_logs


def _sample(clock="10:00:00", buy=True):
    return {"kst": clock, "buy": buy, "turnover": 50_000_000_000,
            "strength": 130.0, "failed": "strength"}


def _row(sid, samples, mode="automatic", crosses=None):
    return {"id": sid, "ticker": str(sid), "ref_date": "2026-09-22",
            "execution_mode": mode, "cross_samples": samples,
            "trigger_cross_count": len(samples) if crosses is None else crosses}


def _report(rows, verified=(), now=None):
    return build_report(rows, {"2026-09-22": ["kosdaq_drawdown"]},
                        {"kosdaq_latched_at": {"2026-09-22": "11:00:00"}},
                        now=now or dt.datetime(2026, 9, 23, 16, tzinfo=KST),
                        verified_days=set(verified))


def test_report_separates_buy_direction_modes_and_guard_timing():
    report = _report([
        _row(1, [_sample(), _sample(), _sample(buy=False), _sample("12:00:00")]),
        _row(2, [_sample()], "observe_only"),
    ])
    trial = next(r for r in report["comparisons"] if
                 r["min_turnover_krw"] == 50_000_000_000 and r["min_execution_strength"] == 130)
    groups = {(g["mode"], g["guard_phase"]): g for g in trial["groups"]}
    before = groups[("automatic", "before_kosdaq_latch")]
    assert (before["samples"], before["sessions"]) == (2, 1)
    assert groups[("automatic", "after_kosdaq_latch")]["samples"] == 1
    assert groups[("observe_only", "before_kosdaq_latch")]["sessions"] == 1
    assert report["verified_day_count"] == 0


@pytest.mark.parametrize("crosses,count", [(2, 1), (200, 200)])
def test_missing_or_capped_samples_cannot_be_certified(crosses, count):
    report = _report([_row(1, [_sample()] * count, crosses=crosses)], ["2026-09-22"])
    assert report["verified_day_count"] == 0


def test_partial_day_cannot_be_certified_and_full_day_requires_audit():
    rows = [_row(1, [_sample()])]
    partial = _report(rows, ["2026-09-22"], dt.datetime(2026, 9, 22, 13, tzinfo=KST))
    assert partial["verified_day_count"] == 0
    assert _report(rows)["verified_day_count"] == 0
    assert _report(rows, ["2026-09-22"])["verified_day_count"] == 1
    with pytest.raises(ValueError, match="no session evidence"):
        _report(rows, ["2026-09-21"])


def test_latch_without_time_is_not_assumed_clear():
    assert guard_phase("2026-09-22", "10:00:00", {"2026-09-22": ["kosdaq_drawdown"]}, {}) == "unknown_guard_timing"
    assert guard_phase("2026-09-22", "10:00:00", {}, {}) == "unknown_guard_timing"


def test_logs_count_attempts_once_and_preserve_latch_timestamp(tmp_path):
    path = tmp_path / "maps.log"
    summary = "2026-09-22 10:00:01 INFO KIS req summary 60s: n=10 ok=8 rate_limited=2 read_timeout=0\n"
    path.write_text(
        summary + summary +
        "2026-09-22 11:00:00 WARNING kosdaq_drawdown 래치\n" +
        "2026-09-22 10:00:00 INFO Scheduler job broker_sync: success {}\n" +
        "2026-09-22 10:01:00 INFO Scheduler job broker_sync: success {}\n" +
        "2026-09-22 18:00:00 INFO KIS req summary 60s: n=100 ok=0 rate_limited=100\n",
        encoding="utf-8",
    )
    result = read_logs([str(path), str(path)], "2026-09-22")
    assert result["kosdaq_latched_at"]["2026-09-22"] == "11:00:00"
    health = result["health"]["2026-09-22"]
    assert health["rate_limited_ratio"] == .2
    assert health["attempts"]["n"] == 10
    assert health["broker_sync_gap_p95_seconds"] == 60
