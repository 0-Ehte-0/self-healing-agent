#This file contains custom error classes and exception handlers for the FastAPI application.

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, message: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR):
        self.message = message
        self.status_code = status_code
        super().__init__(message)

#this handler is used to catch AppError exceptions and return a structured JSON response with the error details and correlation ID.
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank",
            "title": "Application Error",
            "status": exc.status_code,
            "detail": exc.message,
            "instance": str(request.url),
            "correlation_id": correlation_id,
        },
    )
