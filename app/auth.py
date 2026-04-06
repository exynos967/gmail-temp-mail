from __future__ import annotations

from dataclasses import dataclass
from secrets import compare_digest

import jwt
from fastapi import HTTPException, Request, status


@dataclass(slots=True)
class AddressTokenPayload:
    address_id: int
    address: str


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



def require_address_token(request: Request) -> AddressTokenPayload:
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    token = header.removeprefix('Bearer ').strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    try:
        payload = jwt.decode(
            token,
            request.app.state.settings.jwt_secret,
            algorithms=['HS256'],
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized') from exc

    address = payload.get('address')
    address_id = payload.get('address_id')
    if not isinstance(address, str) or not isinstance(address_id, int):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    return AddressTokenPayload(address_id=address_id, address=address)
