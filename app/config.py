from dataclasses import dataclass

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.aliasing import normalize_gmail_address


@dataclass(frozen=True, slots=True)
class GmailAccount:
    address: str
    app_password: str


class Settings(BaseSettings):
    gmail_address: str = ''
    gmail_app_password: str = ''
    gmail_accounts: str = ''
    service_api_key: str = ''
    jwt_secret: str = ''
    database_path: str = 'gmail_temp_mail.db'
    poll_interval_seconds: int = 15
    alias_ttl_minutes: int = 60
    mail_ttl_minutes: int = 1440

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('poll_interval_seconds', 'alias_ttl_minutes', 'mail_ttl_minutes')
    @classmethod
    def ensure_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError('must be > 0')
        return value

    def get_gmail_accounts(self) -> list[GmailAccount]:
        if self.gmail_accounts.strip():
            return self._parse_gmail_accounts(self.gmail_accounts)
        if not self.gmail_address or not self.gmail_app_password:
            return []
        return [
            GmailAccount(
                address=normalize_gmail_address(self.gmail_address),
                app_password=self._normalize_app_password(self.gmail_app_password),
            )
        ]

    def get_gmail_account(self, account_address: str) -> GmailAccount:
        normalized_address = normalize_gmail_address(account_address)
        for account in self.get_gmail_accounts():
            if account.address == normalized_address:
                return account
        raise ValueError('Gmail account is not configured')

    def _parse_gmail_accounts(self, raw_value: str) -> list[GmailAccount]:
        accounts: list[GmailAccount] = []
        seen_addresses: set[str] = set()
        for raw_item in raw_value.split(','):
            item = raw_item.strip()
            if not item:
                continue
            address, separator, app_password = item.partition(':')
            if not separator or not app_password.strip():
                raise ValueError('Invalid GMAIL_ACCOUNTS entry')
            normalized_address = normalize_gmail_address(address)
            if normalized_address in seen_addresses:
                continue
            accounts.append(
                GmailAccount(
                    address=normalized_address,
                    app_password=self._normalize_app_password(app_password),
                )
            )
            seen_addresses.add(normalized_address)
        if not accounts:
            raise ValueError('No valid Gmail accounts configured')
        return accounts

    def _normalize_app_password(self, value: str) -> str:
        return value.strip().replace(' ', '')
