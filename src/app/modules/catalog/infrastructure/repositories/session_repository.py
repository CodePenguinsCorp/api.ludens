from __future__ import annotations

from uuid import UUID
from sqlalchemy import select

from app.core.infrastructure.repositories import AggregateRepository
from app.modules.catalog.domain.aggregates import Session

class SessionRepository(AggregateRepository[Session]):
    model = Session

    async def find_by_id_for_update(self, session_id: UUID) -> Session | None:
        # SELECT ... FOR UPDATE: locks the row until the transaction ends — RN05,
        # needed when editing/cancelling/deleting a session under concurrency.
        result = await self._session.execute(
            select(Session)
            .where(Session.id == session_id, Session.is_active.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return result.scalar_one_or_none()
