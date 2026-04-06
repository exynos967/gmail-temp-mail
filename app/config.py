from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
