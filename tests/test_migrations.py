"""Alembic 전체 체인이 새 DB에 필요한 전략매매 스키마를 만든다."""

from __future__ import annotations

import logging

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_fresh_database_reaches_split_plan_schema(tmp_path, monkeypatch) -> None:
    """새 설치에서 0021 누락으로 분할 계획 저장이 실패하는 회귀를 막는다."""
    db_path = tmp_path / "maps-migration.db"
    monkeypatch.setenv("MAPS_DB_URL", f"sqlite:///{db_path.as_posix()}")
    application_logger = logging.getLogger("maps.risk.manager")
    monkeypatch.setattr(application_logger, "disabled", False)

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)
    pick_columns = {column["name"] for column in inspector.get_columns("analysis_pick")}
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    assert revision == "0021_analysis_pick_split_plan"
    assert {"trade_mode", "total_budget", "entries_cancelled", "exit_pending_reason"} <= pick_columns
    assert "analysis_pick_leg" in inspector.get_table_names()
    indexes = {index["name"]: index for index in inspector.get_indexes("analysis_pick")}
    assert indexes["uq_analysis_pick_active_ticker"]["unique"] == 1
    assert application_logger.disabled is False


def test_split_plan_migration_reports_existing_active_ticker_duplicates(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "maps-duplicate-active.db"
    monkeypatch.setenv("MAPS_DB_URL", f"sqlite:///{db_path.as_posix()}")
    config = Config("alembic.ini")
    command.upgrade(config, "0020_ai_scoring")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO analysis_pick "
            "(ref_date, ticker, name, source, state, strategy_trade_enabled) VALUES "
            "('2026-08-11', '005930', '삼성전자', 'manual', 'ARMED', 1), "
            "('2026-08-11', '005930', '삼성전자', 'manual', 'BOUGHT', 1)"
        ))
    engine.dispose()

    with pytest.raises(RuntimeError, match="duplicate active tickers.*005930"):
        command.upgrade(config, "head")
