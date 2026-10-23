"""Add simulated payment orders and demo tickets.

Revision ID: 0006_simulated_purchases
Revises: 0005_booking_reservation
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_simulated_purchases"
down_revision: Union[str, None] = "0005_booking_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("reservations.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("show_title", sa.String(200), nullable=False),
        sa.Column("session_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(200), nullable=False),
        sa.Column("full_quantity", sa.Integer(), nullable=False),
        sa.Column("half_quantity", sa.Integer(), nullable=False),
        sa.Column("full_price_cents", sa.Integer(), nullable=False),
        sa.Column("half_price_cents", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reservation_id", name="uq_payment_orders_reservation"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payment_orders_amount"),
    )
    for column in ("reservation_id", "user_id", "session_id"):
        op.create_index(f"ix_payment_orders_{column}", "payment_orders", [column])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("payment_orders.id"), nullable=False),
        sa.Column("ticket_type", sa.String(8), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(48), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_index("ix_tickets_order_id", "tickets", ["order_id"])


def downgrade() -> None:
    op.drop_table("tickets")
    op.drop_table("payment_orders")
