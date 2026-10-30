"""Durable daily-stage claims with explicit dependencies and no business replay.

Claims are short transactions. Network/strategy work runs outside them; a worker
must retain its owner token to finish. Expired business work is ambiguous and is
failed for operator review, never silently run a second time.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from maps.common.models import DailyPipelineStage

STAGES = ("data_collection", "candidate_generation", "validation")
DEPENDENCIES = {"candidate_generation": "data_collection", "validation": "candidate_generation"}
TERMINAL = {"success", "partial", "failed", "skipped"}
KST = ZoneInfo("Asia/Seoul")


def utc_now() -> dt.datetime:
    """Return the database's UTC-naive timestamp convention."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class StageClaim:
    """An acquired ownership token, or an explicit non-executing result."""
    name: str
    ref_date: dt.date
    acquired: bool = False
    reason: str = ""
    owner: str | None = None
    stage_id: int | None = None


class DailyPipeline:
    """Serialize collection -> candidates -> validation across scheduler instances."""

    def __init__(self, session_factory, minimum_times: dict[str, str], timezone: str = "Asia/Seoul"):
        self._sessions = session_factory
        self._minimum_times = minimum_times
        self._timezone = ZoneInfo(timezone)

    def claim(self, name: str, ref_date: dt.date, now: dt.datetime | None = None) -> StageClaim:
        """Atomically claim eligible work; failed prerequisites never imply readiness."""
        if name not in STAGES:
            raise ValueError(f"Unknown daily pipeline stage: {name}")
        now = now or utc_now()
        if now.tzinfo is not None:
            now = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
        local = now.replace(tzinfo=dt.timezone.utc).astimezone(self._timezone)
        def denied(reason: str) -> StageClaim:
            return StageClaim(name, ref_date, reason=reason)
        if local.date() != ref_date:
            return denied("date_mismatch")
        hour, minute = map(int, self._minimum_times[name].split(":"))
        if local.time().replace(tzinfo=None) < dt.time(hour, minute):
            return denied("before_minimum_time")
        with self._sessions() as db:
            dependency = DEPENDENCIES.get(name)
            if dependency:
                prior = db.query(DailyPipelineStage).filter_by(ref_date=ref_date, name=dependency).one_or_none()
                if prior is None or prior.status in {"pending", "running"}:
                    return denied("dependency_pending")
                if prior.status not in {"success", "partial"}:
                    return denied("dependency_failed")
            row = db.query(DailyPipelineStage).filter_by(ref_date=ref_date, name=name).one_or_none()
            if row is None:
                row = DailyPipelineStage(ref_date=ref_date, name=name, status="pending")
                db.add(row)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    row = db.query(DailyPipelineStage).filter_by(ref_date=ref_date, name=name).one()
            if row.status in TERMINAL:
                return denied("already_finished")
            if row.status == "running":
                if row.lease_until is not None and row.lease_until <= now:
                    # The collection repository checkpoints and fences its own
                    # writes. It alone supports safe resume after interruption.
                    if name == "data_collection":
                        changed = db.execute(update(DailyPipelineStage).where(
                            DailyPipelineStage.id == row.id,
                            DailyPipelineStage.status == "running",
                            DailyPipelineStage.owner == row.owner,
                            DailyPipelineStage.lease_until <= now,
                        ).values(status="pending", owner=None)).rowcount
                        db.commit()
                        if changed != 1:
                            return denied("already_claimed")
                        db.refresh(row)
                    else:
                        db.execute(update(DailyPipelineStage).where(
                            DailyPipelineStage.id == row.id,
                            DailyPipelineStage.status == "running",
                            DailyPipelineStage.owner == row.owner,
                            DailyPipelineStage.lease_until <= now,
                        ).values(status="failed", finished_at=now,
                                 details={"reason": "interrupted_requires_review"}))
                        db.commit()
                        return denied("interrupted_requires_review")
                else:
                    return denied("already_running")
            token = uuid4().hex
            stage_id = row.id
            changed = db.execute(update(DailyPipelineStage).where(
                DailyPipelineStage.id == stage_id, DailyPipelineStage.status == "pending",
            ).values(status="running", owner=token, started_at=now,
                     lease_until=now + dt.timedelta(seconds=60))).rowcount
            db.commit()
            if changed != 1:
                return denied("already_claimed")
            return StageClaim(name, ref_date, True, owner=token, stage_id=stage_id)

    def heartbeat(self, claim: StageClaim) -> bool:
        """Renew a live worker without holding a transaction during business work."""
        with self._sessions() as db:
            changed = db.execute(update(DailyPipelineStage).where(
                DailyPipelineStage.id == claim.stage_id,
                DailyPipelineStage.owner == claim.owner,
                DailyPipelineStage.status == "running",
            ).values(lease_until=utc_now() + dt.timedelta(seconds=60))).rowcount
            db.commit()
            return changed == 1

    def finish(self, claim: StageClaim, status: str, details: dict,
               *, now: dt.datetime | None = None) -> bool:
        """Finish only the currently owned attempt; stale workers cannot publish success."""
        if not claim.acquired or status not in TERMINAL:
            return False
        now = now or utc_now()
        with self._sessions() as db:
            changed = db.execute(update(DailyPipelineStage).where(
                DailyPipelineStage.id == claim.stage_id,
                DailyPipelineStage.owner == claim.owner,
                DailyPipelineStage.status == "running",
            ).values(status=status, details=details, finished_at=now, lease_until=None)).rowcount
            db.commit()
            return changed == 1

    def status(self, ref_date: dt.date) -> dict:
        """Expose current durable progress without inferring success from elapsed time."""
        with self._sessions() as db:
            rows = db.query(DailyPipelineStage).filter_by(ref_date=ref_date).all()
            return {row.name: {"status": row.status, "started_at": row.started_at.isoformat() if row.started_at else None,
                               "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                               "details": row.details} for row in rows}
