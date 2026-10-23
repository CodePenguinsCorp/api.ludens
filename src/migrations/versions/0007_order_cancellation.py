"""Record order cancellations and ticket check-in.

Revision ID: 0007_order_cancellation
Revises: 0006_simulated_purchases
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_order_cancellation"
down_revision: Union[str, None] = "0006_simulated_purchases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_orders", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column("payment_orders", sa.Column("cancelled_by", sa.Uuid(), sa.ForeignKey("users.id")))
    op.add_column("payment_orders", sa.Column("cancel_origin", sa.String(20)))
    op.add_column("payment_orders", sa.Column("cancel_reason", sa.String(300)))
    op.add_column("payment_orders", sa.Column("refund_percent", sa.Integer()))
    op.add_column("payment_orders", sa.Column("refund_cents", sa.Integer()))
    op.add_column("tickets", sa.Column("checked_in_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("tickets", "checked_in_at")
    for column in ("refund_cents", "refund_percent", "cancel_reason", "cancel_origin", "cancelled_by", "cancelled_at"):
        op.drop_column("payment_orders", column)
