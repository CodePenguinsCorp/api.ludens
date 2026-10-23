from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query, Response, status

from app.core.domain import ForbiddenError
from app.core.shared import Page, PaginationParams, make_pagination_params

from app.dependencies import get_db
from app.modules.identity.dependencies import get_current_user, require_admin

from app.modules.identity.domain.aggregates import User
from app.modules.identity.application.schemas.request import (
    RegisterRequest,
    RequestEmailChangeRequest,
    UpdateProfileRequest,
)
from app.modules.identity.application.schemas.response import TokenResponse, UserResponse

from app.modules.identity.shared import REFRESH_COOKIE, REFRESH_PATH, set_refresh_cookie
from app.modules.identity.application.usecases.user_usecase import UserUseCase

router = APIRouter(prefix="/identity/users", tags=["02.Identity - User"])

@router.post("", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, 
    response: Response, 
    session: AsyncSession = Depends(get_db)
) -> TokenResponse:
    token, raw_refresh = await UserUseCase(session).register(body)
    set_refresh_cookie(response, raw_refresh)

    return token

@router.patch("", response_model=UserResponse)
async def update(
    body: UpdateProfileRequest, 
    session: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
) -> UserResponse:
    return await UserUseCase(session).update_profile(current_user, body)

@router.post("/email/change", status_code=status.HTTP_202_ACCEPTED)
async def request_email_change(
    body: RequestEmailChangeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    return await UserUseCase(session).request_email_change(current_user, body)

@router.patch("/email/change", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email_change(
    response: Response,
    token: str = Query(...), 
    session: AsyncSession = Depends(get_db)
) -> None:
    await UserUseCase(session).confirm_email_change(token)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)

@router.post("/deletion", status_code=status.HTTP_202_ACCEPTED)
async def request_deletion(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    return await UserUseCase(session).request_account_deletion(current_user)

@router.delete("/deletion", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_deletion(
    response: Response,
    token: str = Query(...),
    session: AsyncSession = Depends(get_db)
) -> None:
    await UserUseCase(session).confirm_account_deletion(token)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)

@router.get("", response_model=Page[UserResponse])
async def list_users(
    pagination: PaginationParams = Depends(make_pagination_params()),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin)
) -> Page[UserResponse]:
    return await UserUseCase(session).list_users(pagination)

@router.get("/{user_id}", response_model=UserResponse)
async def get(user_id: UUID, session: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> UserResponse:
    if not current_user.is_admin and current_user.id != user_id:
        raise ForbiddenError("Acesso restrito ao próprio usuário.")

    return await UserUseCase(session).get_by_id(user_id)
