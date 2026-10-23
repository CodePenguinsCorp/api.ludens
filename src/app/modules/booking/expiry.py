import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import AsyncSessionLocal
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository

logger = logging.getLogger(__name__)


async def expire_batch() -> int:
    async with AsyncSessionLocal() as db:
        async with db.begin():
            return await ReservationRepository(db).expire_due(datetime.now(timezone.utc))


async def run() -> None:
    while True:
        try:
            count = await expire_batch()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falha ao expirar reservas; o próximo lote tentará novamente.")
            count = 0
        await asyncio.sleep(0 if count == 100 else settings.reservation_expiry_interval_seconds)
