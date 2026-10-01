"""Glossary, spellings, language policy and the vocabulary version counter.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="hi"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "term", name="uq_glossary_workspace_term"),
    )
    op.create_index("ix_glossary_terms_workspace_id", "glossary_terms", ["workspace_id"])

    op.create_table(
        "spellings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("is_phrase", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "source", name="uq_spelling_workspace_source"),
    )
    op.create_index("ix_spellings_workspace_id", "spellings", ["workspace_id"])

    op.create_table(
        "language_policies",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("detected_language", sa.String(8), nullable=False),
        sa.Column("min_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("decode_as", sa.String(8), nullable=False),
        sa.Column("transliterate", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("workspace_id", "position", name="uq_policy_workspace_position"),
    )
    op.create_index("ix_language_policies_workspace_id", "language_policies", ["workspace_id"])

    op.create_table(
        "vocabulary_versions",
        sa.Column("workspace_id", sa.String(40), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("vocabulary_versions")
    op.drop_table("language_policies")
    op.drop_table("spellings")
    op.drop_table("glossary_terms")
