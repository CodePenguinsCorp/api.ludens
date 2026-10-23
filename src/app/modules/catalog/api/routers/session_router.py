from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db

from app.modules.identity.dependencies import get_current_user_optional, require_admin
from app.modules.identity.domain.aggregates import User

from app.modules.catalog.application.schemas.request import SessionRequest
from app.modules.catalog.application.schemas.response import AdminSessionResponse, SessionDetailResponse
from app.modules.catalog.application.usecases.session_usecase import SessionUseCase

router = APIRouter(prefix="/catalog/sessions", tags=["02.Catalog - Session"])

@router.post("/", response_model=AdminSessionResponse, status_code=201, dependencies=[Depends(require_admin)])
async def create(
    body: SessionRequest, 
    session: AsyncSession = Depends(get_db)
) -> AdminSessionResponse:
    return await SessionUseCase(session).create_session(body)

@router.put("/{session_id}", response_model=AdminSessionResponse, dependencies=[Depends(require_admin)])
async def update(
    session_id: UUID, 
    body: SessionRequest, 
    session: AsyncSession = Depends(get_db)
) -> AdminSessionResponse:
    return await SessionUseCase(session).update_session(session_id, body)

@router.post("/{session_id}/cancel", status_code=202)
async def cancel(
    session_id: UUID, 
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db)
) -> Response:
    await SessionUseCase(session).cancel_session(session_id, admin.id)
    return Response(status_code=202)

@router.delete("/{session_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete(
    session_id: UUID, 
    session: AsyncSession = Depends(get_db)
) -> Response:
    await SessionUseCase(session).delete_session(session_id)
    return Response(status_code=204)

@router.get("/{session_id}", response_model=None)
async def get(
    session_id: UUID,
    current_user: User | None = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_db),
) -> AdminSessionResponse | SessionDetailResponse:
    is_admin = current_user is not None and current_user.is_admin
    return await SessionUseCase(session).get_session_detail(session_id, is_admin=is_admin)
