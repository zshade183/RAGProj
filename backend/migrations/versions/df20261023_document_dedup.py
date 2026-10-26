"""Reject duplicate live content in one knowledge base."""

import sqlalchemy as sa
from alembic import op

revision = "df20261023"
down_revision = "cb20261021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_documents_knowledge_base_sha_live",
        "documents",
        ["knowledge_base_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("status <> 'deleting'"),
    )


def downgrade():
    op.drop_index("uq_documents_knowledge_base_sha_live", table_name="documents")
