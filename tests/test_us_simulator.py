"""Explicit local simulator actions can never target KIS."""
import pytest
from maps.common.settings import MapsSettings
from maps.common.exceptions import ExecutionBlockedError


def test_simulator_rejects_broker_connected_mode():
    from scripts.us_paper_simulator import require_local_mock
    settings = MapsSettings(_env_file=None, maps_us_broker_mode="kis_paper")
    with pytest.raises(ExecutionBlockedError, match="local_mock_only"):
        require_local_mock(settings)


def test_simulator_requires_explicit_mock_capital():
    from scripts.us_paper_simulator import require_local_mock
    with pytest.raises(ExecutionBlockedError, match="mock_cash"):
        require_local_mock(MapsSettings(_env_file=None))
