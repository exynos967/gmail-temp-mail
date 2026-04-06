from __future__ import annotations

from secrets import compare_digest

from fastapi import HTTPException, Request, status


def require_service_api_key(request: Request) -> None:
    configured_key = request.app.state.settings.service_api_key
    provided_key = request.headers.get('x-custom-auth', '')

    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Service API key is not configured',
        )
    if not compare_digest(provided_key, configured_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
