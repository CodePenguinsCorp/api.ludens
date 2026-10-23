from sqlalchemy import func, select
from uuid import UUID
from sqlalchemy.exc import IntegrityError

from app.modules.identity.domain.aggregates import User
from app.core.infrastructure.repositories import AggregateRepository
from app.core.infrastructure.queries import paginate
from app.core.domain import ConflictError

class UserRepository(AggregateRepository[User]):
    model = User

    async def save(self, entity: User) -> None:
        await super().save(entity)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            constraint = getattr(exc.orig.__cause__, "constraint_name", None)
            messages = {
                "uq_users_email_active": "Este e-mail já está em uso.",
                "uq_users_cpf_active": "Este CPF já possui cadastro.",
            }
            if constraint in messages:
                raise ConflictError(messages[constraint]) from exc
            raise

    async def find_by_id_for_update(self, user_id: UUID) -> User | None:
        result = await self._session.execute(
            select(User)
            .where(User.id == user_id, User.is_active.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_active_admins(self) -> None:
        await self._session.execute(
            select(User.id)
            .where(User.is_admin.is_(True), User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update()
        )

    async def list_paginated(self, *, page: int, size: int) -> tuple[list[User], int]:
        stmt = select(User).where(User.is_active.is_(True)).order_by(User.created_at.desc())
        rows, total = await paginate(self._session, stmt, page=page, size=size)

        return [row[0] for row in rows], total

    async def count_active_admins(self) -> int:
        result = await self._session.execute(
            select(func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True))
        )
        return int(result.scalar_one())
