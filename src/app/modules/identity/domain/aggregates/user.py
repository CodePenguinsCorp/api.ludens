from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.domain import AggregateRoot, DomainEvent, Model
from app.modules.identity.domain.events import (
    AccountDeletionRequested,
    EmailChangeRequested,
    EmailChanged,
    PasswordResetRequested,
    UserDeactivated,
    UserPasswordChanged,
    UserProfileUpdated,
    UserRegistered,
    UserSecurityStampRotated,
)

from app.modules.identity.domain.value_objects import CPF, Email

class User(AggregateRoot, Model):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_active", "email", unique=True, postgresql_where=text("is_active = true")),
        Index("uq_users_cpf_active", "cpf", unique=True, postgresql_where=text("is_active = true")),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cpf: Mapped[str] = mapped_column(String(11), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(60), nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    security_stamp: Mapped[UUID] = mapped_column(default=uuid4, nullable=False)

    @classmethod
    def register(cls, name: str, cpf: CPF, email: Email, password_hash: str, is_admin: bool = False) -> User:
        user = cls()
        user.raise_event(
            lambda v: UserRegistered(
                version=v,
                id=uuid4(),
                name=name,
                cpf=cpf.value,
                email=email.value,
                password_hash=password_hash,
                is_admin=is_admin,
                security_stamp=uuid4(),
            )
        )

        return user

    def change_password(self, new_hash: str) -> None:
        self.raise_event(
            lambda v: UserPasswordChanged(version=v, id=self.id, password_hash=new_hash)
        )
        self.rotate_security_stamp()

    def reset_password(self, new_hash: str) -> None:
        self.raise_event(
            lambda v: UserPasswordChanged(version=v, id=self.id, password_hash=new_hash)
        )
        self.rotate_security_stamp()

    def rotate_security_stamp(self) -> None:
        self.raise_event(
            lambda v: UserSecurityStampRotated(version=v, id=self.id, security_stamp=uuid4())
        )

    def request_password_reset(self, token: str, expires_at: datetime) -> None:
        self.raise_event(
            lambda v: PasswordResetRequested(
                version=v, id=self.id, email=self.email, token=token, expires_at=expires_at
            )
        )

    def update_profile(self, name: str) -> None:
        self.raise_event(lambda v: UserProfileUpdated(version=v, id=self.id, name=name))

    def request_email_change(self, new_email: str, token: str, expires_at: datetime) -> None:
        self.raise_event(
            lambda v: EmailChangeRequested(
                version=v, id=self.id, old_email=self.email, new_email=new_email,
                token=token, expires_at=expires_at,
            )
        )

    def apply_email_change(self, new_email: str) -> None:
        self.raise_event(lambda v: EmailChanged(version=v, id=self.id, new_email=new_email))
        self.rotate_security_stamp()

    def request_account_deletion(self, token: str, expires_at: datetime) -> None:
        self.raise_event(
            lambda v: AccountDeletionRequested(
                version=v, id=self.id, email=self.email, token=token, expires_at=expires_at
            )
        )

    def deactivate(self) -> None:
        self.raise_event(lambda v: UserDeactivated(version=v, id=self.id))
        self.rotate_security_stamp()

    def _apply(self, event: DomainEvent) -> None:
        handler = getattr(self, f"_when_{type(event).__name__}", None)
        if handler:
            handler(event)

    def _when_UserRegistered(self, e: UserRegistered) -> None:
        self.id = e.id
        self.name = e.name
        self.cpf = e.cpf
        self.email = e.email
        self.password_hash = e.password_hash
        self.is_admin = e.is_admin
        self.security_stamp = e.security_stamp

    def _when_UserPasswordChanged(self, e: UserPasswordChanged) -> None:
        self.password_hash = e.password_hash

    def _when_UserSecurityStampRotated(self, e: UserSecurityStampRotated) -> None:
        self.security_stamp = e.security_stamp

    def _when_UserProfileUpdated(self, e: UserProfileUpdated) -> None:
        self.name = e.name

    def _when_EmailChanged(self, e: EmailChanged) -> None:
        self.email = e.new_email

    def _when_UserDeactivated(self, _event: UserDeactivated) -> None:
        self.is_active = False
