from datetime import datetime
from uuid import UUID
from sqlalchemy import select

from app.core.infrastructure.repositories import BaseRepository
from app.modules.identity.domain.entities.account_deletion_token import AccountDeletionToken

class AccountDeletionTokenRepository(BaseRepository[AccountDeletionToken]):
    model = AccountDeletionToken

    async def find_by_token_hash_for_update(self, token_hash: str) -> AccountDeletionToken | None:
        result = await self._session.execute(
            select(AccountDeletionToken)
            .where(AccountDeletionToken.token_hash == token_hash, AccountDeletionToken.is_active.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def invalidate_all_for_user(self, user_id: UUID, now: datetime) -> None:
        for token in await self.find_all_by(user_id=user_id):
            if token.used_at is None:
                token.used_at = now
