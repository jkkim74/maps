"""Alembic 전체 체인이 새 DB에 필요한 최신 스키마를 만든다."""

from __future__ import annotations

import logging

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_fresh_database_reaches_current_schema(tmp_path, monkeypatch) -> None:
    """새 설치에서 분석·포트폴리오 최신 스키마 누락을 막는다."""
    db_path = tmp_path / "maps-migration.db"
    monkeypatch.setenv("MAPS_DB_URL", f"sqlite:///{db_path.as_posix()}")
    application_logger = logging.getLogger("maps.risk.manager")
    monkeypatch.setattr(application_logger, "disabled", False)

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)
    pick_columns = {column["name"] for column in inspector.get_columns("analysis_pick")}
    portfolio_columns = {
        column["name"] for column in inspector.get_columns("portfolio_snapshot")
    }
    order_columns = {column["name"] for column in inspector.get_columns("order_log")}
    audit_columns = {
        column["name"] for column in inspector.get_columns("holding_regime_audit")
    }
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    assert revision == "0029_limit_up_v1"
    assert {"trade_mode", "total_budget", "entries_cancelled", "exit_pending_reason"} <= pick_columns
    assert "ai_recommendation" in pick_columns
    assert "holding_details" in portfolio_columns
    assert "decision_context" in order_columns
    assert {
        "id",
        "ref_date",
        "position_key",
        "ticker",
        "strategy_id",
        "entry_regime",
        "current_regime",
        "weekly_trend",
        "vol_regime",
        "action",
        "reason_code",
        "confirmed",
        "mode",
        "details",
        "created_at",
        "updated_at",
    } <= audit_columns
    audit_uniques = inspector.get_unique_constraints("holding_regime_audit")
    assert any(
        set(constraint["column_names"]) == {"ref_date", "position_key"}
        for constraint in audit_uniques
    )
    assert "analysis_pick_leg" in inspector.get_table_names()
    history_columns = {
        column["name"]
        for column in inspector.get_columns("stock_analysis_history")
    }
    assert {
        "snapshot",
        "narrative",
        "trade_plan",
        "recommendation",
        "analyzed_price",
        "latest_price",
        "latest_reference_close",
        "latest_price_source",
        "price_refreshed_at",
    } <= history_columns
    indexes = {index["name"]: index for index in inspector.get_indexes("analysis_pick")}
    assert indexes["uq_analysis_pick_active_ticker"]["unique"] == 1
    assert application_logger.disabled is False

    assert {"limit_up_session", "limit_up_order_leg", "limit_up_event", "limit_up_tape"} <= set(
        inspector.get_table_names()
    )
    session_columns = {
        column["name"] for column in inspector.get_columns("limit_up_session")
    }
    assert {
        "ref_date",
        "ticker",
        "state",
        "state_version",
        "upper_limit_price",
        "trigger_price",
        "first_fill_at",
        "end_reason",
    } <= session_columns
    event_uniques = inspector.get_unique_constraints("limit_up_event")
    assert any(
        set(constraint["column_names"]) == {"idempotency_key"}
        for constraint in event_uniques
    )

    # 개인화 1차: 계정 테이블과 소유자 컬럼. 기존 행은 NULL 로 남는다(backfill 없음).
    assert "app_user" in inspector.get_table_names()
    user_columns = {column["name"] for column in inspector.get_columns("app_user")}
    assert {"username", "password_hash", "role", "status", "plan", "preferences"} <= user_columns
    assert "owner_user_id" in pick_columns
    assert "owner_user_id" in history_columns


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
