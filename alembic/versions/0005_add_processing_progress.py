"""Add durable processing run and item progress."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("retry_of_run_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["retry_of_run_id"], ["processing_runs.id"]),
        sa.ForeignKeyConstraint(["superseded_by_run_id"], ["processing_runs.id"]),
        sa.CheckConstraint(
            "kind IN ('extraction','matching','lookup','pricing','export')",
            name="ck_processing_run_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial_failure','failed','superseded')",
            name="ck_processing_run_status",
        ),
    )
    for column in ("tender_id", "actor_id", "retry_of_run_id", "superseded_by_run_id"):
        op.create_index(f"ix_processing_runs_{column}", "processing_runs", [column])
    op.create_index(
        "uq_processing_run_current",
        "processing_runs",
        ["tender_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "processing_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("expected_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("task_name", sa.String(128), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("retry_of_item_id", sa.Uuid(), nullable=True),
        sa.Column("result_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["processing_runs.id"]),
        sa.ForeignKeyConstraint(["retry_of_item_id"], ["processing_items.id"]),
        sa.UniqueConstraint("run_id", "key", name="uq_processing_item_key"),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','superseded')",
            name="ck_processing_item_state",
        ),
        sa.CheckConstraint(
            "(state='failed' AND error_code IS NOT NULL AND result_id IS NULL) "
            "OR (state<>'failed' AND error_code IS NULL)",
            name="ck_processing_item_outcome",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN "
            "('upstream_timeout','upstream_unavailable','upstream_rate_limited',"
            "'configuration_error','unsupported_input','invalid_result','worker_failed')",
            name="ck_processing_item_error",
        ),
    )
    op.create_index("ix_processing_items_run_id", "processing_items", ["run_id"])
    op.create_index(
        "ix_processing_items_retry_of_item_id", "processing_items", ["retry_of_item_id"]
    )
    op.create_index(
        "ix_processing_items_page", "processing_items", ["run_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_table("processing_items")
    op.drop_table("processing_runs")
