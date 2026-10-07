"""Merge classification snapshots and limit-up shadow probe histories."""

revision = "0040_merge_classification_shadow"
down_revision = ("0039_classification_snapshots", "0038_limit_up_shadow_probes")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both parents; their migrations perform all schema changes."""


def downgrade() -> None:
    """Restore both parent heads without removing their schemas."""
