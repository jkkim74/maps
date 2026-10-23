"""Durable execution policy, independent of cached startup settings."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from maps.common.exceptions import ExecutionBlockedError
from maps.common.models import ExecutionPolicy
from maps.common.settings import MapsSettings
from maps.execution.safety import account_key, execution_environment

_database = ContextVar("execution_policy_database", default=None)
_source = ContextVar("execution_dispatch_source", default=None)


@contextmanager
def policy_database(bind: Session | Engine) -> Iterator[None]:
    """Use the caller's database, including isolated test databases."""
    token = _database.set(bind)
    try:
        yield
    finally:
        _database.reset(token)


@contextmanager
def dispatch_source(source: str | None) -> Iterator[None]:
    """Carry a source verified by OrderManager to the actual broker send."""
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)


def effective_policy(settings: MapsSettings, db: Session | None = None) -> dict:
    """Read current committed policy; absent rows alone use startup defaults."""
    if db is None:
        if isinstance(_database.get(), Session):
            return effective_policy(settings, _database.get())
        from maps.common.db import engine
        with Session(bind=_database.get() or engine) as session:
            return effective_policy(settings, session)
    try:
        row = db.execute(select(ExecutionPolicy.__table__).where(
            ExecutionPolicy.account_key == account_key(settings))).mappings().first()
    except SQLAlchemyError as exc:
        raise ExecutionBlockedError("execution_policy_unavailable") from exc
    if row is None:
        from maps.common.settings import get_settings
        return dict(enabled=settings.maps_live_trading_enabled and get_settings().maps_live_trading_enabled, scope="all",
                    version=0, source="environment", updated_by=None, updated_at=None)
    if row["environment"] != execution_environment(settings) or row["scope"] not in {"all", "fujimoto_only"}:
        raise ExecutionBlockedError("execution_policy_invalid")
    return {**{k: row[k] for k in ("enabled", "scope", "version", "updated_by", "updated_at")},
            "source": "database"}


def execution_allowed(settings: MapsSettings, source: str | None = None, db: Session | None = None) -> bool:
    """Scheduling hint only; dispatch always checks again and raises on errors."""
    policy = effective_policy(settings, db)
    return bool(policy["enabled"] and not settings.maps_dry_run and (
        source is None or policy["scope"] == "all" or source == "fujimoto"))


def require_policy(settings: MapsSettings, *, source: str | None = None, check_scope: bool = False) -> None:
    """Fail closed for disabled execution or an untrusted/disallowed source."""
    policy = effective_policy(settings)
    if not policy["enabled"]:
        raise ExecutionBlockedError("trading_disabled")
    actual_source = source if source is not None else _source.get()
    if check_scope and policy["scope"] == "fujimoto_only" and actual_source != "fujimoto":
        raise ExecutionBlockedError("execution_source_not_allowed")
