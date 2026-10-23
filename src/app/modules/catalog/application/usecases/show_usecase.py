from __future__ import annotations

import random
from uuid import UUID
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.shared import Page, PaginationParams
from app.core.domain.errors import ConflictError, NotFoundError
from app.modules.booking.dependencies import reservation_counts, cancel_session_reservations

from app.modules.catalog.application.schemas.request import ShowRequest
from app.modules.catalog.domain.aggregates import Genre, SeatCounts, Session, Show
from app.modules.catalog.application.schemas.response import (
    AdminShowResponse,
    AdminShowSummaryResponse,
    ShowCardResponse,
    SessionSummaryResponse,
    ShowDetailResponse,
)
from app.modules.catalog.application.usecases.utils import floor_from
from app.modules.catalog.application.mappers import card_response, session_response

from app.modules.catalog.infrastructure.queries import ShowSearchQuery
from app.modules.catalog.infrastructure.repositories import GenreRepository, SessionRepository, ShowRepository

_DEFAULT_SHOW_IMAGES = [
    "/images/show-placeholders/1.jpg",
    "/images/show-placeholders/2.jpg",
    "/images/show-placeholders/3.jpg",
    "/images/show-placeholders/4.jpg",
    "/images/show-placeholders/5.jpg",
    "/images/show-placeholders/6.jpg",
]

def _show_summary(show: Show, genre_name: str) -> AdminShowSummaryResponse:
    return AdminShowSummaryResponse(
        id=show.id,
        title=show.title,
        synopsis=show.synopsis,
        image_url=show.image_url,
        genre_id=show.genre_id,
        genre=genre_name,
        status=show.status.value,
    )

def _show_response(
    show: Show, genre_name: str, sessions: list[Session], counts_map: dict[UUID, SeatCounts], now: datetime
) -> AdminShowResponse:
    return AdminShowResponse(
        **_show_summary(show, genre_name).model_dump(),
        sessions=[
            session_response(s, counts_map.get(s.id, SeatCounts(0, 0)), now) for s in sessions
        ],
    )

def _session_summary(session: Session, counts: SeatCounts, now: datetime) -> SessionSummaryResponse:
    available = session.available_count(counts)

    return SessionSummaryResponse(
        id=session.id,
        starts_at=session.starts_at,
        venue=session.venue,
        capacity=session.capacity,
        available_count=available,
        status=session.status_at(now, counts),
    )

class ShowUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._show_repository = ShowRepository(session)
        self._session_repository = SessionRepository(session)
        self._genre_repository = GenreRepository(session)
        self._show_search_query = ShowSearchQuery(session)

    async def create_show(self, req: ShowRequest) -> AdminShowResponse:
        genre = await self._require_genre(req.genre_id)
        show = Show.create(
            title=req.title,
            synopsis=req.synopsis,
            image_url=random.choice(_DEFAULT_SHOW_IMAGES),
            genre_id=genre.id,
        )

        await self._show_repository.save(show)
        return _show_response(show, genre.name, [], {}, datetime.now(timezone.utc))

    async def update_show(self, show_id: UUID, req: ShowRequest) -> AdminShowResponse:
        show = await self._require_show(show_id)
        genre = await self._require_genre(req.genre_id)
        show.update(
            title=req.title,
            synopsis=req.synopsis,
            image_url=show.image_url,
            genre_id=genre.id,
        )

        await self._show_repository.save(show)
        return await self._view_for(show)

    async def publish_show(self, show_id: UUID) -> None:
        show = await self._require_show(show_id)
        show.publish()

        await self._show_repository.save(show)

    async def unpublish_show(self, show_id: UUID) -> None:
        show = await self._require_show(show_id)
        show.unpublish()

        await self._show_repository.save(show)

    async def delete_show(self, show_id: UUID) -> None:
        show = await self._require_show(show_id)
        sessions = await self._session_repository.find_all_by(show_id=show.id, order_by=["starts_at"])
        for session in sorted(sessions, key=lambda s: s.id):
            await self._session_repository.find_by_id_for_update(session.id)
        counts = await self._counts(sessions, datetime.now(timezone.utc))

        if any(counts.get(s.id, SeatCounts(0, 0)).tickets_sold > 0 for s in sessions):
            raise ConflictError(
                "Cancele as sessões com ingressos vendidos antes de excluir o espetáculo."
            )

        show.deactivate()
        for session in sessions:
            await cancel_session_reservations(self._db, session.id, datetime.now(timezone.utc))
        await self._show_repository.save(show)

    async def search(
        self, *, from_date: date | None, genre_id: UUID | None, pagination: PaginationParams, is_admin: bool
    ) -> Page[AdminShowSummaryResponse] | Page[ShowCardResponse]:
        if is_admin:
            shows, total = await self._show_repository.find_all_paginated(
                order_by=["-created_at"], page=pagination.page, size=pagination.size
            )
            genres = await self._genre_repository.find_all_by_ids([s.genre_id for s in shows])
            names = {g.id: g.name for g in genres}

            return Page(
                items=[_show_summary(s, names.get(s.genre_id, "")) for s in shows],
                page=pagination.page,
                size=pagination.size,
                total=total,
            )

        floor = floor_from(from_date)
        rows, total = await self._show_search_query.search_with_upcoming(
            floor=floor, genre_id=genre_id, page=pagination.page, size=pagination.size
        )

        return Page(
            items=[card_response(row) for row in rows],
            page=pagination.page,
            size=pagination.size,
            total=total,
        )

    async def get_show_detail(self, show_id: UUID, *, is_admin: bool) -> AdminShowResponse | ShowDetailResponse:
        show = await self._require_show(show_id)

        if is_admin:
            return await self._view_for(show)

        if not show.is_published:
            raise NotFoundError("Espetáculo não encontrado.")
        now = datetime.now(timezone.utc)

        sessions = [
            s
            for s in await self._session_repository.find_all_by(show_id=show.id, order_by=["starts_at"])
            if s.starts_at > now
        ]
        counts = await self._counts(sessions, now)
        genre = await self._require_genre(show.genre_id)

        return ShowDetailResponse(
            id=show.id,
            title=show.title,
            synopsis=show.synopsis,
            image_url=show.image_url,
            genre_id=show.genre_id,
            genre=genre.name,
            sessions=[
                _session_summary(s, counts.get(s.id, SeatCounts(0, 0)), now) for s in sessions
            ],
        )

    async def _require_show(self, show_id: UUID) -> Show:
        show = await self._show_repository.find_by("id", show_id)
        if show is None:
            raise NotFoundError("Espetáculo não encontrado.")

        return show

    async def _require_genre(self, genre_id: UUID) -> Genre:
        genre = await self._genre_repository.find_by("id", genre_id)
        if genre is None:
            raise NotFoundError("Gênero não encontrado.")

        return genre

    async def _view_for(self, show: Show) -> AdminShowResponse:
        sessions = await self._session_repository.find_all_by(show_id=show.id, order_by=["starts_at"])
        now = datetime.now(timezone.utc)
        counts = await self._counts(sessions, now)
        genre = await self._require_genre(show.genre_id)

        return _show_response(show, genre.name, sessions, counts, now)

    async def _counts(self, sessions: list[Session], now: datetime) -> dict[UUID, SeatCounts]:
        counts = await reservation_counts(self._db, [s.id for s in sessions], now)
        return {session_id: SeatCounts(*values) for session_id, values in counts.items()}
