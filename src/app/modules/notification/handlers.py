from app.config import settings
from app.outbox.registry import register

from app.modules.notification.infrastructure.services import get_email_service
from app.modules.notification.shared import (
    account_deletion_requested_email,
    email_change_requested_email,
    email_changed_courtesy_email,
    order_paid_email,
    password_reset_email,
)

@register("PasswordResetRequested")
async def handle_password_reset_requested(payload: dict) -> None:
    reset_url = f"{settings.frontend_base_url}/redefinir-senha?token={payload['token']}"
    subject, html_body = password_reset_email(reset_url)

    await get_email_service().send(payload["email"], subject, html_body)

@register("EmailChangeRequested")
async def handle_email_change_requested(payload: dict) -> None:
    confirm_url = f"{settings.frontend_base_url}/confirmar-troca-de-email?token={payload['token']}"
    subject, html_body = email_change_requested_email(confirm_url, payload["new_email"])

    await get_email_service().send(payload["old_email"], subject, html_body)

@register("EmailChanged")
async def handle_email_changed(payload: dict) -> None:
    subject, html_body = email_changed_courtesy_email()

    await get_email_service().send(payload["new_email"], subject, html_body)

@register("AccountDeletionRequested")
async def handle_account_deletion_requested(payload: dict) -> None:
    confirm_url = f"{settings.frontend_base_url}/confirmar-exclusao-de-conta?token={payload['token']}"
    subject, html_body = account_deletion_requested_email(confirm_url)

    await get_email_service().send(payload["email"], subject, html_body)


@register("OrderPaid")
async def handle_order_paid(payload: dict) -> None:
    subject, html_body = order_paid_email(payload)
    await get_email_service().send(payload["email"], subject, html_body)
