from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.aliasing import generate_random_gmail_alias, normalize_gmail_address
from app.auth import require_service_api_key
from app.config import Settings
from app.db import AliasRecord, Database


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    database = Database(resolved_settings.database_path)
    database.initialize()

    app = FastAPI(title='gmail-temp-mail')
    app.state.settings = resolved_settings
    app.state.database = database

    @app.get('/', response_class=PlainTextResponse)
    def root() -> str:
        return 'OK'

    @app.get('/health_check', response_class=PlainTextResponse)
    def health_check() -> str:
        return 'OK'

    @app.post('/api/new_address')
    def new_address(
        request: Request,
        _: None = Depends(require_service_api_key),
    ) -> dict[str, object]:
        current_settings: Settings = request.app.state.settings
        current_database: Database = request.app.state.database
        _validate_alias_creation_settings(current_settings)

        alias_record = _create_unique_alias(current_database, current_settings)
        token = jwt.encode(
            {
                'address_id': alias_record.id,
                'address': alias_record.address,
                'exp': int(alias_record.expires_at.timestamp()),
            },
            current_settings.jwt_secret,
            algorithm='HS256',
        )
        return {
            'address_id': alias_record.id,
            'address': alias_record.address,
            'jwt': token,
            'created_at': alias_record.created_at.isoformat(),
            'expires_at': alias_record.expires_at.isoformat(),
        }

    return app


def _validate_alias_creation_settings(settings: Settings) -> None:
    if not settings.gmail_address:
        raise HTTPException(status_code=500, detail='Gmail address is not configured')
    try:
        normalize_gmail_address(settings.gmail_address)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail='Gmail address is invalid') from exc
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail='JWT secret is not configured')


def _create_unique_alias(database: Database, settings: Settings) -> AliasRecord:
    created_at = datetime.now(UTC)
    expires_at = created_at + timedelta(minutes=settings.alias_ttl_minutes)

    for _ in range(20):
        address = generate_random_gmail_alias(settings.gmail_address)
        try:
            return database.create_alias(address, created_at, expires_at)
        except sqlite3.IntegrityError:
            continue

    raise HTTPException(status_code=500, detail='Failed to generate unique alias')


app = create_app()
