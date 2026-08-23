from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import CorrelationIdMiddleware, setup_logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

settings = get_settings()
setup_logging(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup validation and initializations
    yield
    # Shutdown / teardown connections


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Exception handlers
app.add_exception_handler(AppError, app_error_handler)  # type: ignore

# Middlewares
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health_router, prefix="")

def start():
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8088, reload=True)

if __name__ == "__main__":
    start()
