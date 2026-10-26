"""Persistent processing jobs and active document snapshot."""

import sqlalchemy as sa
from alembic import op

revision = "cb20261021"
down_revision = "7922292ec554"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("active_manifest", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("active_snapshot_id", sa.String(64), nullable=True))
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", name="uq_processing_jobs_document_id"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name=op.f("ck_processing_jobs_status_allowed"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_processing_jobs_attempts_nonnegative")),
    )


def downgrade():
    op.drop_table("processing_jobs")
    op.drop_column("documents", "active_snapshot_id")
    op.drop_column("documents", "active_manifest")
