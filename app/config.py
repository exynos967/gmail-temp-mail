import os
import re
from dataclasses import dataclass
from pathlib import Path

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
        numbered_accounts = self._parse_numbered_gmail_accounts(
            self._load_environment_values()
        )
        if numbered_accounts:
            return numbered_accounts
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

    def _parse_numbered_gmail_accounts(
        self,
        raw_values: dict[str, str],
    ) -> list[GmailAccount]:
        address_pattern = re.compile(r'^GMAIL_ACCOUNTS_(\d+)$')
        password_pattern = re.compile(r'^GMAIL_APP_PASSWORD_(\d+)$')
        raw_addresses: dict[int, str] = {}
        raw_passwords: dict[int, str] = {}
        for key, value in raw_values.items():
            if match := address_pattern.fullmatch(key):
                raw_addresses[int(match.group(1))] = value
            elif match := password_pattern.fullmatch(key):
                raw_passwords[int(match.group(1))] = value

        accounts: list[GmailAccount] = []
        seen_addresses: set[str] = set()
        indices = sorted(set(raw_addresses) | set(raw_passwords))
        for index in indices:
            raw_address = raw_addresses.get(index, '').strip()
            raw_password = raw_passwords.get(index, '').strip()
            if not raw_address and not raw_password:
                continue
            if not raw_address or not raw_password:
                raise ValueError(f'Incomplete Gmail account configuration for index {index}')

            normalized_address = normalize_gmail_address(raw_address)
            if normalized_address in seen_addresses:
                continue
            accounts.append(
                GmailAccount(
                    address=normalized_address,
                    app_password=self._normalize_app_password(raw_password),
                )
            )
            seen_addresses.add(normalized_address)
        return accounts

    def _normalize_app_password(self, value: str) -> str:
        return value.strip().replace(' ', '')

    def _load_environment_values(self) -> dict[str, str]:
        values = self._load_dotenv_values()
        values.update(os.environ)
        return values

    def _load_dotenv_values(self) -> dict[str, str]:
        raw_env_files = self.model_config.get('env_file')
        if raw_env_files is None:
            return {}

        if isinstance(raw_env_files, (str, Path)):
            env_files = [Path(raw_env_files)]
        else:
            env_files = [Path(item) for item in raw_env_files]

        values: dict[str, str] = {}
        for env_file in env_files:
            if not env_file.exists():
                continue
            for line in env_file.read_text(encoding='utf-8').splitlines():
                stripped_line = line.strip()
                if not stripped_line or stripped_line.startswith('#') or '=' not in stripped_line:
                    continue
                key, raw_value = stripped_line.split('=', 1)
                values[key.strip()] = self._strip_env_value(raw_value)
        return values

    def _strip_env_value(self, raw_value: str) -> str:
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value
