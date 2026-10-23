import asyncio
import os
import pytest_asyncio

from app.core.domain import Model
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.engine import make_url

# Must run before any `import app...`: Settings (src/app/config.py) requires
# DATABASE_URL and JWT_SECRET_KEY with no default, and modules imported by usecase
# tests (e.g. TokenService, identity.shared.session) touch app.config on import.
#
# Force (not setdefault) DATABASE_URL: the `engine` fixture below runs
# `drop_all` at teardown, so tests must never inherit whatever DATABASE_URL a
# developer's shell/.env.local happens to already export (e.g. the real dev
# Postgres) — that would silently wipe it.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://ludens:ludens@localhost:5433/ludens_test")
if make_url(TEST_DATABASE_URL).database != "ludens_test":
    raise RuntimeError("Tests require the isolated ludens_test database.")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-at-least-32-bytes-long")

@pytest_asyncio.fixture(scope="session")
async def engine():
    test_engine = create_async_engine(TEST_DATABASE_URL)

    # CI starts the Postgres container right before this fixture runs
    # (`docker compose up -d` has no wait-for-ready step) — retry instead of
    # failing on the first connection attempt while it finishes booting.
    for attempt in range(10):
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Model.metadata.create_all)
            break
        except (OperationalError, OSError):
            if attempt == 9:
                raise
            await asyncio.sleep(1)

    yield test_engine

    async with test_engine.begin() as conn:
        await conn.run_sync(Model.metadata.drop_all)

    await test_engine.dispose()

@pytest_asyncio.fixture
async def session(engine):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False) as db_session:
            yield db_session
        await transaction.rollback()
