"""Populate the local development database without deleting existing records."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.modules.booking.application.reservation_usecase import ReservationUseCase  # noqa: E402
from app.modules.booking.application.schemas import ReservationRequest  # noqa: E402
from app.modules.booking.domain.reservation import Reservation  # noqa: E402
from app.modules.booking.infrastructure.reservation_repository import ReservationRepository  # noqa: E402
from app.modules.catalog.domain.aggregates import Genre, Session, Show  # noqa: E402
from app.modules.catalog.infrastructure.repositories import GenreRepository, SessionRepository, ShowRepository  # noqa: E402
from app.modules.identity.domain.aggregates import User  # noqa: E402
from app.modules.identity.domain.value_objects import CPF, Email  # noqa: E402
from app.modules.identity.infrastructure.repositories import UserRepository  # noqa: E402
from app.modules.identity.infrastructure.services import PasswordService  # noqa: E402


SHOWS = [
    ("Uma Noite de Risadas", "Comédia", "Uma família transforma um jantar simples em uma sequência de situações divertidas.", 4000, 80),
    ("Cartas do Amanhã", "Drama", "Dois amigos reencontram cartas antigas e descobrem novos caminhos para suas vidas.", 5000, 60),
    ("A Floresta Encantada", "Infantil", "Uma aventura sobre amizade, imaginação e cuidado com a natureza para toda a família.", 3000, 100),
    ("Ritmos da Cidade", "Musical", "Música e dança contam histórias de pessoas que se encontram em uma praça de bairro.", 6000, 120),
    ("O Mistério da Mala Azul", "Comédia", "Uma mala trocada na estação aproxima personagens cheios de histórias inesperadas.", 3500, 50),
    ("Pequeno Palco, Grandes Histórias", "Drama", "Moradores de uma comunidade compartilham lembranças em um espetáculo intimista.", 2000, 6),
]


def demo_cpf(number: int) -> CPF:
    digits = [int(digit) for digit in f"{900000000 + number:09d}"]
    for length in (9, 10):
        check = sum(digit * (length + 1 - index) for index, digit in enumerate(digits)) * 10 % 11
        digits.append(0 if check == 10 else check)
    return CPF("".join(map(str, digits)))


async def seed_demo() -> None:
    if settings.environment != "development":
        raise SystemExit("Este script é destinado ao ambiente development.")
    now = datetime.now(timezone.utc)
    password = os.environ.get("DEMO_PASSWORD", "Ludens123!")
    created = {"users": 0, "genres": 0, "shows": 0, "sessions": 0, "reservations": 0}

    async with AsyncSessionLocal() as db, db.begin():
        # Serialize repeated invocations of this script without modifying existing data.
        from sqlalchemy import text
        await db.execute(text("SELECT pg_advisory_xact_lock(84513729)"))
        users = UserRepository(db)
        accounts = {}
        for number, (name, email, is_admin) in enumerate([
            ("Comprador Demonstração", "comprador.demo@example.com", False),
            ("Administrador Demonstração", "admin.demo@example.com", True),
        ], start=1):
            user = await users.find_by("email", email)
            if user is None:
                user = User.register(name, demo_cpf(number), Email(email), PasswordService().hash(password), is_admin=is_admin)
                await users.save(user)
                await db.flush()
                created["users"] += 1
            accounts[email] = user

        sessions = []
        for index, (title, genre_name, synopsis, price, capacity) in enumerate(SHOWS):
            genre = await GenreRepository(db).find_by("name", genre_name)
            if genre is None:
                genre = Genre.create(name=genre_name)
                await GenreRepository(db).save(genre)
                await db.flush()
                created["genres"] += 1
            show = await ShowRepository(db).find_by("title", title)
            if show is None:
                show = Show.create(title=title, synopsis=synopsis,
                    image_url=f"/images/show-placeholders/{index + 1}.jpg", genre_id=genre.id)
                show.publish()
                await ShowRepository(db).save(show)
                await db.flush()
                created["shows"] += 1
            existing = list(await db.scalars(select(Session).where(Session.show_id == show.id).order_by(Session.starts_at)))
            if not existing:
                for performance in range(2):
                    # 22:00 UTC = 19:00 in Sao Paulo, always on a future date.
                    starts_at = (now + timedelta(days=2 + index + performance * 7)).replace(hour=22, minute=0, second=0, microsecond=0)
                    session = Session.create(show_id=show.id, starts_at=starts_at,
                        venue="Teatro Comunitário — Sala Principal" if index % 2 == 0 else "Centro Cultural — Sala Experimental",
                        capacity=capacity, full_price_cents=price)
                    await SessionRepository(db).save(session)
                    existing.append(session)
                    created["sessions"] += 1
                await db.flush()
            sessions.append((show, existing[0]))

        buyer = accounts["comprador.demo@example.com"]
        reservations = ReservationRepository(db)
        for index, state in enumerate(("open", "cancelled", "expired")):
            show, session = sessions[index]
            key = uuid5(NAMESPACE_URL, f"ludens-local-demo:{session.id}:{state}")
            if await reservations.find_request(buyer.id, key) is not None:
                continue
            if state == "open":
                await ReservationUseCase(db).create(ReservationRequest(session_id=session.id,
                    idempotency_key=key, full_quantity=1, half_quantity=1), user_id=buyer.id, cpf=buyer.cpf)
            else:
                created_at = now - timedelta(minutes=30) if state == "expired" else now
                reservation = Reservation.create(now=created_at, user_id=buyer.id, buyer_cpf=buyer.cpf,
                    session_id=session.id, idempotency_key=key, full_quantity=1, half_quantity=0,
                    full_price_cents=session.full_price_cents, half_price_cents=session.half_price_cents,
                    show_title=show.title, starts_at=session.starts_at, venue=session.venue,
                    expires_at=created_at + timedelta(minutes=settings.reservation_ttl_minutes))
                if state == "expired":
                    reservation.expire(now)
                else:
                    reservation.cancel(now)
                await reservations.save(reservation)
                await db.flush()
            created["reservations"] += 1
    print(f"Dados fictícios criados: {created}")
    print("Dados existentes preservados. Reexecutar não duplica os exemplos.")


if __name__ == "__main__":
    asyncio.run(seed_demo())
