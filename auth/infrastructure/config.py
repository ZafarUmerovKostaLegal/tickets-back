import logging
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""

    auth_redirect_uri: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_REDIRECT_URI", "AZURE_REDIRECT_URI"),
    )
    jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices("JWT_SECRET", "jwt_secret"),
        description="Общий с gateway; в Portainer задайте JWT_SECRET (мин. 16 символов; для прода рекомендуется ≥32).",
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    auth_session_cookie_name: str = Field(default="kl_access_token", validation_alias=AliasChoices("AUTH_SESSION_COOKIE_NAME"))
    auth_set_session_cookie: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTH_SET_SESSION_COOKIE", "auth_set_session_cookie"),
        description="Выставлять Set-Cookie на OAuth callback и POST /auth/logout (нужен общий домен с фронтом или прокси).",
    )
    auth_session_cookie_secure: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTH_SESSION_COOKIE_SECURE"),
    )
    auth_session_cookie_samesite: str = Field(
        default="lax",
        validation_alias=AliasChoices("AUTH_SESSION_COOKIE_SAMESITE"),
        description="lax | strict | none (none требует secure=true)",
    )
    frontend_url: str = ""
    admin_frontend_url: str = ""
    admin_username: str = "admin"
    admin_password: str = ""


    admin_bootstrap_secret: str = Field(
        default="",
        validation_alias=AliasChoices("ADMIN_BOOTSTRAP_SECRET", "admin_bootstrap_secret"),
    )
    service_name: str = "auth"


_log = logging.getLogger("auth.config")


_MIN_JWT_SECRET_LEN = 16
_RECOMMENDED_JWT_SECRET_LEN = 32


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_production_secrets(settings: Settings) -> None:

    jwt_secret = (settings.jwt_secret or "").strip()
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET is empty. Set JWT_SECRET in the stack environment (Portainer → stack → Environment). "
            "Same value as gateway; generate: openssl rand -hex 32"
        )
    n = len(jwt_secret)
    if n < _MIN_JWT_SECRET_LEN:
        raise RuntimeError(
            f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LEN} characters long (current length: {n}). "
            "Set a longer secret in Portainer (e.g. openssl rand -hex 32), same value on gateway."
        )
    if n < _RECOMMENDED_JWT_SECRET_LEN:
        _log.warning(
            "JWT_SECRET length is %s; for production use at least %s random characters (e.g. openssl rand -hex 32).",
            n,
            _RECOMMENDED_JWT_SECRET_LEN,
        )
    if (settings.jwt_algorithm or "").strip() not in {"HS256", "HS384", "HS512"}:
        raise RuntimeError("JWT_ALGORITHM must be one of HS256, HS384 or HS512.")
