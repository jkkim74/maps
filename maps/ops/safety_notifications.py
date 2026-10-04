"""Small durable outbox: notification failure never rolls back execution state."""
from datetime import timedelta

from maps.common.models import ExecutionSafetyEvent
from maps.execution.safety import utcnow
from maps.ops.notifications import Notification


def deliver_safety_events(db, notifier, account_key, limit=5):
    now = utcnow()
    events = db.query(ExecutionSafetyEvent).filter(
        ExecutionSafetyEvent.account_key == account_key,
        ExecutionSafetyEvent.delivered_at.is_(None),
        (ExecutionSafetyEvent.next_attempt_at.is_(None)) | (ExecutionSafetyEvent.next_attempt_at <= now),
    ).order_by(ExecutionSafetyEvent.id).limit(limit).all()
    for row in events:
        row.attempts += 1
        row.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(row.attempts, 7)))
        db.commit()
        try:
            delivered = notifier.send(Notification(level="WARN", title="MAPS execution safety",
                message=f"Event #{row.id}: {row.reason_code}\n{row.details}"))
        except Exception:
            delivered = False
        if delivered:
            row.delivered_at = utcnow()
            db.commit()
