from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.shared import Page, PaginationParams
from app.core.shared.pagination import make_pagination_params
from app.dependencies import get_db
from app.modules.booking.application.reservation_usecase import ReservationUseCase
from app.modules.booking.application.schemas import RebookingOptionsResponse, ReservationRequest, ReservationResponse
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.domain.aggregates import User

router = APIRouter(prefix="/booking/reservations", tags=["03.Booking - Reservation"])
reservation_pagination = make_pagination_params(default_size=12, max_size=48)


@router.post("", response_model=ReservationResponse, status_code=201)
async def create(body: ReservationRequest, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await ReservationUseCase(db).create(body, user_id=user.id, cpf=user.cpf, is_admin=user.is_admin)


@router.get("", response_model=Page[ReservationResponse])
async def list_owned(response: Response, user: User = Depends(get_current_user), pagination: PaginationParams = Depends(reservation_pagination), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await ReservationUseCase(db).list(user.id, pagination)


@router.get("/{reservation_id}", response_model=ReservationResponse)
async def detail(reservation_id: UUID, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await ReservationUseCase(db).get(reservation_id, user.id)


@router.post("/{reservation_id}/cancel", response_model=ReservationResponse)
async def cancel(reservation_id: UUID, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await ReservationUseCase(db).get(reservation_id, user.id, cancel=True)


@router.get("/{reservation_id}/rebooking-options", response_model=RebookingOptionsResponse)
async def rebooking_options(reservation_id: UUID, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await ReservationUseCase(db).rebooking_options(reservation_id, user.id)
