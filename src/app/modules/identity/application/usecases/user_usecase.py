from uuid import UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.shared import Page, PaginationParams
from app.core.domain import AuthError, ConflictError, GoneError, NotFoundError

from app.modules.identity.domain.aggregates import User
from app.modules.identity.domain.value_objects import CPF, Email
from app.modules.identity.domain.entities.email_change_token import EmailChangeToken
from app.modules.identity.domain.entities.account_deletion_token import AccountDeletionToken

from app.modules.identity.application.schemas.request import (
    RegisterRequest,
    RequestEmailChangeRequest,
    UpdateProfileRequest,
)

from app.modules.identity.shared import issue_session
from app.modules.identity.application.schemas.response import TokenResponse, UserResponse

from app.modules.identity.infrastructure.services import PasswordService, TokenService

from app.modules.identity.application.mappers import user_response

from app.modules.identity.infrastructure.repositories import (
    AccountDeletionTokenRepository,
    EmailChangeTokenRepository,
    RefreshTokenRepository,
    UserRepository
)

class UserUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

        self._user_repository = UserRepository(session)
        self._refresh_repository = RefreshTokenRepository(session)
        self._email_change_repository = EmailChangeTokenRepository(session)
        self._account_deletion_repository = AccountDeletionTokenRepository(session)

        self._token_service = TokenService()
        self._password_service = PasswordService()

    async def register(self, req: RegisterRequest) -> tuple[TokenResponse, str]:
        cpf = CPF(req.cpf)
        email = Email(req.email.strip().lower())

        if await self._user_repository.exists_by("cpf", cpf.value):
            raise ConflictError("Este CPF já possui cadastro.")

        if await self._user_repository.exists_by("email", email.value):
            raise ConflictError("Este e-mail já está em uso.")

        user = User.register(req.name, cpf, email, self._password_service.hash(req.password))

        await self._user_repository.save(user)
        return await issue_session(user, self._token_service, self._refresh_repository)

    async def get_by_id(self, user_id: UUID) -> UserResponse:
        user = await self._user_repository.find_by("id", user_id)

        if user is None:
            raise NotFoundError("Usuário não encontrado.")

        return user_response(user)

    async def list_users(self, pagination: PaginationParams) -> Page[UserResponse]:
        users, total = await self._user_repository.list_paginated(
            page=pagination.page, size=pagination.size
        )

        return Page(
            items=[user_response(user) for user in users],
            page=pagination.page,
            size=pagination.size,
            total=total,
        )

    async def update_profile(self, user: User, req: UpdateProfileRequest) -> UserResponse:
        user.update_profile(req.name)
        await self._user_repository.save(user)

        return user_response(user)

    async def request_email_change(self, user: User, req: RequestEmailChangeRequest) -> dict[str, str]:
        user = await self._lock_account(user.id)
        if user is None:
            raise AuthError("Sessão inválida ou expirada.")
        new_email = Email(req.new_email.strip().lower())

        if await self._user_repository.exists_by("email", new_email.value):
            raise ConflictError("Este e-mail já está em uso.")

        now = datetime.now(timezone.utc)
        await self._email_change_repository.invalidate_all_for_user(user.id, now)

        raw = self._token_service.new_opaque_token()
        expires_at = now + timedelta(hours=1)

        await self._email_change_repository.save(
            EmailChangeToken(
                user_id=user.id,
                new_email=new_email.value,
                token_hash=self._token_service.hash_opaque(raw),
                expires_at=expires_at,
            )
        )

        user.request_email_change(new_email.value, raw, expires_at)
        await self._user_repository.save(user)

        return {"message": f"Enviamos um link de confirmação para {user.email}."}

    async def confirm_email_change(self, token: str) -> None:
        token_hash = self._token_service.hash_opaque(token)
        reference = await self._email_change_repository.find_by("token_hash", token_hash)
        if reference is None:
            raise GoneError("Este link não é mais válido, solicite um novo.")

        user = await self._lock_account(reference.user_id)
        record = await self._email_change_repository.find_by_token_hash_for_update(token_hash)

        now = datetime.now(timezone.utc)
        if user is None or record is None or not record.is_valid(now):
            raise GoneError("Este link não é mais válido, solicite um novo.")

        if await self._user_repository.exists_by("email", record.new_email):
            raise ConflictError("Este e-mail já está em uso.")

        record.consume(now)

        user.apply_email_change(record.new_email)

        await self._user_repository.save(user)
        await self._refresh_repository.deactivate_all_for_user(user.id)

    async def request_account_deletion(self, user: User) -> dict[str, str]:
        user = await self._lock_account(user.id, protect_admin=True)
        if user is None:
            raise AuthError("Sessão inválida ou expirada.")
        await self._ensure_not_last_admin(user)

        now = datetime.now(timezone.utc)
        await self._account_deletion_repository.invalidate_all_for_user(user.id, now)

        raw = self._token_service.new_opaque_token()
        expires_at = now + timedelta(hours=1)

        await self._account_deletion_repository.save(
            AccountDeletionToken(
                user_id=user.id,
                token_hash=self._token_service.hash_opaque(raw),
                expires_at=expires_at,
            )
        )

        user.request_account_deletion(raw, expires_at)
        await self._user_repository.save(user)

        return {"message": f"Enviamos um link de confirmação para {user.email}."}

    async def confirm_account_deletion(self, token: str) -> None:
        token_hash = self._token_service.hash_opaque(token)
        reference = await self._account_deletion_repository.find_by("token_hash", token_hash)
        if reference is None:
            raise GoneError("Este link não é mais válido, solicite um novo.")

        user = await self._lock_account(reference.user_id, protect_admin=True)
        record = await self._account_deletion_repository.find_by_token_hash_for_update(token_hash)

        now = datetime.now(timezone.utc)
        if user is None or record is None or not record.is_valid(now):
            raise GoneError("Este link não é mais válido, solicite um novo.")

        record.consume(now)

        await self._ensure_not_last_admin(user)
        # Preserve the user row and stable ID for commercial history; only revoke access.
        user.deactivate()

        await self._user_repository.save(user)
        await self._refresh_repository.deactivate_all_for_user(user.id)

    async def _lock_account(self, user_id: UUID, *, protect_admin: bool = False) -> User | None:
        # Always lock administrators before the account and its confirmation tokens.
        if protect_admin:
            user = await self._user_repository.find_by("id", user_id)
            if user is not None and user.is_admin:
                await self._user_repository.lock_active_admins()
        return await self._user_repository.find_by_id_for_update(user_id)

    async def _ensure_not_last_admin(self, user: User) -> None:
        if user.is_admin and await self._user_repository.count_active_admins() <= 1:
            raise ConflictError(
                "Você é a única pessoa administradora da plataforma. "
                "Convide outra pessoa administradora antes de encerrar sua conta."
            )
