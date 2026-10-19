"""allow deleting document status

Revision ID: 7922292ec554
Revises: 9aed260e102b
Create Date: 2026-10-19 14:23:39.974375

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7922292ec554"
down_revision: str | Sequence[str] | None = "9aed260e102b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_documents_status_allowed"),
        "documents",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_documents_status_allowed"),
        "documents",
        "status IN "
        "('uploading', 'uploaded', 'parsing', 'parsed', "
        "'indexing', 'ready', 'failed', 'deleting')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_documents_status_allowed"),
        "documents",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_documents_status_allowed"),
        "documents",
        "status IN ('uploading', 'uploaded', 'parsing', 'parsed', 'indexing', 'ready', 'failed')",
    )
