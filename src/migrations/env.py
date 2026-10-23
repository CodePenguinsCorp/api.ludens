import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.core.domain import Model

# Import all models so SQLAlchemy registers the tables in the metadata.
import app.outbox.models  # noqa: F401,E402

# Each feature adds its own module's import here.
import app.modules.identity.domain.aggregates.user  # noqa: F401,E402
import app.modules.identity.domain.entities.password_reset_token  # noqa: F401,E402
import app.modules.identity.domain.entities.refresh_token  # noqa: F401,E402
import app.modules.identity.domain.entities.account_deletion_token  # noqa: F401,E402
import app.modules.identity.domain.entities.email_change_token  # noqa: F401,E402

# catalog-admin-management:
import app.modules.catalog.domain.aggregates.show  # noqa: F401,E402
import app.modules.catalog.domain.aggregates.session  # noqa: F401,E402
import app.modules.booking.domain.reservation  # noqa: F401,E402
import app.modules.booking.domain.purchase  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Model.metadata

def _get_url() -> str:
    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return url

def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=settings.db_connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
