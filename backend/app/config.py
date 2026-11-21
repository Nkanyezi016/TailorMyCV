from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LODESTAR_", env_file=".env", extra="ignore")

    provider: Literal["local", "openai"] = "local"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    provider_timeout: float = Field(15, gt=0, le=60)
    provider_retries: int = Field(1, ge=0, le=3)
    max_workers: int = Field(2, ge=1, le=8)
    max_pending: int = Field(16, ge=1, le=128)
    api_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_provider(self):
        if self.provider == "openai" and not (
            self.openai_api_key and self.openai_api_key.get_secret_value()
        ):
            raise ValueError("Set LODESTAR_OPENAI_API_KEY before using the OpenAI provider.")
        return self


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
