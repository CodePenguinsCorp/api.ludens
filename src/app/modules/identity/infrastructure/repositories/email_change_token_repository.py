from datetime import datetime
from uuid import UUID
from sqlalchemy import select

from app.core.infrastructure.repositories import BaseRepository
from app.modules.identity.domain.entities.email_change_token import EmailChangeToken

class EmailChangeTokenRepository(BaseRepository[EmailChangeToken]):
    model = EmailChangeToken

    async def find_by_token_hash_for_update(self, token_hash: str) -> EmailChangeToken | None:
        result = await self._session.execute(
            select(EmailChangeToken)
            .where(EmailChangeToken.token_hash == token_hash, EmailChangeToken.is_active.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def invalidate_all_for_user(self, user_id: UUID, now: datetime) -> None:
        for token in await self.find_all_by(user_id=user_id):
            if token.used_at is None:
                token.used_at = now
