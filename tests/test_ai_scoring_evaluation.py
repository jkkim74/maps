"""Pure metrics and guarded CLI tests for AI scoring model evaluation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from maps.ai.evaluation import ModelObservation, summarize_observations
from scripts.evaluate_ai_scoring_models import main


def test_evaluation_summary_calculates_success_variance_tokens_and_latency() -> None:
    """Summaries aggregate schema success, repeat variance, usage, and latency."""
    observations = [
        ModelObservation("sonnet", "005930", 78, True, 400, 80, 1.2),
        ModelObservation("sonnet", "005930", 80, True, 402, 82, 1.1),
        ModelObservation("haiku", "005930", None, False, 390, 20, 0.4),
    ]

    summaries = {
        item.model_id: item for item in summarize_observations(observations)
    }

    assert summaries["sonnet"].schema_success_rate == 1.0
    assert summaries["sonnet"].score_stddev == pytest.approx(1.0)
    assert summaries["sonnet"].input_tokens == 802
    assert summaries["haiku"].schema_success_rate == 0.0


def test_cli_dry_run_makes_no_model_calls(monkeypatch, capsys) -> None:
    """The CLI requires --execute before any billable work can start."""
    call = Mock(side_effect=AssertionError("billable call"))
    monkeypatch.setattr(
        "scripts.evaluate_ai_scoring_models.run_live_evaluation", call
    )

    assert main(["--ref-date", "2026-08-07", "--sample-size", "5"]) == 0

    assert call.call_count == 0
    assert "20 planned calls" in capsys.readouterr().out


def test_cli_execute_refuses_missing_credentials(monkeypatch, capsys) -> None:
    """Even explicit execution is blocked when AWS credentials are absent."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")
    from maps.common.settings import reload_settings

    reload_settings()
    try:
        assert main(["--ref-date", "2026-08-07", "--execute"]) == 2
        assert "AWS credentials" in capsys.readouterr().err
    finally:
        reload_settings()
