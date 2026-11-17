"""Add buyers, versioned tenders, contributors and idempotency records."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "buyers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("customer_number", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("revision >= 1", name="ck_buyer_revision"),
    )
    op.create_index("ix_buyers_page", "buyers", ["created_at", "id"])
    op.create_table(
        "tenders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_tender_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("pricing_customer_number", sa.String(128), nullable=False),
        sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("preparation_version", sa.Integer(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["buyer_id"], ["buyers.id"]),
        sa.ForeignKeyConstraint(["supersedes_tender_id"], ["tenders.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.CheckConstraint(
            "source_revision >= 1 AND preparation_version >= 1 AND content_revision >= 1",
            name="ck_tender_revisions",
        ),
        sa.CheckConstraint(
            "supersedes_tender_id IS NULL OR supersedes_tender_id <> id",
            name="ck_tender_not_self_amendment",
        ),
    )
    for column in ("buyer_id", "supersedes_tender_id", "created_by"):
        op.create_index(f"ix_tenders_{column}", "tenders", [column])
    op.create_index("ix_tenders_page", "tenders", ["created_at", "id"])
    op.create_table(
        "tender_contributors",
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "first_contributed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tender_id", "user_id"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_tender_contributors_user_id", "tender_contributors", ["user_id"]
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("actor_id", "scope", "key_hash"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("tender_contributors")
    op.drop_table("tenders")
    op.drop_table("buyers")
