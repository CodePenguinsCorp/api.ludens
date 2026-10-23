import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.config import settings
from app.core.shared import format_validation_errors
from app.outbox.relay import run as run_outbox_relay
from app.core.domain import AuthError, ConflictError, DomainError, ForbiddenError, GoneError, NotFoundError

from app.modules.catalog.router import router as catalog_router
from app.modules.identity.router import router as identity_router
from app.modules.booking.router import purchase_router as booking_purchase_router, router as booking_router
from app.modules.booking.expiry import run as run_reservation_expiry
from app.modules.notification import handlers as notification_handlers  # noqa: F401

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(run_outbox_relay()), asyncio.create_task(run_reservation_expiry())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(title="Ludens API", version="0.1.0", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": format_validation_errors(exc.errors())},
    )

_DOMAIN_ERROR_STATUS = [
    (ConflictError, 409),
    (AuthError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (GoneError, 410),
]

@app.exception_handler(DomainError)
async def domain_exception_handler(request: Request, exc: DomainError):
    status_code = next((s for t, s in _DOMAIN_ERROR_STATUS if isinstance(exc, t)), 422)
    return JSONResponse(status_code=status_code, content={"detail": exc.message})

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(identity_router, prefix="/api")
app.include_router(catalog_router, prefix="/api")
app.include_router(booking_router, prefix="/api")
app.include_router(booking_purchase_router, prefix="/api")

@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
