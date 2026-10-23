"""Persistent reservations, immutable price snapshots and idempotent requests."""
import sqlalchemy as sa
from alembic import op

revision = "0005_booking_reservation"
down_revision = "0004_catalog_genre"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("buyer_cpf", sa.String(11), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("full_quantity", sa.Integer(), nullable=False),
        sa.Column("half_quantity", sa.Integer(), nullable=False),
        sa.Column("full_price_cents", sa.Integer(), nullable=False),
        sa.Column("half_price_cents", sa.Integer(), nullable=False),
        sa.Column("show_title", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Enum("open", "confirmed", "expired", "cancelled", native_enum=False, length=20), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_reservation_user_key"),
        sa.CheckConstraint("full_quantity >= 0 AND half_quantity >= 0 AND full_quantity + half_quantity > 0", name="ck_reservation_quantities"),
        sa.CheckConstraint("full_price_cents >= 0 AND half_price_cents >= 0", name="ck_reservation_prices"),
    )
    for column in ("user_id", "session_id", "expires_at"):
        op.create_index(f"ix_reservations_{column}", "reservations", [column])


def downgrade() -> None:
    op.drop_table("reservations")
