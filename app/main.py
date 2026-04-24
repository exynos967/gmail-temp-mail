from __future__ import annotations

import logging
import random
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.aliasing import generate_random_gmail_alias
from app.auth import AddressTokenPayload, require_address_token, require_service_api_key
from app.config import GmailAccount, Settings
from app.db import AliasRecord, Database
from app.mail_sync import MailSyncService, NullMailSyncService


logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    mail_sync_service: MailSyncService | None = None,
    start_background_sync: bool = False,
) -> FastAPI:
    resolved_settings = settings or Settings()
    database = Database(resolved_settings.database_path)
    database.initialize()
    if mail_sync_service is not None:
        resolved_mail_sync_service = mail_sync_service
    elif start_background_sync:
        resolved_mail_sync_service = MailSyncService(
            settings=resolved_settings,
            database=database,
        )
    else:
        resolved_mail_sync_service = NullMailSyncService(database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background_sync:
            app.state.mail_sync.start()
        try:
            yield
        finally:
            if start_background_sync:
                app.state.mail_sync.stop()

    app = FastAPI(title='gmail-temp-mail', lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.mail_sync = resolved_mail_sync_service

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
        selected_account = select_random_gmail_account(current_settings)

        alias_record = _create_unique_alias(
            current_database,
            current_settings,
            selected_account.address,
            _get_current_uid_baseline(request, selected_account.address),
        )
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

    @app.get('/api/mails')
    def list_mails(
        request: Request,
        token_payload: AddressTokenPayload = Depends(require_address_token),
    ) -> dict[str, object]:
        limit = _parse_limit(request.query_params.get('limit'))
        offset = _parse_offset(request.query_params.get('offset'))
        current_database: Database = request.app.state.database
        results = current_database.list_mails(token_payload.address, limit, offset)
        count = current_database.count_mails(token_payload.address) if offset == 0 else 0
        return {'results': results, 'count': count}

    @app.get('/api/mail/{mail_id}')
    def get_mail(
        mail_id: int,
        request: Request,
        token_payload: AddressTokenPayload = Depends(require_address_token),
    ) -> dict[str, object] | None:
        current_database: Database = request.app.state.database
        return current_database.get_mail(mail_id, token_payload.address)

    @app.delete('/api/mails/{mail_id}')
    def delete_mail(
        mail_id: int,
        request: Request,
        token_payload: AddressTokenPayload = Depends(require_address_token),
    ) -> dict[str, bool]:
        current_database: Database = request.app.state.database
        return {'success': current_database.delete_mail(mail_id, token_payload.address)}

    return app


def _validate_alias_creation_settings(settings: Settings) -> None:
    try:
        if not settings.get_gmail_accounts():
            raise HTTPException(status_code=500, detail='Gmail address is not configured')
    except ValueError as exc:
        raise HTTPException(status_code=500, detail='Gmail address is invalid') from exc
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail='JWT secret is not configured')


def select_random_gmail_account(settings: Settings) -> GmailAccount:
    accounts = settings.get_gmail_accounts()
    if not accounts:
        raise HTTPException(status_code=500, detail='Gmail address is not configured')
    return random.SystemRandom().choice(accounts)


def _create_unique_alias(
    database: Database,
    settings: Settings,
    account_address: str,
    start_uid: int,
) -> AliasRecord:
    created_at = datetime.now(UTC)
    expires_at = created_at + timedelta(minutes=settings.alias_ttl_minutes)

    for _ in range(20):
        address = generate_random_gmail_alias(
            account_address,
            include_plus_tag=settings.gmail_alias_plus_tag_enabled,
        )
        try:
            return database.create_alias(
                address,
                account_address,
                created_at,
                expires_at,
                start_uid=start_uid,
            )
        except sqlite3.IntegrityError:
            continue

    raise HTTPException(status_code=500, detail='Failed to generate unique alias')


def _get_current_uid_baseline(request: Request, account_address: str) -> int:
    try:
        return request.app.state.mail_sync.get_current_uid_baseline(account_address)
    except Exception as exc:
        logger.exception('failed to read current Gmail UID baseline')
        raise HTTPException(
            status_code=503,
            detail='Failed to read Gmail UID baseline',
        ) from exc


def _parse_limit(value: str | None) -> int:
    if value is None:
        return 20
    try:
        limit = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid limit') from exc
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail='Invalid limit')
    return limit


def _parse_offset(value: str | None) -> int:
    if value is None:
        return 0
    try:
        offset = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid offset') from exc
    if offset < 0:
        raise HTTPException(status_code=400, detail='Invalid offset')
    return offset


app = create_app()
