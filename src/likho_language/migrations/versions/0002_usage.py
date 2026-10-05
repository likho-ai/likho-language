"""How often each term and spelling was heard, with the last lines a spelling was applied to.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("glossary_terms", sa.Column("heard", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("glossary_terms", sa.Column("last_heard_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("spellings", sa.Column("applied", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("spellings", sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "spelling_examples",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("spelling_id", sa.String(40), nullable=False),
        sa.Column("recording_id", sa.String(40), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("before", sa.Text(), nullable=False),
        sa.Column("after", sa.Text(), nullable=False),
        sa.Column("heard_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("spelling_id", "recording_id", "segment_index", name="uq_example_spelling_line"),
    )
    op.create_index("ix_spelling_examples_spelling", "spelling_examples", ["spelling_id", "heard_at"])
    op.create_index("ix_spelling_examples_workspace_id", "spelling_examples", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("spelling_examples")
    op.drop_column("spellings", "last_applied_at")
    op.drop_column("spellings", "applied")
    op.drop_column("glossary_terms", "last_heard_at")
    op.drop_column("glossary_terms", "heard")
