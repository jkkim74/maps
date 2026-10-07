"""Run repository tests without loading local secrets or allowing network calls."""
import os
import socket
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0, os.getcwd())
os.environ.update(MAPS_DB_URL="sqlite:///:memory:", MAPS_BROKER_MODE="mock",
    MAPS_LIVE_TRADING_ENABLED="true", MAPS_DRY_RUN="false", KIS_REAL_TRADING="false",
    MAPS_DATA_PROVIDER="mock", KRX_ID="", KRX_PW="")
_lock_directory = tempfile.TemporaryDirectory(prefix="maps-tests-lock-")
os.environ["MAPS_EXECUTION_LOCK_DIR"] = _lock_directory.name
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from maps.common.settings import MapsSettings
MapsSettings.model_config["env_file"] = None


_connect = socket.socket.connect


def no_network(sock, address):
    # Windows asyncio creates its wake-up socketpair over loopback.
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
        return _connect(sock, address)
    raise RuntimeError("Network disabled by isolated test runner")


socket.socket.connect = no_network
import pytest
try:
    result = pytest.main(sys.argv[1:] or ["tests", "maps/tests", "-q", "--tb=short"])
finally:
    from maps.execution.safety import release_process_locks
    release_process_locks()
    _lock_directory.cleanup()
raise SystemExit(result)
