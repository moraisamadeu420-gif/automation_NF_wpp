"""initial schema

Revision ID: 0001
Revises: -
Create Date: 2026-05-22

Nota para bancos EXISTENTES (SQLite já em uso):
    NÃO rode `alembic upgrade head` — as tabelas já existem.
    Execute: alembic stamp 0001
    Isso marca a migration como aplicada sem executá-la.

Nota para instalações NOVAS ou PostgreSQL:
    Execute normalmente: alembic upgrade head
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.dialect.has_table(bind, "users")
    if existing:
        # Banco já existe — stamp foi chamado manualmente, não há nada a fazer
        return

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("whatsapp_number", sa.String(20), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_blocked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("subscription_expires_at", sa.DateTime, nullable=True),
        sa.Column("subscription_cancelled_at", sa.DateTime, nullable=True),
        sa.Column("last_payment_id", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "nfse_credentials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("portal_type", sa.String(50), nullable=False, server_default="nacional"),
        sa.Column("municipality", sa.String(100), nullable=False),
        sa.Column("portal_url", sa.Text, nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password", sa.Text, nullable=False),
        sa.Column("prestador_nome", sa.String(200), nullable=True),
        sa.Column("prestador_cnpj", sa.String(20), nullable=True),
        sa.Column("tomador_cnpj", sa.String(20), nullable=True),
        sa.Column("tomador_razao_social", sa.String(200), nullable=True),
        sa.Column("service_description", sa.Text, nullable=True),
        sa.Column("service_aliquota_iss", sa.Float, nullable=False, server_default="2.0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("invoice_number", sa.String(50), nullable=True),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("period", sa.String(100), nullable=True),
        sa.Column("municipality", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("pdf_path", sa.Text, nullable=True),
        sa.Column("xml_path", sa.Text, nullable=True),
        sa.Column("failed_at", sa.DateTime, nullable=True),
        sa.Column("failed_stage", sa.String(50), nullable=True),
        sa.Column("screenshot_path", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "whatsapp_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
        sa.Column("state", sa.String(50), nullable=False, server_default="idle"),
        sa.Column("context_data", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("whatsapp_sessions")
    op.drop_table("invoices")
    op.drop_table("nfse_credentials")
    op.drop_table("users")
