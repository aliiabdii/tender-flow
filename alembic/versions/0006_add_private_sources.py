"""Add immutable private source documents and document processing subjects."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenders",
        sa.Column(
            "sources_frozen",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.String(200), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint("id", "tender_id", name="uq_document_tender"),
        sa.CheckConstraint(
            "format IN ('csv','xlsx','pdf','docx')", name="ck_document_format"
        ),
        sa.CheckConstraint(
            "source_revision >= 1 AND size_bytes > 0", name="ck_document_source"
        ),
    )
    op.create_index("ix_documents_tender_id", "documents", ["tender_id"])
    op.create_index("ix_documents_created_by", "documents", ["created_by"])
    op.create_index("ix_documents_page", "documents", ["tender_id", "created_at", "id"])
    op.create_unique_constraint(
        "uq_processing_run_tender", "processing_runs", ["id", "tender_id"]
    )
    op.add_column("processing_items", sa.Column("tender_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE processing_items i SET tender_id=r.tender_id FROM processing_runs r WHERE i.run_id=r.id"
    )
    op.alter_column("processing_items", "tender_id", nullable=False)
    op.add_column(
        "processing_items", sa.Column("document_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_processing_item_run_tender",
        "processing_items",
        "processing_runs",
        ["run_id", "tender_id"],
        ["id", "tender_id"],
    )
    op.create_foreign_key(
        "fk_processing_item_document_tender",
        "processing_items",
        "documents",
        ["document_id", "tender_id"],
        ["id", "tender_id"],
    )
    op.create_index(
        "ix_processing_items_document_id", "processing_items", ["document_id"]
    )


def downgrade():
    op.drop_index("ix_processing_items_document_id", table_name="processing_items")
    op.drop_constraint(
        "fk_processing_item_document_tender", "processing_items", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_processing_item_run_tender", "processing_items", type_="foreignkey"
    )
    op.drop_column("processing_items", "document_id")
    op.drop_column("processing_items", "tender_id")
    op.drop_constraint("uq_processing_run_tender", "processing_runs", type_="unique")
    op.drop_table("documents")
    op.drop_column("tenders", "sources_frozen")
